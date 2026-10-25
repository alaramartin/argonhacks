"""Single pass: Argon watches the walkthrough and writes a primitive scene."""

from pathlib import Path

import argon
from events import emit
from models import ArgonScene, Item, Scene

CONVENTIONS = """\
Coordinate conventions (follow exactly):
- Meters. Right-handed, y is up. The floor is y = 0.
- Put the origin at one floor corner of the room so most coordinates are positive.
- Walls: `from` and `to` are [x, z] floor points; `height` is the ceiling height. List walls in order around the room.
- Openings: `wall` is the index into that room's walls, `offset` is meters from the wall's `from` end to the near edge.
- Items are axis-aligned boxes. `anchor` is the CENTER of the box, so an item on the floor has anchor.y = size.h / 2.
  `size.w` is along x, `size.h` along y, `size.d` along z.
- Keyframes: `t` is seconds into the video, `position` is the camera position (a handheld phone is about 1.5 m up),
  `look_at` is a point the camera is pointed at. Assume a 60 degree vertical field of view.
"""

SYSTEM = f"""\
You reconstruct rooms from phone walkthrough videos for home-insurance inventories.
A missed item underpays the claim; an invented item makes it fraudulent. List only what you can see.

{CONVENTIONS}"""

PROMPT = """\
Watch this walkthrough and reconstruct the space as a primitive scene.

1. rooms: walls (with ceiling height) and doors/windows.
2. items: every distinct belonging an insurer would pay for — furniture, electronics, appliances, lamps, decor,
   rugs, art, instruments, small appliances on counters, visible valuables. Skip built-ins (outlets, radiators,
   light switches, built-in cabinets) and skip people.
   - One entry per physical object. Two identical chairs are two items.
   - `label` must be specific enough to price by like kind and quality: kind, color, material, size class,
     and brand only if it's readable (e.g. "gray 2-seat fabric loveseat", "black 55-inch flat-screen TV, wall-mounted").
   - `seen_at`: 1-3 timestamps (seconds) where the item is clearly visible.
   - `confidence`: 0-1, how sure you are the item exists as labeled.
3. keyframes: 6-10 moments spread across the video where the camera is fairly still, with your best estimate of
   the camera position and look-at point. Together they should cover every wall.
"""


def run(video: str | Path) -> Scene:
    emit("reconstruct", f"Uploading walkthrough video ({Path(video).name})")
    f = argon.upload_video(video)
    emit("reconstruct", "Argon is watching the walkthrough and writing the scene")
    raw = argon.ask([argon.video_part(f), PROMPT], ArgonScene, system=SYSTEM)

    items = []
    for n, it in enumerate(raw.items, start=1):
        items.append(Item(id=f"item_{n:03d}", **it.model_dump()))
    scene = Scene(rooms=raw.rooms, keyframes=raw.keyframes, items=items)

    walls = sum(len(r.walls) for r in scene.rooms)
    emit("reconstruct", f"Room layout: {len(scene.rooms)} room(s), {walls} walls, {len(scene.keyframes)} keyframes", "ok")
    for n, item in enumerate(scene.items, start=1):
        emit("reconstruct", f"Indexing item {n}/{len(scene.items)}: {item.label}", "ok",
             id=item.id, seen_at=item.seen_at, confidence=item.confidence)
    return scene
