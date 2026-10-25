# pipeline (Agent A)

Turns a walkthrough video into `scene.json`: the room's walls, keyframe camera poses, and every insurable item as a labeled box. Then it checks its own work against the footage and fixes it.

## Run it

```
cd pipeline
uv sync
# GEMINI_API_KEY in pipeline/.env (gitignored)

uv run python orchestrator.py run --video ../fixtures/before.mp4 --out ../out/final
uv run python orchestrator.py publish ../out/final     # copy to out/, where the viewer reads it
```

Other commands:

| Command | What it does |
| --- | --- |
| `run --from loop` | rerun from a stage; every stage saves its scene, so `reconstruct` doesn't have to repeat |
| `run --sandbox` | run the loop's render-and-critique step in a Managed Agents sandbox instead of locally |
| `run --skip-passes --max-iter 1` | loop only, one iteration |
| `smoke --video clip.mp4` | single pass plus renders, for a quick look |
| `score` | rescore a run against `fixtures/ground_truth.json` |

## Stages

1. **Single pass** (`reconstruct.py`). Argon watches the whole video once and writes rooms, 6 to 10 keyframes (a timestamp plus an estimated camera position and look-at point) and the items. Saved as `scene.single_pass.json`.
2. **Render-and-compare loop** (`loop.py`, `render.py`). For each keyframe, ffmpeg pulls the real frame and our renderer draws the current scene from the estimated camera, with every box labeled by its id. Argon gets the REAL | RENDER pairs and returns critiques: add a missing item, remove, move, resize, relabel, split a bundled entry ("bike with helmet") into separate items, or re-estimate a camera. We apply them and render again, up to 4 times or until Argon has nothing to fix. Two guards run before a fix lands. An add that lands on top of an existing box with a similar label is refused as a duplicate, and a removal is refused if the item still shows in its own evidence frames.
3. **Completeness pass** (`passes.py`). Argon rewatches the video in 30-second segments with the current item list and adds what's missing.
4. **Precision pass** (`passes.py`). Each item is checked against its evidence frames (all of them, since a single frame can be motion-blurred) and removed if none shows it. Then nearby items with similar labels are checked for double counting.
5. **Fidelity** (`fidelity.py`). Scores the single-pass scene and the final scene against the hand count.

Every step appends a line to `events.jsonl`, which the viewer's Activity panel tails. Loop events carry the path of the REAL | RENDER image they came from.

## How fidelity is computed

`fixtures/ground_truth.json` is a hand-checked list of every insurable item in the demo room, with a few aliases each ("e-scooter", "kick scooter"). The pipeline never reads it.

Matching is deterministic and doesn't use a model. Both labels are lowercased, and colors, materials, size words and generic words like "stand" or "device" are dropped. An item matches a ground-truth entry when it covers at least half of the entry's remaining words (or an alias's). Pairs are assigned one-to-one, best first. Recall is matched items over ground-truth items. Precision is matched items over listed items. `out/fidelity.json` lists every match, every missed item and every extra, so any number can be checked by hand.

Both passes follow the same counting rules. Built-ins (counters, fixed shelving), consumables (snacks, pens, paper) and people's clothing and bags are left out of both the scene and the hand count.

Final run on the lobby walkthrough: the single pass found 30 of 35 items at 97% precision. After the loop and both passes it found 34 of 35 at 87% precision.

## Managed Agents

With `--sandbox`, each loop iteration runs on the Antigravity managed agent (`antigravity-preview-09-2026`) with `gemini-4-argon`. One environment per run holds the video and the renderer (`render_pairs.py` and its imports). In each iteration Argon renders the pairs inside the sandbox, opens them, pulls extra frames with ffmpeg when something is unclear, test-renders its proposed fixes, and writes `critiques_<run>_iter<n>.json`, which we download and apply. Every command it runs streams into the Activity panel as it starts. One sandbox iteration takes about 8 minutes, against about 2.5 minutes for the local loop.

## Files

| File | Role |
| --- | --- |
| `orchestrator.py` | CLI; drives the stages and owns `scene.json` |
| `argon.py` | Gemini client: cached video upload, structured calls with `thinking_level` |
| `reconstruct.py` | single pass and the shared counting rules |
| `loop.py` | Loop 1, local or sandboxed |
| `render.py`, `render_pairs.py` | software renderer and the REAL \| RENDER composites |
| `passes.py` | completeness, precision and duplicate checks |
| `fidelity.py` | scoring against the hand count |
| `sandbox.py` | Managed Agents environment, uploads and streamed steps |
| `models.py` | pydantic mirror of `contracts/` |
| `events.py` | `events.jsonl` writer |

Libraries: google-genai, pydantic, Pillow, numpy, and ffmpeg on the PATH.
