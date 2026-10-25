"""Part 2: compare an "after" walkthrough with the baseline scene and decide, per item, present / moved / stolen.

    cd pipeline
    uv run python ../diff/diff.py --after ../fixtures/after.mp4 [--before-video ../fixtures/before.mp4] [--scene ../out/scene.json]

1. Survey: Argon watches the after video with the baseline inventory (each item's id, label, position and its
   before-frame) and says, per item, whether it's in the same place, somewhere else, or not seen.
2. Re-check: every "not seen" item gets a focused second look. Was the spot where it stood clearly visible in the
   after video, and is the item anywhere? Visible spot + item nowhere = stolen candidate. Spot never clearly in
   view = unverified, which stays "present": we never claim what we can't show.
3. Rounds of re-checks repeat until the stolen set is the same twice in a row.

Writes out/diff/scene.json (baseline + items[].status), out/diff/stolen.json and a BEFORE | AFTER evidence image per
stolen item. Every step goes to out/diff/events.jsonl as `diff` events.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))

import argon  # noqa: E402
import events  # noqa: E402
import frames  # noqa: E402
from events import emit  # noqa: E402
from models import Item, Scene  # noqa: E402
from render import _font  # noqa: E402

MAX_ROUNDS = 3
RECHECK_BATCH = 6


class SurveyEntry(BaseModel):
    id: str
    where: Literal["same_place", "moved", "not_seen"]
    after_t: list[float] = Field(description="after-video timestamps (seconds) where the item is visible; empty if not seen")
    note: str = Field(description="one short sentence a person can read")


class Survey(BaseModel):
    items: list[SurveyEntry]


class Recheck(BaseModel):
    id: str
    found: bool = Field(description="the item itself is visible somewhere in the after video")
    found_t: float | None = Field(default=None, description="timestamp where it's visible, if found")
    spot_visible: bool = Field(description="the place where it stood before is clearly in view at some point")
    spot_t: float | None = Field(default=None, description="timestamp where that place is clearly in view")
    reason: str = Field(description="one short sentence a person can read")


class RecheckList(BaseModel):
    items: list[Recheck]


SYSTEM = """\
You compare two phone walkthroughs of the same room for a home-insurance theft claim: a BEFORE walkthrough
(represented here by one frame per item) and an AFTER walkthrough (the video). Calling an item stolen when it's still
there makes the claim fraudulent; missing a stolen item underpays it. Moved furniture is normal and is not theft."""

SURVEY_PROMPT = """\
Here is the AFTER walkthrough video, then the inventory from BEFORE: each item's id, label, rough position
(meters), and the BEFORE frame it was seen in.

For EVERY inventory item, say where it is in the AFTER video:
- "same_place": visible roughly where it was (small shifts, a turned chair or a slid laptop all count as same place).
- "moved": clearly visible, but somewhere else in the room.
- "not_seen": you can't find it anywhere in the AFTER video.
Identical items (six matching stools) are judged by count in the area: if one of six is gone, mark one of them
not_seen. Give the after timestamps where you saw each item.

Inventory:
"""

RECHECK_PROMPT = """\
These items weren't found in a first pass over the AFTER video. Look again, carefully, for each one. For each item
you get its label, where it stood, and its BEFORE frame.

1. Is the item itself anywhere in the AFTER video, maybe moved, partly hidden, or seen from a new angle? (found)
2. Is the exact spot where it stood clearly in view at some point in the AFTER video, close enough that you would
   see the item if it were there? (spot_visible, with the timestamp)
