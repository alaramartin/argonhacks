"""Loop 1: render the scene from each keyframe, compare with the real frame, fix, repeat."""

import json
from pathlib import Path

import argon
import frames
from events import emit
from models import Critique, CritiqueList, Item, Keyframe, Scene, Size
from reconstruct import CONVENTIONS
from render import render, side_by_side

MAX_ITERATIONS = 4

SYSTEM = f"""\
You check a 3D room reconstruction against the real video, one viewpoint at a time.
Each image you get is a pair: REAL frame on the left, RENDER of the current scene on the right, from the
estimated camera of the same keyframe. Items in the render are colored boxes labeled with their id.

{CONVENTIONS}"""

PROMPT = """\
Here is the current scene (JSON) and one REAL | RENDER pair per keyframe.

Report only real, material errors in the inventory, as critiques:
- fix "add": a belonging clearly visible in REAL with no box in RENDER. Give `label`, `anchor`, `size`, target "new".
- fix "remove": a box in RENDER for something that is not in REAL at all. Target its id.
- fix "move" / "resize": a box clearly in the wrong place (wrong wall, wrong side of another item) or badly wrong
  size (off by more than ~50%). Give the corrected `anchor` / `size`.
- fix "relabel": the box is the right object but the label is wrong in kind or color. Give the new `label`.
- fix "camera": the whole RENDER is clearly from the wrong viewpoint (most items shifted the same way).
  Give corrected `position` and `look_at`. When the camera is off, fix it instead of moving every item.

Rules:
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


def run(scene: Scene, video: str | Path, out_dir: str | Path, max_iterations: int = MAX_ITERATIONS) -> tuple[Scene, int]:
    out_dir = Path(out_dir)
    frame_dir, pair_dir = out_dir / "frames", out_dir / "loop"
    pair_dir.mkdir(parents=True, exist_ok=True)
    width, height = frames.size(video)
    scale = 720 / height
    width, height = int(width * scale), 720

    scene = scene.model_copy(deep=True)
    iterations = 0
    for it in range(1, max_iterations + 1):
        iterations = it
        emit("loop", f"Loop iter {it}: rendering scene from {len(scene.keyframes)} keyframe viewpoints")
        pairs = []
        for k, kf in enumerate(scene.keyframes):
            real = frames.extract(video, kf.t, frame_dir)
            pair = side_by_side(real, render(scene, kf, width, height), caption=f"keyframe {k} @ {kf.t:.1f}s")
            path = pair_dir / f"iter{it}_kf{k}.jpg"
            pair.save(path, quality=88)
            pairs.append(path)

        contents: list = [PROMPT + _scene_for_prompt(scene)]
        for k, path in enumerate(pairs):
            contents += [f"Keyframe {k} (t={scene.keyframes[k].t:.1f}s):", argon.image_part(path)]
        emit("loop", f"Loop iter {it}: Argon comparing {len(pairs)} rendered views with the real frames")
        critiques = argon.ask(contents, CritiqueList, system=SYSTEM).critiques

        if not critiques:
            emit("loop", f"Loop iter {it}: rendered views match the video, no critiques", "ok")
            break

        applied = 0
        for c in critiques:
            t = scene.keyframes[c.keyframe].t if 0 <= c.keyframe < len(scene.keyframes) else None
            where = f" @{int(t // 60)}:{t % 60:04.1f}" if t is not None else ""
            pair = str(pairs[c.keyframe]) if 0 <= c.keyframe < len(pairs) else None
            emit("loop", f"Loop iter {it}: {c.problem}{where}", "warn",
                 fix=c.fix, target=c.target, keyframe=c.keyframe, pair=pair)
            result = _apply(scene, c)
            if result:
                applied += 1
                emit("loop", f"Loop iter {it}: fix → {result}", "ok", fix=c.fix, target=c.target)
            else:
                emit("loop", f"Loop iter {it}: couldn't apply {c.fix} on {c.target}, skipped", "info")
        if applied == 0:
            emit("loop", f"Loop iter {it}: no applicable fixes, stopping", "info")
            break

    return scene, iterations
