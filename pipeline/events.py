"""Append-only event stream (contracts/event.schema.json) that the Activity panel tails."""

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_path = Path(os.environ.get("POL_EVENTS", Path(__file__).resolve().parent.parent / "out" / "events.jsonl"))


def set_path(path: str | Path) -> None:
    global _path
    _path = Path(path)


def emit(agent: str, action: str, status: str = "info", **payload) -> None:
    event = {"ts": datetime.now().strftime("%H:%M:%S"), "agent": agent, "action": action, "status": status}
    if payload:
        event["payload"] = payload
    line = json.dumps(event, default=str)
    with _lock:
        _path.parent.mkdir(parents=True, exist_ok=True)
        with _path.open("a") as f:
            f.write(line + "\n")
    print(f"[{event['ts']}] {agent:<11} {action}", file=sys.stderr)
