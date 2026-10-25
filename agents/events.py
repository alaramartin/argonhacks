"""Append-only event stream (contracts: events.jsonl). Every agent action the judges should see goes through emit()."""
import json
import os
import sys
import threading
import time
from pathlib import Path

# Windows consoles default to cp1252 and choke on the arrows/dashes in event text.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = Path(os.environ.get("POL_EVENTS", ROOT / "out" / "events.jsonl"))  # same file as pipeline/events.py


_lock = threading.Lock()  # pricing runs several sandboxes in threads


def emit(agent, action, status="info", payload=None):
    ev = {"ts": time.strftime("%H:%M:%S"), "agent": agent, "action": action, "status": status}
    if payload is not None:
        ev["payload"] = payload
    with _lock:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        print(f"[{ev['ts']}] {agent:<8} {action}", flush=True)
    return ev
