"""Thin wrapper around Gemini 4 Argon: video upload with caching, structured calls."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import TypeVar

from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel

_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

MODEL = os.environ.get("ARGON_MODEL", "gemini-4-argon")
T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()  # reads GEMINI_API_KEY
    return _client


def _cache_path(video: Path) -> Path:
    return video.parent / ".argon_uploads.json"


def upload_video(path: str | Path) -> types.File:
    """Upload once per file content; the venue Wi-Fi is too slow to re-upload every run."""
    path = Path(path)
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    cache_file = _cache_path(path)
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    if digest in cache:
        try:
            f = client().files.get(name=cache[digest])
            if f.state and f.state.name == "ACTIVE":
                return f
        except Exception:
            pass
    f = client().files.upload(file=str(path))
    while f.state and f.state.name == "PROCESSING":
        time.sleep(2)
        f = client().files.get(name=f.name)
    if f.state and f.state.name != "ACTIVE":
        raise RuntimeError(f"upload of {path} ended in state {f.state.name}")
    cache[digest] = f.name
    cache_file.write_text(json.dumps(cache, indent=2))
    return f


def video_part(f: types.File, start: float | None = None, end: float | None = None, fps: float | None = None) -> types.Part:
    meta = None
    if start is not None or end is not None or fps is not None:
        meta = types.VideoMetadata(
            start_offset=f"{start}s" if start is not None else None,
            end_offset=f"{end}s" if end is not None else None,
            fps=fps,
        )
    return types.Part(file_data=types.FileData(file_uri=f.uri, mime_type=f.mime_type), video_metadata=meta)


def image_part(path: str | Path) -> Image.Image:
    return Image.open(path)


def ask(contents: list, schema: type[T], system: str | None = None, thinking: str = "medium", retries: int = 3) -> T:
    # Argon ignores temperature/top_p and warns on them; thinking_level is its only knob.
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        system_instruction=system,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client().models.generate_content(model=MODEL, contents=contents, config=config)
            if isinstance(resp.parsed, schema):
                return resp.parsed
            return schema.model_validate_json(resp.text)
        except Exception as e:  # transient API errors and the odd malformed JSON
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Argon call failed after {retries} tries: {last}")
