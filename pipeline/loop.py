"""Loop 1: render the scene from each keyframe, compare with the real frame, fix, repeat."""

import json
import math
import os
import re
import time
from pathlib import Path

import argon
import passes
from events import emit
from fidelity import _tokens
from models import Critique, CritiqueList, Item, Keyframe, Scene, Size
from reconstruct import CONVENTIONS
from render_pairs import render_pairs

MAX_ITERATIONS = 4
THINKING = "high"
REPO = Path(__file__).resolve().parent.parent
RUN_TAG = time.strftime("%H%M%S")

SYSTEM = f"""\
You check a 3D room reconstruction against the real video, one viewpoint at a time.
Each image you get is a pair: REAL frame on the left, RENDER of the current scene on the right, from the
estimated camera of the same keyframe. Items in the render are colored boxes labeled with their id.

{CONVENTIONS}"""

PROMPT = """\
Here is the current scene (JSON) and one REAL | RENDER pair per keyframe.

Work through every pair as an itemized accounting (in your thinking, not the output):
1. List every belonging visible in the REAL frame, including small ones on tables and counters (electronics,
   card readers, speakers, bags, books, decor) and items partly hidden behind others.
2. For each, find its box in the RENDER, or in the scene JSON if the render's viewpoint is off.
3. Anything with no box anywhere in the scene is missing. A box whose object you can't find in any REAL frame is
   suspect.
The single pass that built this scene usually misses a few small or partly hidden items, so look hard before
concluding nothing is missing.

Then report the real, material errors as critiques:
- fix "add": a belonging clearly visible in REAL with no box in RENDER. Give `label`, `anchor`, `size`, target "new".
- fix "remove": a box in RENDER for something that is not in REAL at all. Target its id.
- fix "move" / "resize": a box clearly in the wrong place (wrong wall, wrong side of another item, off by more than
  ~0.5 m) or badly wrong size (any dimension off by more than ~50%; a thin pole or stand drawn as a thick block
  counts). Give the corrected `anchor` / `size`. Geometry errors matter as much as missing items.
- fix "relabel": the box is the right object but the label is wrong in kind or color. Give the new `label`.
- Bundled entries: a label that names several separate objects ("bike with helmet on handlebar", "book with
  flower arrangement and vases") hides belongings that each need their own entry. Relabel the box to the main
  object, and "add" each other object as its own item at its own spot.
- fix "camera": the whole RENDER is clearly from the wrong viewpoint (most items shifted the same way).
  Give corrected `position` and `look_at`. When the camera is off, fix it instead of moving every item.

Rules:
- Only add belongings an insurer would pay to replace. Not consumables or supplies (snacks, cardboard boxes,
  pens, paper, cards, cups), not trash, not people or their clothing.
- Built-ins are deliberately NOT modeled: counters, built-in cabinets and shelving, radiators, outlets, fixed
  lighting. Never add them, even though they show in REAL and not in RENDER.
- Before calling an item missing, check other keyframes and the scene JSON: it may already exist, seen from elsewhere.
- Ignore small position errors, lighting, texture, and things outside the frame.
- Each problem once, even if visible in several pairs. Use the keyframe index where it's clearest.
- `problem` is one short sentence a person watching can follow.
- If the scene matches the video well, return an empty list. Don't invent critiques to have something to say.

Scene:
"""


def _scene_for_prompt(scene: Scene) -> str:
    slim = {
        "rooms": [r.model_dump(by_alias=True) for r in scene.rooms],
        "keyframes": [{"index": i, **k.model_dump(exclude_none=True)} for i, k in enumerate(scene.keyframes)],
        "items": [i.model_dump(include={"id", "label", "anchor", "size"}) for i in scene.items],
    }
    return json.dumps(slim, indent=1)


DUPLICATE_PAD = 1.2
DUPLICATE_MIN_DIST = 0.25
DUPLICATE_SIZE_RATIO = 2.5


# Color words grouped into families, so "brown" and "bronze" can still be the same object but "white" and
# "brown" can't.
COLOR_FAMILY = {
    "white": "white", "cream": "white", "ivory": "white", "beige": "white", "off": "white",
    "black": "black", "charcoal": "black",
    "gray": "gray", "grey": "gray", "silver": "gray", "space": "gray",
    "brown": "brown", "bronze": "brown", "copper": "brown", "tan": "brown", "walnut": "brown",
    "gold": "gold", "golden": "gold", "brass": "gold", "yellow": "gold",
    "red": "red", "pink": "red", "orange": "orange", "green": "green", "blue": "blue", "navy": "blue",
    "purple": "purple",
}


def _colors(label: str) -> set[str]:
    return {COLOR_FAMILY[w] for w in re.findall(r"[a-z]+", label.lower()) if w in COLOR_FAMILY}


