"""Render a REAL | RENDER pair for every keyframe of a scene. Runs locally or inside the sandbox.

    python render_pairs.py scene.json video.mp4 pairs/ [prefix]
"""

import sys
from pathlib import Path

import frames
from models import Scene
from render import render, side_by_side


def render_pairs(scene: Scene, video: str | Path, out_dir: str | Path, prefix: str = "") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = frames.size(video)
    width, height = int(width * 720 / height), 720
    paths = []
    for k, kf in enumerate(scene.keyframes):
        real = frames.extract(video, kf.t, out_dir.parent / "frames")
        pair = side_by_side(real, render(scene, kf, width, height), caption=f"keyframe {k} @ {kf.t:.1f}s")
        path = out_dir / f"{prefix}kf{k}.jpg"
        pair.save(path, quality=88)
        paths.append(path)
    return paths


if __name__ == "__main__":
    scene_path, video, out = sys.argv[1:4]
    prefix = sys.argv[4] if len(sys.argv) > 4 else ""
    for p in render_pairs(Scene.model_validate_json(Path(scene_path).read_text()), video, out, prefix):
        print(p)
