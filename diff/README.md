# diff (Agent A, Part 2)

Compares an "after" walkthrough with the baseline scene and marks every item `present`, `moved` or `stolen`. Lives on the `part2` branch.

## Run it

```
cd pipeline
uv run python ../diff/diff.py --after ../fixtures/after.mp4            # baseline: ../out/scene.json, output: ../out/diff
uv run python ../diff/score.py                                         # check against fixtures/after_truth.json
uv run python ../diff/diff.py --reprice                                # baseline prices changed: copy them in, no Argon calls
```

A run takes 3 to 5 minutes.

## What it does

1. **Survey.** Argon watches the after video with the baseline inventory (each item's label, position and before-frame) and says whether each item is in the same place, somewhere else, or not seen.
2. **Re-check.** Each missing item gets a second, focused look: is the item anywhere in the after video, and was its old spot clearly in view? An item whose spot is never clearly in view stays `present`, because we can't show it's gone. Rounds repeat until the stolen list is the same twice in a row (at most 3).
3. **Confirm.** For each remaining theft, we build the BEFORE | AFTER image the claim will carry, and Argon has to confirm removal from that image alone. If it can't, the item stays `present` and the panel says why.

## Output in `out/diff/`

| File | Contents |
| --- | --- |
| `scene.json` | the baseline scene with `items[].status` set on every item (contract field) |
| `stolen.json` | the claim list, one entry per stolen item (below) |
| `stolen_<id>.jpg` | BEFORE \| AFTER evidence image for each stolen item |
| `events.jsonl` | `diff` events for the Activity panel |
| `diff_score.json` | written by `score.py` |

A `stolen.json` entry:

```json
{
  "id": "item_023",
  "label": "space gray Apple MacBook laptop",
  "price": { "low": 859.0, "high": 1099.0, "currency": "USD", "sources": ["..."], "basis": "like kind and quality" },
  "before_t": 74.0,
  "after_t": 90.0,
  "evidence": "out/diff/stolen_item_023.jpg",
  "reason": "The MacBook is clearly visible on the round coffee table before, and that surface is empty after."
}
```

`before_t` is seconds into `fixtures/before.mp4` and `after_t` seconds into `fixtures/after.mp4`. `evidence` is relative to the repo root, the same convention as the loop images.

## Results so far

On the lobby walkthrough (laptop removed, one stool carried across the room, trash can shifted), runs #10 to #12 in `RUNS.md` all found the laptop and the moved stool. #10 also flagged a small vase hidden behind another vase. The confirm step was added after that, and in #12 it blocked the same false claim.
