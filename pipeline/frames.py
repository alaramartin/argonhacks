"""Real-frame extraction with ffmpeg."""

import json
import subprocess
from pathlib import Path


def duration(video: str | Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def size(video: str | Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation",
         "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    s = json.loads(out)["streams"][0]
    w, h = s["width"], s["height"]
    rotation = next((abs(int(d.get("rotation", 0))) for d in s.get("side_data_list", [])), 0)
    return (h, w) if rotation in (90, 270) else (w, h)


def extract(video: str | Path, t: float, out_dir: str | Path, height: int = 720) -> Path:
    """Frame at `t` seconds, scaled to `height`. Cached by timestamp."""
    out = Path(out_dir) / f"frame_{t:07.2f}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale=-2:{height}", "-q:v", "3", str(out)],
        check=True,
    )
    return out
