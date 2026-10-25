# Contracts

Frozen at Hour 0 (PLAN.md §5). Change these only after telling the other agent.

| File | What it describes | Written by |
| --- | --- | --- |
| `scene.schema.json` | `scene.json`, the shared scene | A (geometry, items, fidelity, status), B (`items[].price`) |
| `event.schema.json` | one line of `events.jsonl` | every agent |
| `ground_truth.schema.json` | `fixtures/ground_truth.json`, the hand count | a human, once |

## Coordinates

- Units are meters. Right-handed, **y is up** (same as Three.js defaults).
- The floor is the plane `y = 0`. Wall endpoints `from` / `to` are `[x, z]` on the floor.
- `items[].anchor` is the **center of the item's box**, so a box sits on the floor when `anchor.y == size.h / 2`.
- `size.w` runs along x, `size.h` along y, `size.d` along z. Boxes are axis-aligned (no rotation).
- `keyframes[].position` is the camera position. `look_at` is the point the camera faces. Vertical field of view is assumed to be 60° unless `keyframes[].fov` is set.
- `openings[].wall` is an index into that room's `walls` array. `offset` is meters from the wall's `from` end to the near edge of the opening.

## events.jsonl

Append-only, one JSON object per line. `ts` is local wall-clock `HH:MM:SS`. `agent` is one of `reconstruct`, `loop`, `fidelity`, `pricing`, `diff`, `claim`. `status` is `ok`, `info`, `warn` or `error`. The `action` text is what the Activity panel shows, so write it for a judge to read.
