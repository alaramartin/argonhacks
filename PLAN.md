# PLAN.md — Proof of Loss

**Gemini 4 Argon Hackathon · Cerebral Valley × Google DeepMind · Oct 25 2026**
Target: **Problem Statement 2 (More Dimensions)** + **Best Use of Managed Agents ($5k)** (details in hackathon.md)

---

## 1. One-liner

After a disaster — fire, flood, theft — you have to list everything you owned, from memory, to file an insurance claim ("proof of loss"). Walk your space once on video; we build a **verified** explorable 3D inventory, price every item by actually browsing retailers, and auto-file the claim. "Verified" is the whole point: a single-pass reconstruction that hallucinates an item produces an inflated claim, and one that misses an item means underpayment — so we render the reconstruction back from the original viewpoints and correct it against the real footage until it matches.

## 2. Hard split: Part 1 and Part 2

The build is split so we **always have a complete, demoable product even if Part 2 never works.**

- **Part 1 (must-have, the demo floor):** video in → explorable 3D reconstruction, corrected by the render-and-compare loop → priced by a Managed Agents browser agent → price-tag UI on every item. This alone is a complete PS2 + Managed Agents entry.
- **Part 2 (stretch, the emotional close):** upload an "after" video (e.g. post-burglary) → classify each item **stolen vs moved** (moved → ignore; stolen → goes on a list) → the stolen list auto-fills the insurance claim form.

**GATE: neither person starts Part 2 until Part 1 passes the acceptance test in §7.**

## 3. The judged requirement that shapes everything: visible agent actions

Judges stated the **actions the agents take must be visible** — indexing items, searching prices, filling forms. This is not decoration; it's a scoring criterion and our own integration/debug tool. So it's a first-class, shared component built in Hour 0:

- Every agent (reconstruct, loop, pricing, diff, claim) appends human-readable events to a shared append-only stream `events.jsonl`.
- The viewer has an **Activity panel** that tails the stream live.
- Example events the panel must show in real time:
    - `Indexing item 7/20: floor lamp (left of sofa)`
    - `Searching price: "gray 2-seat fabric loveseat" → $380–$520 (3 retailers)`
    - `Loop iter 3: rendered view missing TV present in real frame @0:42 → adding`
    - `Filling claim field: item_description = "55\" OLED TV, wall-mounted"`

If an action isn't visible in the panel, it doesn't count. Build the panel early and wire every agent into it from the start.

**Layout rule:** the 3D scene is the centerpiece; the Activity panel sits beside it. Never let the UI read as a dashboard (that's on the banned list).

## 4. Architecture

```
video (720p, 2–3 min) ─▶ [A] reconstruct (Argon writes primitive scene JSON + keyframe viewpoints)
                       │
                       ▼
              [A] Loop 1: render scene from each keyframe viewpoint ─▶ Argon critique vs real frame ─▶ fix ─▶ re-render
                       │ (until no critiques, or max 4 iterations)
                       ▼
                 scene.json (rooms + items[] + keyframes + fidelity)
                       │
         ┌─────────────┴───────────────┐
         ▼                             ▼
 [B] 3D viewer + tags          [B] pricing agent (Managed Agents browser)
         │                             │ writes items[].price
         └──────────── Activity panel (tails events.jsonl) ────────────┘

── PART 2 (only after gate) ──
after-video ─▶ [A] align to baseline ─▶ classify present/moved/stolen ─▶ stolen list
                                                                           │
                                                      [B] claim agent (MA + Credentials API)
                                                      fills mock claim form, visibly
```

**Reconstruction approach (decided, not a fallback):** Argon outputs text, not meshes. It watches the video and writes a **primitive scene JSON** — walls, doors, windows, and every item as a labeled box with position and size — plus 6–10 **keyframes**: a video timestamp and an estimated camera position + look-at point. Three.js renders the boxes. No COLMAP, no photogrammetry, no splats. Labeled boxes in the right places are enough to tell the story.

**Agent execution:** all agent work (render loop, pricing, claim) runs in **Managed Agents sandboxes** — one API call provisions a Google-hosted Linux sandbox. Managed agents can't spawn subagents, so **our own orchestrator code drives the loop** and calls the sandbox for each step. Credentials (insurance portal login) go through the **Credentials API**: injected at the proxy, never in model context, never off the network allowlist. _(Confirm exact SDK signatures in the Managed Agents docs — don't take these as verbatim.)_

**Model check (first 30 min):** confirm whether `agent_config` accepts `"model": "gemini-4-argon"` (docs only clearly list Flash models). Two possible splits:
- **If yes:** Argon runs inside the managed agent end to end.
- **If no:** Argon is called from our orchestrator as the **brain** (generate + critique); the managed agent on Flash is the **hands** (render, extract frames, browse, fill forms). Pitch it exactly that way.

