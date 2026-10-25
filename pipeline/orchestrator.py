"""Drives Part 1 of the pipeline: reconstruct → loop → completeness → precision → fidelity → scene.json.

    uv run python orchestrator.py run --video ../fixtures/before.mp4 --gt ../fixtures/ground_truth.json
    uv run python orchestrator.py smoke --video clip.mp4        # single pass only, plus renders
    uv run python orchestrator.py score --gt ../fixtures/ground_truth.json   # re-score saved scenes

Every stage writes its scene to --out, so a later stage can be rerun with --from <stage>.
"""

import argparse
import os
import shutil
import json
from pathlib import Path

import events
import fidelity
import loop
import passes
import reconstruct
from events import emit
from models import Fidelity, GroundTruth, Scene
from render import render

REPO = Path(__file__).resolve().parent.parent
STAGES = ["reconstruct", "loop", "completeness", "precision"]


def _save(scene: Scene, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scene.dump(), indent=2))


def _load(path: Path) -> Scene:
    return Scene.model_validate_json(path.read_text())


def _score(out: Path, gt_path: Path, iterations: int | None = None) -> None:
    gt = GroundTruth.model_validate_json(gt_path.read_text())
    single = _load(out / "scene.single_pass.json")
    final = _load(out / "scene.json")
    s1, r1 = fidelity.score(single, gt)
    s2, r2 = fidelity.score(final, gt)
    prev = final.fidelity.iterations if final.fidelity else 0
    final.fidelity = Fidelity(ground_truth_count=len(gt.items), single_pass=s1, after_loop=s2,
                              iterations=iterations if iterations is not None else prev)
    _save(final, out / "scene.json")
    (out / "fidelity.json").write_text(json.dumps({"single_pass": r1, "after_loop": r2}, indent=2))
    # a rescore replaces the previous score lines instead of stacking under them
    ev = out / "events.jsonl"
    if ev.exists():
        lines = [l for l in ev.read_text().splitlines() if l and json.loads(l).get("agent") != "fidelity"]
        ev.write_text("".join(l + "\n" for l in lines))
    n = len(gt.items)
    emit("fidelity", f"Single pass found {round(s1.recall * n)} of {n} items (precision {s1.precision:.0%})", "info",
         recall=s1.recall, precision=s1.precision)
    emit("fidelity", f"After the loop: {round(s2.recall * n)} of {n} items (precision {s2.precision:.0%})",
         "ok" if s2.recall > s1.recall else "warn", recall=s2.recall, precision=s2.precision)


def cmd_run(args) -> None:
    out = Path(args.out)
    events.set_path(out / "events.jsonl")
    start = STAGES.index(args.from_stage)
    iterations = None

    if start <= 0:
        scene = reconstruct.run(args.video)
        _save(scene, out / "scene.single_pass.json")
        _save(scene, out / "scene.json")
    scene = _load(out / ("scene.single_pass.json" if start <= 1 else "scene.json"))

    if start <= 1:
        sb = None
        if args.sandbox:
            from sandbox import Sandbox
            sb = Sandbox(args.video, out / ".sandbox_env.json")
        scene, iterations = loop.run(scene, args.video, out, args.max_iter, sandbox=sb)
        _save(scene, out / "scene.after_loop1.json")
        _save(scene, out / "scene.json")
    if start <= 2 and not args.skip_passes:
        scene = passes.completeness(scene, args.video)
        _save(scene, out / "scene.json")
    if start <= 3 and not args.skip_passes:
        scene = passes.precision(scene, args.video, out)
        _save(scene, out / "scene.json")

    if args.gt:
        _score(out, Path(args.gt), iterations)
    emit("reconstruct", f"scene.json written: {len(scene.items)} items", "ok", path=str(out / "scene.json"))


def cmd_smoke(args) -> None:
    out = Path(args.out)
    events.set_path(out / "events.jsonl")
    scene = reconstruct.run(args.video)
    _save(scene, out / "scene.single_pass.json")
    for k, kf in enumerate(scene.keyframes):
        render(scene, kf).save(out / f"smoke_kf{k}.png")
    print(json.dumps({"rooms": len(scene.rooms), "keyframes": len(scene.keyframes),
                      "items": [i.label for i in scene.items]}, indent=2))


def cmd_score(args) -> None:
    out = Path(args.out)
    events.set_path(out / "events.jsonl")
    _score(out, Path(args.gt))
    print((out / "fidelity.json").read_text())


def cmd_publish(args) -> None:
    """Promote a finished run to out/, where the viewer reads it. Prices B already wrote are kept."""
    src, dst = Path(args.run).resolve(), REPO / "out"
    if src == dst:
        return
    prev_prices = {}
    if (dst / "scene.json").exists():
        prev = json.loads((dst / "scene.json").read_text())
        prev_prices = {i["label"]: i["price"] for i in prev.get("items", []) if i.get("price")}
    scene = json.loads((src / "scene.json").read_text())
    kept = 0
    for item in scene["items"]:
        if "price" not in item and item["label"] in prev_prices:
            item["price"] = prev_prices[item["label"]]
            kept += 1
    (dst / "scene.json").write_text(json.dumps(scene, indent=2))
    for name in ("scene.single_pass.json", "fidelity.json"):
        if (src / name).exists():
            shutil.copy(src / name, dst / name)
    for d in ("loop", "frames"):
        if (src / d).exists():
            shutil.rmtree(dst / d, ignore_errors=True)
            shutil.copytree(src / d, dst / d)
    old_prefix = os.path.relpath(src, REPO) + "/"
    lines = (src / "events.jsonl").read_text().replace(old_prefix, "out/")
    (dst / "events.jsonl").write_text(lines)
    print(f"published {src.name} → out/: {len(scene['items'])} items, {kept} prices carried over")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--video", required=True)
    r.add_argument("--gt", default=str(REPO / "fixtures" / "ground_truth.json"))
    r.add_argument("--out", default=str(REPO / "out"))
    r.add_argument("--from", dest="from_stage", choices=STAGES, default="reconstruct")
    r.add_argument("--max-iter", type=int, default=loop.MAX_ITERATIONS)
    r.add_argument("--skip-passes", action="store_true", help="loop only; skip completeness and precision")
    r.add_argument("--sandbox", action="store_true", help="run the loop's render-and-critique step in a Managed Agents sandbox")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("smoke")
    s.add_argument("--video", required=True)
    s.add_argument("--out", default=str(REPO / "out" / "smoke"))
    s.set_defaults(fn=cmd_smoke)

    sc = sub.add_parser("score")
    sc.add_argument("--gt", default=str(REPO / "fixtures" / "ground_truth.json"))
    sc.add_argument("--out", default=str(REPO / "out"))
    sc.set_defaults(fn=cmd_score)

    pb = sub.add_parser("publish")
    pb.add_argument("run", help="run directory, e.g. ../out/final")
    pb.set_defaults(fn=cmd_publish)

    args = p.parse_args()
    if getattr(args, "gt", None) and not Path(args.gt).exists():
        print(f"note: no ground truth at {args.gt}, skipping fidelity")
        args.gt = None
    args.fn(args)


if __name__ == "__main__":
    main()
