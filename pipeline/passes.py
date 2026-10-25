"""Completeness pass (find what's missing, segment by segment) and precision pass (drop hallucinations)."""

import json
from pathlib import Path

import argon
import frames
from events import emit
from models import Item, MissingItems, Scene, VerdictList
from reconstruct import SYSTEM as RECON_SYSTEM

SEGMENT = 30.0
PRECISION_BATCH = 8

COMPLETENESS_PROMPT = """\
This is the {start}-{end} segment of a room walkthrough. Below is the inventory found so far (ids, labels, positions).
List belongings clearly visible in this segment that are NOT already in the inventory — same rules as before:
insurable items only, one entry per physical object, specific priceable labels, positions in the same coordinates.
Check positions before adding: an item already listed near the same spot is the same item, not a new one.
Return an empty list if nothing is missing.

Inventory:
"""

PRECISION_PROMPT = """\
Below, each item an inventory claims to have seen comes with one or more evidence frames.
Answer per item id: is that object visible in ANY of its frames (partly visible counts)? Frames can be blurry or
mid-pan, so check them all before answering no. Say not visible only if no frame shows it, or the frames show
something different (a cushion called a pillow is fine; a chair called a table is not).
"""
FRAMES_PER_ITEM = 3


def _fmt(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _inventory(scene: Scene) -> str:
    return json.dumps([i.model_dump(include={"id", "label", "anchor"}) for i in scene.items], indent=1)


def completeness(scene: Scene, video: str | Path) -> Scene:
    scene = scene.model_copy(deep=True)
    f = argon.upload_video(video)
    total = frames.duration(video)
    start = 0.0
    while total - start > 3.0:  # skip a sub-3s tail
        end = min(start + SEGMENT, total)
        if total - end <= 3.0:
            end = total
        emit("loop", f"Completeness: what's visible {_fmt(start)}–{_fmt(end)} that isn't listed?")
        prompt = COMPLETENESS_PROMPT.format(start=_fmt(start), end=_fmt(end)) + _inventory(scene)
        found = argon.ask([argon.video_part(f, start, end), prompt], MissingItems, system=RECON_SYSTEM).items
        for it in found:
            item = Item(id=scene.next_id(), **it.model_dump())
            scene.items.append(item)
            emit("loop", f"Completeness: found {item.label} @{_fmt(item.seen_at[0] if item.seen_at else start)} → adding {item.id}",
                 "ok", id=item.id, seen_at=item.seen_at)
        start = end
    return scene


def precision(scene: Scene, video: str | Path, out_dir: str | Path) -> Scene:
    scene = scene.model_copy(deep=True)
    frame_dir = Path(out_dir) / "frames"
    checkable = [i for i in scene.items if i.seen_at]
    emit("loop", f"Precision: checking each of {len(checkable)} items against its evidence frame")
    for b in range(0, len(checkable), PRECISION_BATCH):
        batch = checkable[b:b + PRECISION_BATCH]
        contents: list = [PRECISION_PROMPT]
        for item in batch:
            ts = item.seen_at[:FRAMES_PER_ITEM]
            contents.append(f"Item {item.id}: {item.label}. Evidence frames at " + ", ".join(_fmt(t) for t in ts) + ":")
            contents += [argon.image_part(frames.extract(video, t, frame_dir)) for t in ts]
        verdicts = {v.id: v for v in argon.ask(contents, VerdictList).verdicts}
        for item in batch:
            v = verdicts.get(item.id)
            if v is not None and not v.visible:
                scene.items = [i for i in scene.items if i.id != item.id]
                emit("loop", f"Precision: {item.label} not in any of its frames → removing {item.id}",
                     "warn", id=item.id, reason=v.reason)
    emit("loop", f"Precision: {len(scene.items)} items confirmed", "ok")
    return scene