## 5. Interface contract — AGREE THIS FIRST (Hour 0), then never fight over it

Parallelism depends entirely on both of these being frozen early. Commit them to `/contracts` before splitting.

**`scene.json`**

```json
{
    "rooms": [
        {
            "id": "room_1",
            "walls": [{ "from": [0, 0], "to": [4.5, 0], "height": 2.6 }],
            "openings": [{ "type": "door", "wall": 0, "offset": 1.2, "width": 0.9 }]
        }
    ],
    "keyframes": [
        {
            "t": 42.0, // seconds into the video
            "position": { "x": 0.5, "y": 1.6, "z": 3.0 },
            "look_at": { "x": 2.0, "y": 1.0, "z": 0.0 }
        }
    ],
    "items": [
        {
            "id": "item_003",
            "label": "stand mixer, red",
            "anchor": { "x": 1.2, "y": 0.9, "z": -0.8 },
            "size": { "w": 0.4, "h": 0.4, "d": 0.3 },
            "seen_at": [41.5, 43.0], // video timestamps (evidence frames)
            "confidence": 0.91,
            "price": {
                "low": 379.0,
                "high": 449.0,
                "currency": "USD",
                "sources": ["..."],
                "basis": "like kind and quality"
            }, // B writes
            "status": "present" // A writes in Part 2: present|moved|stolen
        }
    ],
    "fidelity": {
        "ground_truth_count": 24,
        "single_pass": { "recall": 0.58, "precision": 0.85 },
        "after_loop": { "recall": 0.92, "precision": 0.96 },
        "iterations": 3
    } // numbers above are illustrative ONLY; real values come from scoring against ground_truth.json
}
```

**`events.jsonl`** (one JSON object per line, append-only)

```json
{
    "ts": "10:32:14",
    "agent": "pricing",
    "action": "Searching price: red stand mixer",
    "status": "ok",
    "payload": { "low": 379.0, "high": 449.0 }
}
```

**`ground_truth.json`** — hand-counted list of every item in the demo room (label + rough location). Written once at 10:30, never edited by agents. Used only to score fidelity.

**Producer/consumer:**

- **A writes:** `rooms`, `keyframes`, `items[].{id,label,anchor,size,seen_at,confidence}`, `fidelity`, and in Part 2 `items[].status`. Emits `reconstruct`/`loop`/`diff` events.
- **B writes:** `items[].price`. Emits `pricing`/`claim` events. Reads `anchor`/`size` to place boxes and tags, `status` to color them.
- Until A's real `scene.json` exists, **B builds against `/fixtures/scene.sample.json`** (hand-write one with ~10 items). This is what makes the two tracks independent.

## 6. Repo structure (disjoint dirs → no merge conflicts)

```
/contracts     shared  scene schema, event schema, ground_truth schema (frozen Hour 0)
/sandbox       shared  MA provisioning, Credentials API, event-stream helper (Hour 0, pair)
/fixtures      shared  demo before-video (compressed), scene.sample.json, events.sample.jsonl, ground_truth.json
/pipeline      A       orchestrator/, reconstruct/, loop/, fidelity/
/diff          A       (Part 2) after-video alignment + stolen/moved classifier
/viewer        B       3D viewer, price tags, Activity panel
/agents        B       pricing/ , claim/ (Part 2)
/integration   both    thin glue, touched only at integration milestones
README.md      shared  includes "Built at the hackathon" section (see §10)
RUNS.md        shared  log of every test run (see rules below)
PLAN.md
```

