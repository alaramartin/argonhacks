# Proof of Loss

After a fire, flood or burglary, your insurer asks you to list everything you owned, from memory, with a value for each item. That list is the "proof of loss". People forget most of what they had, and every forgotten item is money they don't get back.

Proof of Loss turns one walkthrough video of your home into a **verified** 3D inventory with a price on every item. "Verified" is the point. A single pass that invents an item inflates the claim; one that misses an item underpays it. So Gemini 4 Argon renders its reconstruction back from the camera positions in the video, compares each render with the real frame, and fixes the scene until they agree. Then Managed Agents sandboxes browse real retailers to price every item at "like kind and quality", the standard insurers reimburse at.

After a burglary, you walk the same space again. Argon compares the "after" video with the inventory, tells moved items from stolen ones, and only calls an item stolen when its BEFORE | AFTER image proves it. A claim agent in a Managed Agents sandbox then fills the insurer's claim form from the stolen list and stops for you to review and submit. Every step any agent takes appears live in the Activity panel.

Built at the **Gemini 4 Argon Hackathon** (Cerebral Valley × Google DeepMind, San Francisco, October 25 2026) for **Problem Statement 2: More Dimensions** and **Best Use of Managed Agents**.

## Results on our demo walkthrough

A 2-minute walkthrough of a building lobby, scored against a hand count of the 35 insurable items in it (`fixtures/ground_truth.json`, which the pipeline never reads):

| | Accuracy | Items found |
|---|---|---|
| Argon single pass | 88% | 28 of 35 |
| After the render-and-compare loop and both checking passes | **96%** | **35 of 35** |

Accuracy is the F1 score: it combines how many of the room's items were found with how many of the listed items are real. These numbers are from the run in our demo video ([RUNS.md](RUNS.md) #25). The scene the live demo loads comes from an earlier run of the same pipeline and scores 93% (34 of 35).

Every item in the live demo's scene is priced from 2–3 retailer listings, each kept with its product title, price and URL.

For the "after" walkthrough we removed the laptop from the coffee table, carried one stool across the room and shifted the trash can. Scored against that list (`fixtures/after_truth.json`):

| | Laptop | Stool | Trash can | False thefts |
|---|---|---|---|---|
| After-video diff | stolen | moved | moved | none |
| Control: before video compared with itself | — | — | — | none (0 stolen, 0 moved) |

The claim agent then filed the laptop at $1,099.00 replacement cost (median of 3 listings) on the Mock Mutual demo portal, with its evidence photo, and left it unsubmitted for review.

Every test run, including the failed ones, is logged in [RUNS.md](RUNS.md).

## How it works

1. **Single pass.** Argon watches the whole video and writes the room's walls, 6–10 camera keyframes (a timestamp plus an estimated camera pose), and every item as a labeled box with a position, a size and the timestamps where it is visible.
2. **Render-and-compare loop.** For each keyframe, ffmpeg pulls the real frame and our renderer draws the current scene from the estimated camera. Argon gets the REAL | RENDER pairs and returns critiques: add, remove, move, resize, relabel, or split a bundled entry. We apply them and repeat, up to 4 times. Guards refuse an add that duplicates an existing box, and a removal while the item still shows in its own evidence frames. With `--sandbox`, this step runs inside a Managed Agents sandbox, where Argon can pull extra frames and test-render its fixes before proposing them.
3. **Completeness and precision passes.** Argon rewatches the video in 30-second segments looking for missed items, then checks each item against its evidence frames and looks for double counting.
4. **Fidelity.** A deterministic, model-free word matcher scores the single pass and the final scene against the hand count.
5. **Pricing.** Several Managed Agents sandboxes run in parallel. Each browses retailers for one item at a time and returns a price range with its sources.
6. **Viewer.** A Three.js scene of the room with a price tag on every item, the fidelity scorecard, the loop's REAL | RENDER catches, and the live Activity panel. Clicking an item plays the video at the moment it was seen.
7. **After-video diff.** Argon watches the after video with the inventory and says, per item, same place, moved or not seen. Every missing item gets a focused re-check: is it anywhere, and was its old spot clearly in view? A spot never clearly in view can't be claimed. Each remaining theft must then be confirmed on the BEFORE | AFTER image the claim will carry.
8. **Claim.** Each stolen item is valued at the median comparable listing. The claim agent runs in a Managed Agents sandbox whose network allowlist is only the portal's host. The portal's token is a Credentials API credential the proxy injects, so it never enters the sandbox or the model's context. The agent fills the form field by field and stops; only the policyholder, on their own device, can submit. The portal is hosted on Vercel, so it stays up with the laptop closed.

Details: [pipeline/README.md](pipeline/README.md) (reconstruction, loop, fidelity), [diff/README.md](diff/README.md) (after-video diff) and [PLAN.md](PLAN.md) (architecture, contracts, plan for the day).

## Run it

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 18+, ffmpeg on the PATH, and a Gemini API key.