def find_duplicate(scene: Scene, label: str, anchor, size: Size | None = None) -> Item | None:
    """An existing item that is probably the same object: similar label, similar size, same color if both name
    one, and the new center falls inside its (slightly padded) box or very close to it. Identical items side by
    side don't overlap, a small vase split out of a "flowers in a vase" entry is much smaller than it, and a
    white vase next to a brown one names different colors, so all of those are kept."""
    words, colors = _tokens(label), _colors(label)
    for item in scene.items:
        other_colors = _colors(item.label)
        if colors and other_colors and not colors & other_colors:
            continue
        if size is not None:
            big, small = sorted([max(size.w, size.h, size.d), max(item.size.w, item.size.h, item.size.d)], reverse=True)
            if small <= 0 or big / small > DUPLICATE_SIZE_RATIO:
                continue
        other = _tokens(item.label)
        # "snack basket" vs "snack bar sign" share a word but aren't the same kind of thing
        if not words or not other or len(words & other) / min(len(words), len(other)) <= 0.5:
            continue
        a, s = item.anchor, item.size
        inside = (abs(anchor.x - a.x) <= s.w * DUPLICATE_PAD / 2 and abs(anchor.y - a.y) <= s.h * DUPLICATE_PAD / 2
                  and abs(anchor.z - a.z) <= s.d * DUPLICATE_PAD / 2)
        # "very close" scales with the object: 25 cm for furniture, a few cm for small things on a table
        near = min(DUPLICATE_MIN_DIST, 0.5 * min(max(s.w, s.h, s.d), max(size.w, size.h, size.d) if size else 9e9))
        if inside or math.dist((a.x, a.y, a.z), (anchor.x, anchor.y, anchor.z)) < near:
            return item
    return None


def _apply(scene: Scene, c: Critique) -> str | None:
    """Apply one critique in place. Returns a human-readable description, or None if it couldn't apply."""
    if c.fix == "add":
        if not (c.label and c.anchor and c.size):
            return None
        kf_t = scene.keyframes[c.keyframe].t if 0 <= c.keyframe < len(scene.keyframes) else 0.0
        item = Item(id=scene.next_id(), label=c.label, anchor=c.anchor, size=c.size, seen_at=[kf_t], confidence=0.7)
        scene.items.append(item)
        return f"added {item.id} {item.label}"
    if c.fix == "camera":
        if not (0 <= c.keyframe < len(scene.keyframes) and c.position and c.look_at):
            return None
        kf = scene.keyframes[c.keyframe]
        scene.keyframes[c.keyframe] = Keyframe(t=kf.t, position=c.position, look_at=c.look_at, fov=kf.fov)
        return f"re-estimated camera for keyframe {c.keyframe} (t={kf.t:.1f}s)"
    item = scene.item(c.target)
    if item is None:
        return None
    if c.fix == "remove":
        scene.items.remove(item)
        return f"removed {item.id} {item.label}"
    if c.fix == "move" and c.anchor:
        item.anchor = c.anchor
        return f"moved {item.id} {item.label}"
    if c.fix == "resize" and c.size:
        item.size = Size(w=max(c.size.w, 0.02), h=max(c.size.h, 0.02), d=max(c.size.d, 0.02))
        return f"resized {item.id} {item.label}"
    if c.fix == "relabel" and c.label:
        old = item.label
        item.label = c.label
        return f"relabeled {item.id}: {old} → {c.label}"
    return None


def _critique_local(scene: Scene, video: str | Path, pair_dir: Path, it: int) -> tuple[list[Critique], list[Path]]:
    emit("loop", f"Loop iter {it}: rendering scene from {len(scene.keyframes)} keyframe viewpoints")
    pairs = render_pairs(scene, video, pair_dir, f"iter{it}_")
    contents: list = [PROMPT + _scene_for_prompt(scene)]
    for k, path in enumerate(pairs):
        contents += [f"Keyframe {k} (t={scene.keyframes[k].t:.1f}s):", argon.image_part(path)]
    emit("loop", f"Loop iter {it}: Argon comparing {len(pairs)} rendered views with the real frames")
    return argon.ask(contents, CritiqueList, system=SYSTEM, thinking=THINKING).critiques, pairs


SANDBOX_TASK = """\
You are inside a Linux sandbox. Our files are in `pol/` under your starting directory (find it with `ls` if needed).

Step 1. Setup (skip what's already done): `pip install --break-system-packages -q pillow numpy pydantic`.
Step 2. Render: `cd pol && python3 render_pairs.py scene.json video.mp4 pairs {tag}_`
        This writes pairs/{tag}_kf<k>.jpg: REAL frame on the left, RENDER of the current scene on the right.
Step 3. Open and look at EVERY pair image pol/pairs/{tag}_kf*.jpg yourself. Only if something is genuinely
        unclear, extract an extra frame from pol/video.mp4 with ffmpeg: at most 4 extra frames in total. Be quick;
        this runs while people watch.
Step 4. Write your critiques to pol/critiques_{tag}.json as {{"critiques": [...]}} matching this JSON schema:
{schema}
Step 5. Reply with just the number of critiques.

Background:
{system}

What to look for:
{prompt}"""