Rules:
- You only edit your own dirs. `/contracts`, `/sandbox`, `/integration` changes are announced before committing.
- **Commit small and often** (at least every ~30 min). Timestamped history is our evidence that everything was built today.
- **Log every run in `RUNS.md`.** Any pipeline or agent run that produced output, failed, or was aborted gets a row, appended at the bottom (never edit someone else's row; add a note instead). A row has:
    - **#, time started, who** (A/B)
    - **What** was run, and on which input (video, scene)
    - **Command and settings:** model, iterations, sandbox or local, flags like `--skip-passes`
    - **Duration**
    - **Result:** fidelity as `found/35 (precision%)` for single pass → final, loop catches, items priced, or the failure
    - **Output** location (`out/`, `out/<run>/`, cache)
    - Anything surprising (a wrong fix, a duplicate, a bug found) goes under "Notes per run" with the commit that fixed it. Comparisons still to run go under "Open comparisons".
- Commit `RUNS.md` together with the run's output, so the row and the files share a timestamp.

## 7. THE GATE — Part 1 acceptance test

Part 1 "works" (and only then does anyone start Part 2) when **all four** pass on the real demo video:

1. Real before-video → an **explorable** 3D scene loads in the viewer.
2. The loop ran and the Activity panel shows **at least one caught-and-fixed reconstruction error**, and **measured** `fidelity.after_loop.recall > fidelity.single_pass.recall` against `ground_truth.json`.
3. Every item has a **price tag**, and the panel showed the pricing agent browsing for each.
4. **All** agent actions appeared in the Activity panel in real time.

If any fail at the gate time (§9), both people stay on Part 1. We do not trade a working Part 1 for a broken Part 2. **If there is still no caught-and-fixed error at 2:15, drop straight to fallback D (§11)** and protect the demo.

## 8. Work split (even, parallel)

### Agent A — the depth / the gate (reconstruction + loop + classification)

**Part 1**

- Video ingest (compressed 720p, 2–3 min) → Argon writes primitive scene JSON + keyframes.
- **Loop 1 (orchestrator-driven):** for each keyframe, render the scene from its viewpoint headlessly and extract the real frame at `t` with ffmpeg → Argon compares each pair and returns structured critiques `{item_or_wall, problem, fix}` → apply fixes (add / move / resize / remove) → re-render → repeat until zero critiques or 4 iterations. Emit a `loop` event per critique and per fix.
- **Completeness pass:** Argon re-watches the video in short segments with the current item list ("what's visible 1:00–1:30 that isn't listed?"). **Precision pass:** for each item, check its `seen_at` frame actually shows it; remove hallucinations.
- **Fidelity:** match `items[]` against `ground_truth.json` → recall + precision for single pass and after loop. These are the only numbers we show judges.
- Write `scene.json`.

**Part 2 (after gate)**

- After-video ingest → align to baseline scene.
- Per-item classify **present / moved / stolen**: an item seen in a new position = `moved` (do nothing); an item absent from the whole after-walkthrough (occlusion-checked) = `stolen`. Re-examine ambiguous regions until the stolen list stabilizes. Emit `diff` events.
- Write `items[].status`; produce the stolen list.

### Agent B — the breadth / the surface (viewer + agents + visible actions)

**Part 1**

- 3D viewer loading `scene.json` (Three.js; walls + labeled boxes; orbit controls; tags as CSS2D/HTML overlays anchored to `items[].anchor`). 3D scene is the centerpiece.
- **Activity panel** tailing `events.jsonl` (SSE or poll a file endpoint — simplest robust option).
- **Pricing agent** (Managed Agents browser): for each item, search by **like kind and quality** (e.g. "gray 2-seat fabric loveseat") — video rarely shows exact model names — and record a price **range** from 2–3 retailers with sources. Write `items[].price`, emit `pricing` events. Build against `scene.sample.json` first. **Cache all results before judging**; retailer sites often block bots.
- Price-tag UI: tag per item at its anchor, showing label + price range + source; click → evidence frame at `seen_at`.

**Part 2 (after gate)**

- After-flow UI: upload "after" video, show per-item status (moved = neutral, stolen = red) on the scene.
- Stolen-items list UI.
- **Claim agent** (Managed Agents browser + Credentials API): fill a **mock** claim form from the stolen list, field by field, visibly; emit `claim` events. Demo uses a mock/sandbox form — never fire a real claim.

### Shared, Hour 0 (pair for ~30–45 min)

- **Smoke tests first (by 11:00):**
    - Give Argon a 2-min clip of the demo room → does it produce a recognizable layout + item list? If not, decide on fallback now, not at 2:15.
    - Confirm the `agent_config` model question (§4) and pick the brain/hands split.
    - Confirm a managed-agent sandbox can run headless Chromium + ffmpeg.
- Freeze `/contracts` (all three schemas above).
- Stand up `/sandbox`: MA provisioning helper, Credentials API + allowlist config, and the `events.jsonl` append helper every agent imports.
- **Capture the demo input now:** film the before-video of the actual room today, **720p, 2–3 minutes**, slow steady pan (large files over venue Wi-Fi are a failure point). Pick a room with one **tricky object** (partially occluded / reflective / behind another item) so the single pass reliably errs and the loop has something to catch. Choose a **cleanly-priceable, clearly-visible product** to remove for the "after."
- **Hand-count `ground_truth.json`** for the demo room (~15 min).
- Write `scene.sample.json` + `events.sample.jsonl` fixtures so B can start immediately.

## 9. Timeline (10:30–5:00, submission due 5:00)

| Time        | A                                                                    | B                                                            | Joint                                                                                                              |
| ----------- | -------------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| 10:30–11:00 | —                                                                    | —                                                            | **Pair:** smoke tests (Argon on 2-min clip, `agent_config` model, sandbox Chromium/ffmpeg), film demo video, ground truth |
| 11:00–11:15 | —                                                                    | —                                                            | **Pair:** freeze contracts, stand up `/sandbox`, write fixtures                                                    |
| 11:15–1:30  | reconstruct + Loop 1 (catch 1 error) + completeness/precision + fidelity | viewer + Activity panel + pricing agent + tags (on fixtures) | —                                                                                                                  |
| 1:30–2:15   | —                                                                    | —                                                            | **Part 1 integration + run §7 acceptance test**                                                                    |
| **2:15**    | —                                                                    | —                                                            | **GATE DECISION.** Pass → split to Part 2. Fail/shaky → both stay on Part 1. No caught error → fallback D         |
| 2:15–3:30   | after-diff classifier (stolen/moved) + stolen list                   | after-UI + claim agent + visible claim fill                  | —                                                                                                                  |
| 3:30–3:45   | —                                                                    | —                                                            | Part 2 integration + test (cut it if it isn't working)                                                             |
| 3:45–4:00   | —                                                                    | —                                                            | **Record the 1-min video** — whether or not Part 2 works                                                           |
| 4:00–4:15   | —                                                                    | —                                                            | **Submission checklist:** repo public, README "Built at the hackathon" section, all members added, video link works |
| 4:15–5:00   | —                                                                    | —                                                            | **Demo prep:** pre-run reconstruction on demo room, confirm the caught-error moment, cache prices, rehearse, buffer |

**Protect above all:** the visible loop-catch (A, by 1:30) and the Activity panel (B, by 1:30). If only one thing works today, it's Part 1 with a visible caught error.

## 10. Submission + demo (1-min video, then 3-min live)

**README "Built at the hackathon" section (required):** the rules disqualify teams that don't clearly identify what they built during the event. List every component built today (orchestrator, reconstruct, loop, fidelity scoring, viewer, Activity panel, pricing agent, claim agent) and name any third-party libraries used (Three.js, ffmpeg, etc.). Point judges to the commit history.

- **1-min video (pre-recorded by 4:00, controlled):** the emotional story + full happy path, framed as **disaster** (wildfire, whole home, the stakes you can't stage live). Shows the complete flow end to end.
- **3-min live:** proof it's real. Pre-run the full reconstruction during setup, but **run at least one loop iteration live** — 50% of the score is "runs live with real inputs." Live sequence:
    1. (20s) The problem + "here's my before video of this room."
    2. (45s) **The loop catch** — rendered view vs real frame, Argon flags the missing item, scene updates live, then the **measured** scorecard: "single pass found X of N items; after the loop, Y of N" (from `fidelity`, scored against our hand count). _This is the moment that wins; everything else can be partially staged._
    3. (40s) Price tags in 3D; Activity panel shows the pricing agent browsing (cached fallback ready).
    4. (40s) Remove the item → "after" video → it classifies stolen vs moved → stolen list (Part 2; skip cleanly if it failed the gate).
    5. (20s) Claim-file button → mock form fills field-by-field in the panel; note the Credentials API keeps the portal login out of the model and off-network.

**Be ready for:** "How did you compute fidelity?" → recall/precision of detected items vs a hand-counted ground truth of the room.

## 11. Fallback ladder (decide by 11:00 smoke test, re-check at the gate)

- **A.** Argon primitive scene JSON (walls + labeled boxes) with Argon-estimated keyframes — **the plan.**
- **B.** Fewer keyframes (3–4) and one room only; loop compares only item presence, not wall geometry.
- **C.** Per-frame 2.5D: items placed on a single floor plane, still rendered in 3D for the loop.
- **D.** Demo-save: frame-anchored inventory on a 2D floor plan — still shows the completeness loop + pricing + claim.

## 12. Top risks

- **Argon's layout/item read too weak** for the loop to converge, or accidentally perfect (no error to catch). Mitigation: 11:00 smoke test; pick the tricky-object room; confirm the caught error exists in the pre-run.
- **Keyframe viewpoints too inaccurate** to compare renders with real frames. Mitigation: film on a slow, simple path; compare item presence and rough position, not pixels; drop to fallback B.
- **Argon not available inside managed agents.** Mitigation: brain/hands split (§4), decided by 11:00.
- **Live browser automation** (pricing/claim) flaking on stage. Mitigation: pre-fetch prices and cache; mock claim form; the Activity panel can replay cached events if a live call stalls.
- **Video upload over venue Wi-Fi.** Mitigation: 720p, 2–3 min, uploaded and cached during setup.
- **Part 2 eating Part 1's polish time.** Mitigation: the §7 gate is hard — Part 1 ships first, always.
- **Missing the 5:00 deadline for video/submission.** Mitigation: video recorded by 4:00, checklist done by 4:15.
