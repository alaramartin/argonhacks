# PLAN.md — Proof of Loss

**Gemini 4 Argon Hackathon · Cerebral Valley × Google DeepMind · Oct 25 2026**
Target: **Problem Statement 2 (More Dimensions)** + **Best Use of Managed Agents ($5k)** (details in hackathon.md)

---

## 1. One-liner

After a disaster — fire, flood, theft — you have to list everything you owned, from memory, to file an insurance claim ("proof of loss"). Walk your space once on video; we build a **verified** explorable 3D inventory, price every item by actually browsing retailers, and auto-file the claim. "Verified" is the whole point: a single-pass 3D reconstruction that hallucinates an item is fraud, one that misses an item is underpayment — so we render the reconstruction back along the original camera path and correct it against the real footage until it matches.

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
    - `Searching price: "KitchenAid Artisan mixer" → $449 (williams-sonoma.com)`
    - `Loop iter 3: rendered frame missing TV present in real frame → adding`
    - `Filling claim field: item_description = "LG 55\" OLED C4"`

If an action isn't visible in the panel, it doesn't count. Build the panel early and wire every agent into it from the start.

## 4. Architecture

```
video ─▶ [A] reconstruct (Argon single-pass 3D) ─▶ scene draft
                       │
                       ▼
              [A] Loop 1: render-along-path ─▶ Argon critique vs real frames ─▶ fix ─▶ re-render
                       │ (until fidelity threshold)
                       ▼
                 scene.json (geometry + items[] + camera_path + fidelity)
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

All agent execution (reconstruction loop, pricing, claim) runs in **Managed Agents sandboxes** — one API call provisions a Google-hosted Linux sandbox; pass `"model": "gemini-4-argon"` in `agent_config`. Credentials (insurance portal login) go through the **Credentials API**: injected at the proxy, never in model context, never off the network allowlist. _(Confirm exact SDK signatures in the Managed Agents docs — don't take these as verbatim.)_

## 5. Interface contract — AGREE THIS FIRST (Hour 0), then never fight over it

Parallelism depends entirely on both of these being frozen early. Commit them to `/contracts` before splitting.

**`scene.json`**

```json
{
    "geometry": "scene.glb", // or splat / scene-json
    "camera_path": [
        {
            "frame": 0,
            "pose": [
                /*4x4*/
            ]
        }
    ],
    "items": [
        {
            "id": "item_003",
            "label": "KitchenAid Artisan mixer",
            "anchor": { "x": 1.2, "y": 0.0, "z": -0.8 },
            "confidence": 0.91,
            "price": {
                "amount": 449.0,
                "currency": "USD",
                "source_url": "..."
            }, // B writes
            "status": "present" // A writes in Part 2: present|moved|stolen
        }
    ],
    "fidelity": { "single_pass": 0.61, "after_loop": 0.93, "iterations": 4 }
}
```

**`events.jsonl`** (one JSON object per line, append-only)

```json
{
    "ts": "10:32:14",
    "agent": "pricing",
    "action": "Searching price: KitchenAid Artisan mixer",
    "status": "ok",
    "payload": { "amount": 449.0 }
}
```

**Producer/consumer:**

- **A writes:** `geometry`, `camera_path`, `items[].{id,label,anchor,confidence}`, `fidelity`, and in Part 2 `items[].status`. Emits `reconstruct`/`loop`/`diff` events.
- **B writes:** `items[].price`. Emits `pricing`/`claim` events. Reads `anchor` to place tags, `status` to color them.
- Until A's real `scene.json` exists, **B builds against `/fixtures/scene.sample.json`** (hand-write one with ~10 items). This is what makes the two tracks independent.

## 6. Repo structure (disjoint dirs → no merge conflicts)

```
/contracts     shared  scene schema, event schema (frozen Hour 0)
/sandbox       shared  MA provisioning, Credentials API, event-stream helper (Hour 0, pair)
/fixtures      shared  sample before-video, scene.sample.json, events.sample.jsonl
/pipeline      A       reconstruct/, loop/, pose/, fidelity/
/diff          A       (Part 2) after-video alignment + stolen/moved classifier
/viewer        B       3D viewer, price tags, Activity panel
/agents        B       pricing/ , claim/ (Part 2)
/integration   both    thin glue, touched only at integration milestones
PLAN.md
```

Rule: you only edit your own dirs. `/contracts`, `/sandbox`, `/integration` changes are announced before committing.

## 7. THE GATE — Part 1 acceptance test

Part 1 "works" (and only then does anyone start Part 2) when **all four** pass on the real demo video:

1. Real before-video → an **explorable** 3D scene loads in the viewer.
2. The loop ran and the Activity panel shows **at least one caught-and-fixed reconstruction error**, and `fidelity.after_loop > fidelity.single_pass`.
3. Every item has a **price tag**, and the panel showed the pricing agent browsing for each.
4. **All** agent actions appeared in the Activity panel in real time.

If any fail at the gate time (§9), both people stay on Part 1. We do not trade a working Part 1 for a broken Part 2.

## 8. Work split (even, parallel)

### Agent A — the depth / the gate (reconstruction + loop + classification)

**Part 1**

- Video ingest → Argon single-pass 3D generation → `geometry`.
- Camera-pose recovery (COLMAP or light SLAM; fallback: film the demo video on a simple known path).
- **Loop 1:** render scene along `camera_path` headlessly → Argon compares each rendered frame to the real frame → edits the scene (add/move/recolor) → re-render → repeat to threshold. Emit a `loop` event per critique/fix.
- Compute `fidelity` (item-presence accuracy or mean rendered-vs-real frame similarity), single-pass vs after-loop.
- Write `scene.json`.

**Part 2 (after gate)**

- After-video ingest → align to baseline scene.
- Per-item classify **present / moved / stolen**: an item seen in a new position = `moved` (do nothing); an item absent from the whole after-walkthrough (occlusion-checked) = `stolen`. Re-examine ambiguous regions until the stolen list stabilizes. Emit `diff` events.
- Write `items[].status`; produce the stolen list.

### Agent B — the breadth / the surface (viewer + agents + visible actions)

**Part 1**

- 3D viewer loading `scene.json` geometry, orbit/walk controls (Three.js suggested; tags as CSS2D/HTML overlays anchored to `items[].anchor`).
- **Activity panel** tailing `events.jsonl` (SSE or poll a file endpoint — simplest robust option).
- **Pricing agent** (Managed Agents browser): for each `items[].label`, browse retailers, pick a replacement price, write `items[].price`, emit `pricing` events. Build against `scene.sample.json` first.
- Price-tag UI: tag per item at its anchor, showing label + price + source.

**Part 2 (after gate)**

- After-flow UI: upload "after" video, show per-item status (moved = neutral, stolen = red) on the scene.
- Stolen-items list UI.
- **Claim agent** (Managed Agents browser + Credentials API): fill a **mock** claim form from the stolen list, field by field, visibly; emit `claim` events. Demo uses a mock/sandbox form — never fire a real claim.

### Shared, Hour 0 (pair for ~45 min)

- Freeze `/contracts` (both schemas above).
- Stand up `/sandbox`: MA provisioning helper, Credentials API + allowlist config, and the `events.jsonl` append helper every agent imports.
- **Capture the demo input now:** film the before-video of the actual room today. Pick a room with one **tricky object** (partially occluded / reflective / behind another item) so the single pass reliably errs and the loop has something to catch. Remove a **cleanly-priceable, clearly-visible product** (a named monitor / mixer / bike) for the "after."
- Write `scene.sample.json` + `events.sample.jsonl` fixtures so B can start immediately.

## 9. Timeline (10:30–5:00, demo at 5:00)

| Time        | A                                                      | B                                                            | Joint                                                                                                 |
| ----------- | ------------------------------------------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| 10:30–11:15 | —                                                      | —                                                            | **Pair:** freeze contracts, stand up `/sandbox`, capture demo videos, write fixtures                  |
| 11:15–1:30  | reconstruct + pose + Loop 1 (catch 1 error) + fidelity | viewer + Activity panel + pricing agent + tags (on fixtures) | —                                                                                                     |
| 1:30–2:15   | —                                                      | —                                                            | **Part 1 integration + run §7 acceptance test**                                                       |
| **2:15**    | —                                                      | —                                                            | **GATE DECISION.** Pass → split to Part 2. Fail/shaky → both stay on Part 1                           |
| 2:15–3:45   | after-diff classifier (stolen/moved) + stolen list     | after-UI + claim agent + visible claim fill                  | —                                                                                                     |
| 3:45–4:15   | —                                                      | —                                                            | Part 2 integration + test                                                                             |
| 4:15–5:00   | —                                                      | —                                                            | **Demo prep:** pre-run reconstruction on demo room, confirm the caught-error moment, rehearse, buffer |

**Protect above all:** the visible loop-catch (A, by 1:30) and the Activity panel (B, by 1:30). If only one thing works today, it's Part 1 with a visible caught error.

## 10. Submission + demo (1-min video, then 3-min live)

- **1-min video (pre-recorded, controlled):** the emotional story + full happy path, framed as **disaster** (wildfire, whole home, the stakes you can't stage live). Shows the complete flow end to end.
- **3-min live:** proof it's real. Pre-run the reconstruction on the demo room during setup (don't wait for it live). Live sequence:
    1. (20s) The problem + "here's my before video of this room."
    2. (45s) **The loop catch** — rendered vs real frame, Argon flags the missing item, scene updates, `fidelity 0.61 → 0.93`. _This is the moment that wins; everything else can be partially staged._
    3. (40s) Price tags in 3D; Activity panel shows the pricing agent browsing.
    4. (40s) Remove the item → "after" video → it classifies stolen vs moved → stolen list (Part 2; skip cleanly if it failed the gate).
    5. (20s) Claim-file button → mock form fills field-by-field in the panel; note the Credentials API keeps the portal login out of the model and off-network.

## 11. Fallback ladder (3D fidelity; decide by the gate)

- **A.** Argon single-pass generative 3D (try first — lowest build cost, strongest PS2 story).
- **B.** Photogrammetry / Gaussian splat (COLMAP + splat) for faithful geometry; Argon does detection + the loop on top.
- **C.** Per-frame depth → navigable 2.5D (still renderable for the loop).
- **D.** Demo-save: frame-anchored inventory on a 2D floor plan — still shows loop + pricing + claim.

## 12. Top risks

- **Single-pass 3D fidelity** too low for the loop to converge, or accidentally too high (no error to catch). Mitigation: pick the tricky-object room; confirm the caught error exists in the pre-run.
- **Camera-pose recovery** blocking the render-along-path. Mitigation: film on a simple known path; fall back to a sparse keyframe comparison if full pose fails.
- **Live browser automation** (pricing/claim) flaking on stage. Mitigation: pre-fetch prices and cache; mock claim form; the Activity panel can replay cached events if a live call stalls.
- **Part 2 eating Part 1's polish time.** Mitigation: the §7 gate is hard — Part 1 ships first, always.