def _critique_sandbox(sb, scene: Scene, video: str | Path, pair_dir: Path, it: int) -> tuple[list[Critique], list[Path]]:
    sb.upload_text(json.dumps(scene.dump()), "pol/scene.json")
    emit("loop", f"Loop iter {it}: Argon in the sandbox is rendering {len(scene.keyframes)} viewpoints and checking them against the video")
    tag = f"{RUN_TAG}_iter{it}"  # unique per run: the environment can be reused across runs
    task = SANDBOX_TASK.format(tag=tag, schema=json.dumps(CritiqueList.model_json_schema()), system=SYSTEM,
                               prompt=PROMPT + _scene_for_prompt(scene))
    sb.run(task, f"Loop iter {it}")
    critiques = CritiqueList.model_validate_json(sb.download(f"pol/critiques_{tag}.json")).critiques
    pairs = []
    for k in range(len(scene.keyframes)):
        path = pair_dir / f"iter{it}_kf{k}.jpg"
        try:
            path.write_bytes(sb.download(f"pol/pairs/{tag}_kf{k}.jpg"))
        except Exception:  # the viewer still needs the image; render the same pair locally
            render_pairs(scene.model_copy(update={"keyframes": [scene.keyframes[k]]}), video, pair_dir, f"iter{it}_tmp_")
            (pair_dir / f"iter{it}_tmp_kf0.jpg").rename(path)
        pairs.append(path)
    return critiques, pairs


def run(scene: Scene, video: str | Path, out_dir: str | Path, max_iterations: int = MAX_ITERATIONS,
        sandbox=None, on_iteration=None) -> tuple[Scene, int]:
    """on_iteration(scene), if given, runs after each iteration's fixes, so a viewer can watch the scene change."""
    out_dir = Path(out_dir)
    pair_dir = out_dir / "loop"
    pair_dir.mkdir(parents=True, exist_ok=True)

    scene = scene.model_copy(deep=True)
    iterations = 0
    for it in range(1, max_iterations + 1):
        iterations = it
        if sandbox is not None:
            critiques, pairs = _critique_sandbox(sandbox, scene, video, pair_dir, it)
        else:
            critiques, pairs = _critique_local(scene, video, pair_dir, it)

        if not critiques:
            emit("loop", f"Loop iter {it}: rendered views match the video, no critiques", "ok")
            break

        # A removal deletes a belonging from the claim, so check the item's own evidence frames first.
        to_remove = [scene.item(c.target) for c in critiques if c.fix == "remove" and scene.item(c.target)]
        still_visible = {}
        if to_remove:
            verdicts = passes.verify(to_remove, video, out_dir / "frames")
            still_visible = {i: v for i, v in verdicts.items() if v.visible}

        applied = 0
        # adds last: unbundling relabels "bike with helmet" to "bike" first, so the helmet isn't seen as a duplicate
        for c in sorted(critiques, key=lambda c: c.fix == "add"):
            t = scene.keyframes[c.keyframe].t if 0 <= c.keyframe < len(scene.keyframes) else None
            where = f" @{int(t // 60)}:{t % 60:04.1f}" if t is not None else ""
            # repo-relative, so the viewer can serve it (e.g. out/loop/iter1_kf3.jpg)
            pair = os.path.relpath(pairs[c.keyframe], REPO) if 0 <= c.keyframe < len(pairs) else None
            emit("loop", f"Loop iter {it}: {c.problem}{where}", "warn",
                 fix=c.fix, target=c.target, keyframe=c.keyframe, pair=pair)
            if c.fix == "add" and c.anchor and c.label and (dup := find_duplicate(scene, c.label, c.anchor, c.size)):
                emit("loop", f"Loop iter {it}: already have {dup.id} {dup.label} there, not adding a duplicate", "info",
                     duplicate_of=dup.id)
                continue
            if c.fix == "remove" and c.target in still_visible:
                item = scene.item(c.target)
                emit("loop", f"Loop iter {it}: kept {item.id} {item.label}: its own evidence frames still show it", "info",
                     reason=still_visible[c.target].reason)
                continue
            result = _apply(scene, c)
            if result:
                applied += 1
                emit("loop", f"Loop iter {it}: fix → {result}", "ok", fix=c.fix, target=c.target)
            else:
                emit("loop", f"Loop iter {it}: couldn't apply {c.fix} on {c.target}, skipped", "info")
        if on_iteration is not None and applied:
            on_iteration(scene)
            emit("loop", f"Loop iter {it}: scene updated, {len(scene.items)} items", "ok")
        if applied == 0:
            emit("loop", f"Loop iter {it}: no applicable fixes, stopping", "info")
            break

    return scene, iterations