```
# API key: put GEMINI_API_KEY=... in .env (repo root, for agents/) and pipeline/.env (for the pipeline)
copy env.example .env

# 1. Reconstruct, loop, score (Agent A)
cd pipeline
uv sync
uv run python orchestrator.py run --video ../fixtures/before.mp4 --out ../out/run
uv run python orchestrator.py publish ../out/run
cd ..

# 2. Price every item (Agent B)
pip install -r agents/requirements.txt
python agents/pricing/price.py                 # live, parallel sandboxes
python agents/pricing/price.py --cache-only    # replay cached prices, no network

# 3. View
node viewer/server.js                          # http://localhost:8080

# 4. After video: diff, then claim (the viewer's upload and "File claim" buttons run these)
cd pipeline && uv run python ../diff/diff.py --fast --after ../fixtures/after.mp4 && cd ..
node agents/claim/portal/server.js             # policyholder's review page, http://localhost:8090
python agents/claim/claim.py --local           # fill the portal directly
python agents/claim/claim.py --portal https://mock-mutual-portal.vercel.app   # the sandboxed claim agent
```

The videos aren't committed (they're large). Put them at `fixtures/before.mp4` and `fixtures/after.mp4`.

The Mock Mutual portal runs at https://mock-mutual-portal.vercel.app (`portal-vercel/`, state in Upstash Redis). With `PORTAL_REMOTE_URL` set, the local `server.js` serves the policyholder's review page, forwards to it with the policyholder token, and copies its events into the Activity panel. `env.example` lists the four settings. Without them, `server.js` runs the whole portal locally, reachable by the agent through a cloudflared tunnel.

## Built at the hackathon

**Everything in this repository was written during the event, on October 25 2026.** Hacking started at 10:30. The first commit, the plan, is at 10:50, and the [commit history](../../commits/main) shows every component arriving in small timestamped commits through the day. The two authors are `alaramartin` (Agent A: reconstruction, loop, fidelity) and `cabbles` (Agent B: viewer, agents).

What we built today:

| Component | Where | What it does |
|---|---|---|
| Interface contracts | `contracts/` | JSON schemas for the scene, the event stream and the ground truth, frozen at 11:02 |
| Orchestrator | `pipeline/orchestrator.py` | Runs the stages, saves every stage's scene, publishes a run to the viewer, keeps prices across republishes |
| Single-pass reconstruction | `pipeline/reconstruct.py`, `pipeline/argon.py` | Video → rooms, keyframes and items via Gemini 4 Argon structured output |
| Renderer | `pipeline/render.py`, `pipeline/render_pairs.py` | Software renderer for the box scene from an estimated camera, and the REAL \| RENDER composites |
| Render-and-compare loop | `pipeline/loop.py` | Argon critiques render vs real frame and the fixes are applied, with duplicate and evidence-frame guards |
| Sandboxed loop | `pipeline/sandbox.py` | The same loop inside a Managed Agents sandbox, every step streamed to the Activity panel |
| Completeness and precision passes | `pipeline/passes.py` | Segment-by-segment rewatch, evidence-frame checks, double-count detection |
| Fidelity scoring | `pipeline/fidelity.py`, `fixtures/ground_truth.json` | Hand count of the demo room; deterministic recall/precision with every match, miss and extra listed |
| Pricing agent | `agents/pricing/price.py` | Parallel Managed Agents sandboxes browse retailers; price ranges with sources; cache for offline demos |
| Event stream | `pipeline/events.py`, `agents/events.py` | Append-only `events.jsonl` that every agent writes and the viewer tails |
| Viewer and Activity panel | `viewer/` | Three.js scene, price tags with declutter, fidelity scorecard, loop-catch card, evidence video, live SSE event feed |
| After-video diff | `diff/diff.py`, `diff/score.py`, `fixtures/after_truth.json` | Present / moved / stolen with re-checks and evidence-image confirmation; scored against the list of what we changed |
| Claim agent | `agents/claim/claim.py` | Median-listing valuation, Credentials API token, sandbox with a one-host allowlist, field-by-field fill, stops before submit |
| Mock Mutual portal | `portal-vercel/`, `agents/claim/portal/` | Made-up insurer's claim site, hosted on Vercel. The agent's token can only fill; submit, reset and photos need the policyholder's token. Every accepted field is logged as an event. The local server is the review page, or the whole portal behind a tunnel |
| After flow in the viewer | `viewer/` | Upload an after video, run the diff, color items by status, file the claim |
| Run log | `RUNS.md` | Every test run with settings, timings and scores |

What we did **not** build:

- **Models and agents (Google):** Gemini 4 Argon (`gemini-4-argon`), Gemini 3.8 Flash (`gemini-3.8-flash`), and the Antigravity managed agent (`antigravity-preview-09-2026`), called through the Interactions API.
- **Libraries:** [google-genai](https://pypi.org/project/google-genai/) (Gemini SDK), [Three.js](https://threejs.org/) 0.169.0 with its OrbitControls and CSS2DRenderer add-ons (loaded from unpkg), [Pillow](https://python-pillow.org/), [NumPy](https://numpy.org/), [Pydantic](https://docs.pydantic.dev/), [FFmpeg](https://ffmpeg.org/) (frame extraction), [uv](https://docs.astral.sh/uv/) (Python environments), [Upstash Redis](https://upstash.com/) via the Vercel Marketplace (portal state) and its [@upstash/redis](https://www.npmjs.com/package/@upstash/redis) client, [Vercel](https://vercel.com/) (hosts the portal), [cloudflared](https://github.com/cloudflare/cloudflared) quick tunnels (the earlier way to expose a local portal to the agent's sandbox).
- **Data:** we filmed the walkthrough video ourselves, in the venue's lobby. Prices come from public retailer product pages, linked in each item's sources.
