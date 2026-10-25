"""Completeness pass (find what's missing, segment by segment) and precision pass (drop hallucinations)."""

import json
import math
import re
from pathlib import Path

import argon
import frames
from events import emit
from fidelity import _tokens
from pydantic import BaseModel

from models import Item, MissingItems, Scene, Verdict, VerdictList
from reconstruct import SYSTEM as RECON_SYSTEM

SEGMENT = 30.0
PRECISION_BATCH = 8

COMPLETENESS_PROMPT = """\
This is the {start}-{end} segment of a room walkthrough. Below is the inventory found so far (ids, labels, positions).
List belongings clearly visible in this segment that are NOT already in the inventory — same rules as before:
insurable items only, one entry per physical object, specific priceable labels, positions in the same coordinates.
Check positions before adding: an item already listed near the same spot is the same item, not a new one.
But if an inventory label bundles several separate objects ("bike with helmet", "book with flowers and vases"),
the extra objects are NOT listed yet: add each one as its own item.
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


def verify(items: list[Item], video: str | Path, frame_dir: str | Path) -> dict[str, Verdict]:
    """Ask Argon whether each item really shows in its own evidence frames. Items without evidence are skipped."""
    items = [i for i in items if i.seen_at]
    out: dict[str, Verdict] = {}
    for b in range(0, len(items), PRECISION_BATCH):
        batch = items[b:b + PRECISION_BATCH]
        contents: list = [PRECISION_PROMPT]
        for item in batch:
            ts = item.seen_at[:FRAMES_PER_ITEM]
            contents.append(f"Item {item.id}: {item.label}. Evidence frames at " + ", ".join(_fmt(t) for t in ts) + ":")
            contents += [argon.image_part(frames.extract(video, t, frame_dir)) for t in ts]
        out.update({v.id: v for v in argon.ask(contents, VerdictList).verdicts})
    return out


DEDUPE_PROMPT = """\
An inventory lists the two items below as separate belongings. Their labels are similar and they are close
together in the room, so they may be the same physical object counted twice. Look at each item's evidence frames.
Answer per pair: same physical object, or two different objects? Identical items side by side (two matching chairs)
are different objects. If you can't tell, say different: never merge real belongings away.
"""
DEDUPE_RADIUS = 1.0


class PairVerdict(BaseModel):
    pair: int
    same_object: bool
    reason: str


class PairVerdictList(BaseModel):
    verdicts: list[PairVerdict]


def _object_words(label: str) -> set[str]:
    """Words naming the object itself, without where it is: "plant on reception counter" → {plant}."""
    head = re.split(r" on | near | behind | next to | in front of | by | at | under |\(|,", label.lower())[0]
    return _tokens(head)


def _candidate_pairs(scene: Scene) -> list[tuple[Item, Item]]:
    pairs = []
    for n, a in enumerate(scene.items):
        for b in scene.items[n + 1:]:
            wa, wb = _object_words(a.label), _object_words(b.label)
            if not wa or not wb or len(wa & wb) / min(len(wa), len(wb)) <= 0.5:
                continue
            if math.dist((a.anchor.x, a.anchor.y, a.anchor.z), (b.anchor.x, b.anchor.y, b.anchor.z)) < DEDUPE_RADIUS:
                pairs.append((a, b))
    return pairs


def dedupe(scene: Scene, video: str | Path, out_dir: str | Path) -> Scene:
    scene = scene.model_copy(deep=True)
    pairs = _candidate_pairs(scene)
    if not pairs:
        return scene
    emit("loop", f"Precision: checking {len(pairs)} pairs of similar items for double counting")
    frame_dir = Path(out_dir) / "frames"
    contents: list = [DEDUPE_PROMPT]
    for n, (a, b) in enumerate(pairs):
        contents.append(f"Pair {n}: {a.id} '{a.label}' vs {b.id} '{b.label}'.")
        for item in (a, b):
            ts = item.seen_at[:2]
            contents.append(f"{item.id} evidence at " + ", ".join(_fmt(t) for t in ts) + ":")
            contents += [argon.image_part(frames.extract(video, t, frame_dir)) for t in ts]
    verdicts = argon.ask(contents, PairVerdictList, thinking="high").verdicts
    removed = set()
    for v in verdicts:
        if not v.same_object or not 0 <= v.pair < len(pairs):
            continue
        a, b = pairs[v.pair]
        if a.id in removed or b.id in removed:
            continue
        keep, drop = (a, b) if a.confidence >= b.confidence else (b, a)
        removed.add(drop.id)
        emit("loop", f"Precision: {drop.id} {drop.label} is the same object as {keep.id} → removing the double count",
             "warn", id=drop.id, duplicate_of=keep.id, reason=v.reason)
    scene.items = [i for i in scene.items if i.id not in removed]
    return scene


def precision(scene: Scene, video: str | Path, out_dir: str | Path) -> Scene:
    scene = scene.model_copy(deep=True)
    checkable = [i for i in scene.items if i.seen_at]
    emit("loop", f"Precision: checking each of {len(checkable)} items against its evidence frame")
    verdicts = verify(checkable, video, Path(out_dir) / "frames")
    for item in checkable:
        v = verdicts.get(item.id)
        if v is not None and not v.visible:
            scene.items = [i for i in scene.items if i.id != item.id]
            emit("loop", f"Precision: {item.label} not in any of its frames → removing {item.id}",
                 "warn", id=item.id, reason=v.reason)
    scene = dedupe(scene, video, out_dir)
    emit("loop", f"Precision: {len(scene.items)} items confirmed", "ok")
    return scene
