"""Score a diff result against fixtures/after_truth.json.

    cd pipeline && uv run python ../diff/score.py [--diff ../out/diff]

What matters for the claim: every removed item is called stolen (recall), and nothing else is (no false thefts).
Moves are scored separately; calling a moved item "present" is harmless, calling it "stolen" is not.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))

from fidelity import MATCH_THRESHOLD, _similarity  # noqa: E402


def score(scene: dict, truth: dict) -> dict:
    items = scene["items"]
    rows, claimed = [], set()
    for change in truth["changes"]:
        names = [change["label"], *change.get("aliases", [])]
        cands = sorted(((_similarity(i["label"], names), i) for i in items), key=lambda c: c[0], reverse=True)
        cands = [i for s, i in cands if s[0] >= MATCH_THRESHOLD]
        # several identical items (six stools): the change is right if any one of them got the expected status
        hit = next((i for i in cands if i.get("status") in change["expected"] and i["id"] not in claimed), None)
        pick = hit or (cands[0] if cands else None)
        if pick:
            claimed.add(pick["id"])
        rows.append({"change": change["label"], "expected": change["expected"],
                     "item": pick["id"] if pick else None, "status": pick.get("status") if pick else None,
                     "ok": hit is not None})
    should_be_stolen = {r["item"] for r, c in zip(rows, truth["changes"]) if c["expected"] == ["stolen"] and r["item"]}
    called_stolen = {i["id"] for i in items if i.get("status") == "stolen"}
    return {
        "changes": rows,
        "stolen_found": f"{len(should_be_stolen & called_stolen)}/{sum(1 for c in truth['changes'] if c['expected'] == ['stolen'])}",
        "false_thefts": sorted(called_stolen - should_be_stolen),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--diff", default=str(REPO / "out" / "diff"))
    p.add_argument("--truth", default=str(REPO / "fixtures" / "after_truth.json"))
    args = p.parse_args()
    scene = json.loads((Path(args.diff) / "scene.json").read_text())
    result = score(scene, json.loads(Path(args.truth).read_text()))
    labels = {i["id"]: i["label"] for i in scene["items"]}
    for r in result["changes"]:
        mark = "ok " if r["ok"] else "BAD"
        print(f"{mark} {r['change']:<14} expected {'/'.join(r['expected']):<15} got {r['status']} ({r['item']})")
    print(f"stolen found {result['stolen_found']}; false thefts: "
          + (", ".join(f"{i} {labels[i]}" for i in result["false_thefts"]) or "none"))
    (Path(args.diff) / "diff_score.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
