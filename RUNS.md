# RUNS.md — test run log

Every pipeline or agent run that produced output, failed, or taught us something gets a row here. This is our evidence for judges ("how do you know it works?") and for the "Built at the hackathon" section. How to log a run is in PLAN.md §6.

**Video:** `fixtures/before.mp4` is the lobby before-walkthrough (120.8 s, not committed). **Ground truth:** `fixtures/ground_truth.json`, 35 items.
**Scores:** recall = ground-truth items we found ÷ 35. Precision = our listed items that match the ground truth ÷ items listed. See `pipeline/README.md` for the matching rule.

## Log

| # | Time | Who | What | Command / settings | Duration | Result | Output |
|---|------|-----|------|--------------------|----------|--------|--------|
| 1 | 11:58 | B | Pricing, 1 item (sample scene), live | `price.py --only item_003` · flash | 3 min | **Failed** at the last step: agent browsed 4 retailers, but the parser couldn't read the streamed reply | — |
| 2 | 12:01 | B | Same, after the fix | `price.py --only item_003` · flash | 1.5 min | Stand mixer → $300–$500 (Walmart, Target, Best Buy) | cache |
| 3 | 12:14 | B | Parallel pricing test, 4 sample items (1 cached) | `price.py --only …` · 3 sandboxes · flash | 4.5 min | 4/4 priced | cache |
| 4 | 12:28 | A | **Full Part 1 pipeline, local loop** | `orchestrator.py run --video ../fixtures/before.mp4` · gemini-4-argon · 4 loop iterations + completeness + precision | 14.5 min | Single pass **30/35 (97%)** → final **34/35 (87%)**, 39 items, 17 loop catches | `out/` (published) |
| 5 | 12:43 | B | Rerun of #4 on B's machine (integration check) | same as #4 | — | **Aborted**: no API key found (pipeline reads `pipeline/.env`, agents read root `.env`); stopped the retry because it writes over the published `out/` | — |
| 6 | 12:44 | A | Loop in a Managed Agents sandbox | `run --sandbox` · gemini-4-argon in Antigravity agent · 1 loop iteration, reuses #4's single pass, no passes | 5.5 min | Single pass 30/35 (97%) → **31/35 (94%)**, 33 items, 6 critiques | `out/sandbox_demo/` |
| 7 | 12:48 | B | **Price every item in #4's scene** | `price.py --sandboxes 8` · flash | 11 min | **39/39 priced**, all with sources, 0 failures. Total $7,426–$11,971 | `out/scene.json`, cache |
| 8 | 12:51 | A | **Rerun of #4 with the Part 1 fixes** (evidence check before removals `58b40cd`, duplicate check `dd01544`, size rule `f42bc8f`) | same as #4 | 12.5 min | Single pass **30/35 (94%)** → final **34/35 (90%)**, 38 items. Loop kept the iMac, caught the QR code stand, split both small vases out | `out/final2/` (not published, see notes) |
| 9 | 13:08 | B | **Price #8's published scene** (35 unpriced, labels changed so none cached) | `price.py --sandboxes 8` · flash | 13 min | 34/35, 1 failure (bar stool: malformed URL crashed the host filter, fixed); rerun from cache → **38/38 priced**. Total $7,253–$11,612 | `out/scene.json`, cache |
| 10 | 13:13 | A | **Part 2 diff** (branch `part2`): after.mp4 vs #8's scene | `diff/diff.py --after ../fixtures/after.mp4` · gemini-4-argon, thinking high · survey + up to 3 re-check rounds | 4.8 min | All 3 changes right (laptop stolen, stool moved, trash can moved), **1 false theft** (small white vase) | `out/diff/` (overwritten by #11) |
| 11 | 13:19 | A | **Part 2 diff, rerun** after adding the evidence-image confirmation (`73679b7`) | same as #10 + confirm step | 3.2 min | Laptop **stolen** (confirmed on its image), stool **moved**, trash can present, **0 false thefts** | `out/diff/` |
| 12 | 13:25 | A | **Part 2 diff with generic prompt** (removed the room-specific examples "a slid laptop" and "six matching stools") | same as #11 | 4.9 min | Laptop **stolen**, stool **moved**, trash can moved, **0 false thefts**. The confirm step rejected the vase claim | `out/diff/` (prices from #9 copied in with `diff.py --reprice`) |

## Notes per run

**#1.** The final answer arrives in fragments across streamed events. Fixed by fetching the finished interaction with `interactions.get` once the stream ends and reading `output_text` (commit `9524443`).

**#4.** Loop iteration 3 removed `item_008` (iMac on the reception desk), judging it "not present" from keyframe 0 at 0:03, where the desk is too far away to make it out (`out/loop/iter3_kf0.jpg`). The completeness pass re-added it as `item_036` from 0:19. Fixed afterwards in `58b40cd` (the loop checks an item's evidence frames before removing it). The 5 extras include a duplicate check-in phone (`item_034`, `item_035`); the duplicate check (`dd01544`) landed after this run. Render camera alignment with real frames is loose on some keyframes, so pick a well-aligned pair for the demo's loop-catch moment.

**#6.** Not comparable to #4: one loop iteration and no completeness/precision passes. For a fair local-vs-sandbox comparison, run the local loop with `--skip-passes --max-iter 1` from the same single pass.

**#7.** 6 items have ranges wider than 2.5× because the agent mixed models or tiers, e.g. MacBook $700–$1,837 and longboard $144–$486. Candidate for the deferred "review" flag. Re-running is instant from `agents/pricing/cache.json` whenever A republishes the scene.

**#8.** Answers the open comparison "rerun #4 with the fixes": precision 87% → 90% at the same recall. Loop iteration 1 tried to remove the iMac again and was refused ("kept item_009 … its own evidence frames still show it"). The loop added "black countertop QR scanner stand" twice in one iteration at two spots, so one is an extra; the only miss is the black tablet or monitor (g09). Not published to `out/` because `out/scene.json` holds #7's 39 prices on #4's scene, and #8's labels differ, so publishing would drop most of them. Publishing #8 means rerunning pricing on it (cached labels are instant, new ones need a sandbox). **Published to `out/` at 13:07** (team decision): 3 of 38 prices carried over by exact label, 35 items need repricing (B). Publishing also replaced `out/events.jsonl`, so #7's pricing events are gone from it until pricing reruns.

**#9.** 10 items have ranges wider than 2.5× (e.g. folded kick scooter $128–$749, driftwood $27–$264). Claim value should be the median comparable listing, with wide ranges flagged for review (see PLAN/claim research).

**#10.** The false theft was `item_038`, a small vase that #8 had already listed as an extra (the ground truth has two small vases, #8 listed three). Its spot was "clearly in view" because the table was, but the vase sat behind another vase (`out/diff/stolen_item_038.jpg` in #10). Part 1 precision errors become false thefts in Part 2. Fixed in `73679b7`: every claim is confirmed on the BEFORE | AFTER image it will carry; on #10's two images Argon confirmed the laptop and rejected the vase.

**#11.** The re-check found the vase by itself this time, so the confirmation step only confirmed the laptop. Scored with `diff/score.py` against `fixtures/after_truth.json` (written by the person who removed and moved the items).

**#12.** Same verdicts with the room-specific examples gone from the survey prompt. The re-check called the small vase stolen again, and this time the confirmation step blocked it on its own evidence image ("all of the small white ceramic vases on top of the book are still present"). That's the first end-to-end run where the confirm step prevented a false theft (in #11 the re-check found the vase on its own).

## Open comparisons

- Local loop vs sandbox loop, same single pass, same iteration count (see #6).