Be strict about 2: a spot glimpsed far away, blurred, or blocked by a person or furniture is not clearly in view.
"""


def _fmt(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _inventory_contents(items: list[Item], before_video: Path, frame_dir: Path) -> list:
    contents: list = []
    for it in items:
        a = it.anchor
        contents.append(f"{it.id}: {it.label}, at x={a.x:.1f} y={a.y:.1f} z={a.z:.1f}. BEFORE frame:")
        contents.append(argon.image_part(frames.extract(before_video, it.seen_at[0], frame_dir)))
    return contents


def survey(scene: Scene, after_file, before_video: Path, frame_dir: Path) -> dict[str, SurveyEntry]:
    emit("diff", f"Argon is watching the after walkthrough and looking for all {len(scene.items)} items")
    contents = [argon.video_part(after_file), SURVEY_PROMPT] + _inventory_contents(scene.items, before_video, frame_dir)
    result = {e.id: e for e in argon.ask(contents, Survey, system=SYSTEM, thinking="high").items}
    for it in scene.items:
        e = result.get(it.id)
        if e is None:
            continue
        where = f" @{_fmt(e.after_t[0])}" if e.after_t else ""
        if e.where == "same_place":
            emit("diff", f"Found {it.label} in place{where}", "ok", id=it.id, after_t=e.after_t)
        elif e.where == "moved":
            emit("diff", f"Found {it.label} moved{where}: {e.note}", "info", id=it.id, after_t=e.after_t)
        else:
            emit("diff", f"Can't find {it.label} in the after walkthrough", "warn", id=it.id)
    return result


def recheck(items: list[Item], after_file, before_video: Path, frame_dir: Path, rnd: int) -> dict[str, Recheck]:
    out: dict[str, Recheck] = {}
    for b in range(0, len(items), RECHECK_BATCH):
        batch = items[b:b + RECHECK_BATCH]
        emit("diff", f"Re-check round {rnd}: looking again for " + ", ".join(i.label for i in batch))
        contents = [argon.video_part(after_file), RECHECK_PROMPT] + _inventory_contents(batch, before_video, frame_dir)
        out.update({r.id: r for r in argon.ask(contents, RecheckList, system=SYSTEM, thinking="high").items})
    return out


def evidence_image(before: Path, after: Path, label: str, before_t: float, after_t: float, out: Path) -> Path:
    """BEFORE | AFTER composite for the claim: the item at before_t, its empty spot at after_t."""
    b, a = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
    a = a.resize((int(a.width * b.height / a.height), b.height))
    bar = 36
    img = Image.new("RGB", (b.width + a.width, b.height + bar), (20, 20, 20))
    img.paste(b, (0, bar))
    img.paste(a, (b.width, bar))
    d = ImageDraw.Draw(img)
    font = _font(22)
    d.text((10, 6), f"BEFORE @{_fmt(before_t)}: {label}", fill=(255, 255, 255), font=font)
    d.text((b.width + 10, 6), f"AFTER @{_fmt(after_t)}: spot is empty", fill=(255, 120, 120), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=88)
    return out


def run(scene: Scene, after_video: Path, before_video: Path, out_dir: Path) -> tuple[Scene, list[dict]]:
    frame_dir_before, frame_dir_after = out_dir / "frames_before", out_dir / "frames_after"
    emit("diff", f"Uploading after walkthrough ({after_video.name})")
    after_file = argon.upload_video(after_video)

    found = survey(scene, after_file, before_video, frame_dir_before)
    status: dict[str, str] = {}
    for it in scene.items:
        e = found.get(it.id)
        status[it.id] = "moved" if e and e.where == "moved" else "present"

    pending = [it for it in scene.items if found.get(it.id) is None or found[it.id].where == "not_seen"]
    evidence: dict[str, Recheck] = {}
    previous: set[str] | None = None
    for rnd in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        checks = recheck(pending, after_file, before_video, frame_dir_before, rnd)
        stolen_now = set()
        for it in pending:
            r = checks.get(it.id)
            if r is None:
                continue
            if r.found:
                status[it.id] = "moved"
                emit("diff", f"Re-check round {rnd}: found {it.label}" + (f" @{_fmt(r.found_t)}" if r.found_t else "")
                     + f": {r.reason}", "info", id=it.id)
            elif r.spot_visible:
                stolen_now.add(it.id)
                evidence[it.id] = r
            else:
                emit("diff", f"Re-check round {rnd}: {it.label}'s spot is never clearly in view, so it can't be "
                     f"claimed; keeping it as present", "info", id=it.id, reason=r.reason)
        # anything found or unverifiable drops out; only stolen candidates get looked at again
        pending = [it for it in pending if it.id in stolen_now]
        if previous is not None and stolen_now == previous:
            emit("diff", f"Re-check round {rnd}: stolen list unchanged, done", "ok")
            break
        previous = stolen_now
    stolen_ids = previous or set()

    scene = scene.model_copy(deep=True)
    stolen = []
    for it in scene.items:
        it.status = "stolen" if it.id in stolen_ids else status[it.id]
        if it.status != "stolen":
            continue
        r = evidence[it.id]
        after_t = r.spot_t if r.spot_t is not None else 0.0
        img = evidence_image(frames.extract(before_video, it.seen_at[0], frame_dir_before),
                             frames.extract(after_video, after_t, frame_dir_after),
                             it.label, it.seen_at[0], after_t, out_dir / f"stolen_{it.id}.jpg")
        price = it.price.model_dump() if it.price else None
        stolen.append({"id": it.id, "label": it.label, "price": price, "before_t": it.seen_at[0],
                       "after_t": after_t, "evidence": os.path.relpath(img, REPO), "reason": r.reason})
        emit("diff", f"Stolen: {it.label}, seen @{_fmt(it.seen_at[0])} before, spot empty @{_fmt(after_t)} after",
             "warn", id=it.id, evidence=os.path.relpath(img, REPO), price=price)

    moved = sum(1 for i in scene.items if i.status == "moved")
    emit("diff", f"After walkthrough: {len(stolen)} stolen, {moved} moved, "
         f"{len(scene.items) - len(stolen) - moved} in place", "ok")
    return scene, stolen


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--after", required=True, help="after-walkthrough video")
    p.add_argument("--before-video", default=str(REPO / "fixtures" / "before.mp4"))
    p.add_argument("--scene", default=str(REPO / "out" / "scene.json"), help="baseline scene")
    p.add_argument("--out", default=str(REPO / "out" / "diff"))
    args = p.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    events.set_path(out_dir / "events.jsonl")
    scene = Scene.model_validate_json(Path(args.scene).read_text())
    result, stolen = run(scene, Path(args.after), Path(args.before_video), out_dir)
    (out_dir / "scene.json").write_text(json.dumps(result.dump(), indent=2))
    (out_dir / "stolen.json").write_text(json.dumps(stolen, indent=2))
    print(json.dumps([{"id": s["id"], "label": s["label"]} for s in stolen], indent=2))


if __name__ == "__main__":
    main()
