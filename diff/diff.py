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
import subprocess
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
SURVEY_THINKING = "high"
RECHECK_THINKING = "high"
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
- "same_place": visible roughly where it was (small shifts, a turned chair or a book slid along a shelf all count as same place).
- "moved": clearly visible, but somewhere else in the room.
- "not_seen": you can't find it anywhere in the AFTER video.
Sets of matching items (dining chairs, a row of identical lamps) are judged by count in the area: if one of a set
is gone, mark one of them not_seen; if one turns up elsewhere, mark one of them moved. Give the after timestamps where you saw each item.

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
    result = {e.id: e for e in argon.ask(contents, Survey, system=SYSTEM, thinking=SURVEY_THINKING).items}
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
        out.update({r.id: r for r in argon.ask(contents, RecheckList, system=SYSTEM, thinking=RECHECK_THINKING).items})
    return out


class Confirmation(BaseModel):
    id: str
    proves_removal: bool
    reason: str = Field(description="one short sentence a person can read")


class ConfirmationList(BaseModel):
    items: list[Confirmation]


CONFIRM_PROMPT = """\
Each image below is the evidence a theft claim will carry for one item: BEFORE frame on the left, AFTER frame of
the same spot on the right. Judge each image on its own. It proves removal only if the item is clearly visible on
the left AND the same spot is clearly visible and empty on the right. If the item could be hidden behind something,
out of frame, or lost in blur on the right, it does not prove removal. An adjuster will see only this image.
"""


def confirm(scene: Scene, images: dict[str, tuple[Path, float]]) -> dict[str, Confirmation]:
    emit("diff", f"Checking that each of {len(images)} missing items is proven gone by its own BEFORE | AFTER image")
    contents: list = [CONFIRM_PROMPT]
    for item_id, (img, _) in images.items():
        contents += [f"{item_id}: {scene.item(item_id).label}", argon.image_part(img)]
    return {c.id: c for c in argon.ask(contents, ConfirmationList, system=SYSTEM, thinking="high").items}


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
            emit("diff", f"Re-check round {rnd}: missing list unchanged, done", "ok")
            break
        previous = stolen_now
    stolen_ids = previous or set()

    # The claim has to stand on its own evidence: build the BEFORE | AFTER image it will carry and have Argon
    # confirm on that image alone. Anything it can't confirm stays "present".
    images: dict[str, tuple[Path, float]] = {}
    for it in scene.items:
        if it.id not in stolen_ids:
            continue
        r = evidence[it.id]
        after_t = r.spot_t if r.spot_t is not None else 0.0
        img = evidence_image(frames.extract(before_video, it.seen_at[0], frame_dir_before),
                             frames.extract(after_video, after_t, frame_dir_after),
                             it.label, it.seen_at[0], after_t, out_dir / f"stolen_{it.id}.jpg")
        images[it.id] = (img, after_t)
    confirmed = confirm(scene, images) if images else {}

    scene = scene.model_copy(deep=True)
    stolen = []
    for it in scene.items:
        it.status = status[it.id]
        if it.id not in images:
            continue
        img, after_t = images[it.id]
        c = confirmed.get(it.id)
        if c is None or not c.proves_removal:
            reason = c.reason if c else "no verdict"
            emit("diff", f"Can't prove {it.label} is gone from its evidence image ({reason}); keeping it as present",
                 "info", id=it.id, evidence=os.path.relpath(img, REPO))
            img.unlink(missing_ok=True)
            continue
        it.status = "stolen"
        price = it.price.model_dump() if it.price else None
        stolen.append({"id": it.id, "label": it.label, "price": price, "before_t": it.seen_at[0],
                       "after_t": after_t, "evidence": os.path.relpath(img, REPO), "reason": c.reason})
        emit("diff", f"Missing: {it.label}, seen @{_fmt(it.seen_at[0])} before, spot empty @{_fmt(after_t)} after",
             "warn", id=it.id, evidence=os.path.relpath(img, REPO), price=price)

    moved = sum(1 for i in scene.items if i.status == "moved")
    emit("diff", f"After walkthrough: {len(stolen)} missing, {moved} moved, "
         f"{len(scene.items) - len(stolen) - moved} in place", "ok")
    return scene, stolen


