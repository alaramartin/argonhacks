"""Score a scene's items against the hand-counted ground truth.

Deterministic on purpose: when a judge asks how fidelity was computed, the answer
is "label matching against our hand count", and fidelity.json shows every match.
"""

import re

from models import GroundTruth, Scene, Score

STOP = {
    "a", "an", "the", "of", "on", "in", "with", "and", "or", "to", "for", "near", "small", "large", "big", "medium",
    "tall", "short", "black", "white", "gray", "grey", "red", "blue", "green", "yellow", "brown", "beige", "silver",
    "gold", "pink", "purple", "orange", "dark", "light", "wood", "wooden", "metal", "fabric", "plastic", "glass",
    "leather", "inch", "in", "mounted", "wall", "style", "modern", "set",
}
MATCH_THRESHOLD = 0.5


def _tokens(s: str) -> set[str]:
    words = re.findall(r"[a-z]+", s.lower())
    out = set()
    for w in words:
        if w in STOP or len(w) < 2:
            continue
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
        out.add(w)
    return out


def _similarity(label: str, names: list[str]) -> float:
    lt = _tokens(label)
    best = 0.0
    for name in names:
        nt = _tokens(name)
        if not nt or not lt:
            continue
        # share of the ground-truth name the label covers; "floor lamp" vs "tall black floor lamp" = 1.0
        best = max(best, len(lt & nt) / len(nt))
    return best


def match(scene: Scene, gt: GroundTruth) -> list[tuple[str, str, float]]:
    """Greedy one-to-one matching, best pairs first. Returns (item_id, gt_id, score)."""
    pairs = []
    for item in scene.items:
        for g in gt.items:
            s = _similarity(item.label, [g.label, *g.aliases])
            if s >= MATCH_THRESHOLD:
                pairs.append((s, item.id, g.id))
    pairs.sort(reverse=True)
    used_items, used_gt, out = set(), set(), []
    for s, iid, gid in pairs:
        if iid in used_items or gid in used_gt:
            continue
        used_items.add(iid)
        used_gt.add(gid)
        out.append((iid, gid, round(s, 2)))
    return out


def score(scene: Scene, gt: GroundTruth) -> tuple[Score, dict]:
    matches = match(scene, gt)
    n_gt, n_items, n_match = len(gt.items), len(scene.items), len(matches)
    result = Score(
        recall=round(n_match / n_gt, 3) if n_gt else 0.0,
        precision=round(n_match / n_items, 3) if n_items else 0.0,
    )
    matched_items = {m[0] for m in matches}
    matched_gt = {m[1] for m in matches}
    labels = {i.id: i.label for i in scene.items}
    gt_labels = {g.id: g.label for g in gt.items}
    report = {
        "matches": [{"item": i, "item_label": labels[i], "gt": g, "gt_label": gt_labels[g], "score": s} for i, g, s in matches],
        "missed": [{"gt": g.id, "label": g.label} for g in gt.items if g.id not in matched_gt],
        "extra": [{"item": i.id, "label": i.label} for i in scene.items if i.id not in matched_items],
    }
    return result, report