def reprice(out_dir: Path, scene_path: Path) -> int:
    """Copy current prices from the baseline into an existing diff result, by item id. Verdicts are untouched."""
    prices = {i["id"]: i.get("price") for i in json.loads(scene_path.read_text())["items"]}
    diff_scene = json.loads((out_dir / "scene.json").read_text())
    for it in diff_scene["items"]:
        if prices.get(it["id"]):
            it["price"] = prices[it["id"]]
    (out_dir / "scene.json").write_text(json.dumps(diff_scene, indent=2))
    stolen = json.loads((out_dir / "stolen.json").read_text())
    for s in stolen:
        s["price"] = prices.get(s["id"]) or s["price"]
    (out_dir / "stolen.json").write_text(json.dumps(stolen, indent=2))
    return sum(1 for s in stolen if s["price"])


def normalize(video: Path, out_dir: Path) -> Path:
    """Phone uploads are often HEVC .MOV at 1080p or more. Re-encode to 720p H.264 MP4 unless it already is one."""
    if video.suffix.lower() == ".mp4" and frames.size(video)[1] <= 720:
        return video
    out = out_dir / "after.mp4"
    emit("diff", f"Converting {video.name} to 720p for upload")
    base = ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", "scale=-2:720", "-an"]
    try:  # the Mac's hardware encoder is several times faster than x264
        subprocess.run(base + ["-c:v", "h264_videotoolbox", "-b:v", "2500k", str(out)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(base + ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast", str(out)], check=True)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--after", help="after-walkthrough video (required unless --reprice)")
    p.add_argument("--before-video", default=str(REPO / "fixtures" / "before.mp4"))
    p.add_argument("--scene", default=str(REPO / "out" / "scene.json"), help="baseline scene")
    p.add_argument("--out", default=str(REPO / "out" / "diff"))
    p.add_argument("--reprice", action="store_true", help="only refresh prices in an existing diff from --scene")
    p.add_argument("--fast", action="store_true", help="live-demo speed: medium thinking for survey and re-check, one "
                                                       "re-check round (the confirm step still runs at high)")
    p.add_argument("--events", help="events file to append to (default: <out>/events.jsonl); point it at the file "
                                    "the viewer tails to show the diff live")
    args = p.parse_args()

    out_dir = Path(args.out)
    if args.fast:
        global SURVEY_THINKING, RECHECK_THINKING, MAX_ROUNDS
        SURVEY_THINKING, RECHECK_THINKING, MAX_ROUNDS = "medium", "medium", 1
    if args.reprice:
        print(f"{reprice(out_dir, Path(args.scene))} stolen items priced")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    events.set_path(Path(args.events) if args.events else out_dir / "events.jsonl")
    if not args.after:
        p.error("--after is required")
    scene = Scene.model_validate_json(Path(args.scene).read_text())
    after = normalize(Path(args.after), out_dir)
    result, stolen = run(scene, after, Path(args.before_video), out_dir)
    (out_dir / "scene.json").write_text(json.dumps(result.dump(), indent=2))
    (out_dir / "stolen.json").write_text(json.dumps(stolen, indent=2))
    moved = sum(1 for i in result.items if i.status == "moved")
    # last stdout line is a one-line JSON summary for whatever launched us (e.g. the viewer's upload handler)
    print(json.dumps({"stolen": [{"id": s["id"], "label": s["label"]} for s in stolen], "moved": moved,
                      "scene": os.path.relpath(out_dir / "scene.json", REPO),
                      "stolen_list": os.path.relpath(out_dir / "stolen.json", REPO)}))


if __name__ == "__main__":
    main()
