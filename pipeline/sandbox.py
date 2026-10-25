"""Managed Agents sandbox: Argon running in a Google-hosted Linux box (Antigravity agent).

One environment per run holds the video and our renderer. Each loop iteration uploads the
current scene, Argon renders and inspects the REAL | RENDER pairs there, and writes its
critiques to a file we download. Every command it runs is streamed to the Activity panel.
"""

import json
import time
from pathlib import Path

import argon
from events import emit

AGENT = "antigravity-preview-09-2026"
HERE = Path(__file__).resolve().parent
CODE = ["models.py", "render.py", "frames.py", "render_pairs.py"]


def _short(code: str, n: int = 90) -> str:
    line = " ".join(code.split())
    return line if len(line) <= n else line[: n - 1] + "…"


class Sandbox:
    def __init__(self, video: str | Path, state_file: str | Path):
        self.c = argon.client()
        self.state_file = Path(state_file)
        state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.env_id = state.get("env_id")
        if self.env_id:
            try:
                self.c.environments.get(self.env_id)
                emit("loop", f"Sandbox: reusing environment {self.env_id[:8]}")
                return
            except Exception:
                self.env_id = None
        env = self.c.environments.create()
        self.env_id = env.id
        emit("loop", f"Sandbox: provisioned Linux environment {self.env_id[:8]} (Managed Agents)", "ok")
        for name in CODE:
            self.upload(HERE / name, f"pol/{name}", "text/x-python")
        emit("loop", f"Sandbox: uploading walkthrough video ({Path(video).stat().st_size // 1_000_000} MB)")
        self.upload(video, "pol/video.mp4", "video/mp4")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"env_id": self.env_id}))

    def upload(self, local: str | Path, remote: str, mime: str) -> None:
        self.c.environments.files.upload(environment_id=self.env_id, path=remote, file=str(local),
                                         mime_type=mime, overwrite=True)

    def upload_text(self, text: str, remote: str, mime: str = "application/json") -> None:
        self.c.environments.files.upload(environment_id=self.env_id, path=remote, file=text.encode(),
                                         mime_type=mime, overwrite=True)

    def download(self, remote: str) -> bytes:
        return self.c.environments.files.download(environment_id=self.env_id, path=remote)

    def run(self, prompt: str, label: str, timeout: float = 900) -> str:
        """Run one agent task, streaming each step it takes as an event the moment it starts. Returns its final text."""
        stream = self.c.interactions.create(
            agent=AGENT,
            agent_config={"type": "antigravity", "model": argon.MODEL},
            environment={"environment_id": self.env_id},
            input=prompt,
            stream=True,
        )
        t0, text, status, interaction_id = time.time(), [], None, None
        for ev in stream:
            d = ev.model_dump(exclude_none=True) if hasattr(ev, "model_dump") else dict(ev)
            kind = d.get("event_type", "")
            if "interaction" in d and isinstance(d["interaction"], dict):
                interaction_id = d["interaction"].get("id", interaction_id)
                status = d["interaction"].get("status", status)
            elif kind == "interaction.status_update":
                status = d.get("status", status)
            elif kind == "step.start":
                step = d.get("step", {})
                if step.get("type") == "code_execution_call":
                    emit("loop", f"{label}: sandbox ran `{_short(step.get('arguments', {}).get('code', ''))}`")
                elif step.get("type") == "function_call":
                    args = step.get("arguments", {})
                    target = args.get("AbsolutePath") or args.get("DirectoryPath") or json.dumps(args)
                    emit("loop", f"{label}: sandbox {step.get('name', 'tool')} {_short(str(target), 70)}")
            elif kind == "step.delta" and d.get("delta", {}).get("type") == "text":
                text.append(d["delta"].get("text", ""))
            if time.time() - t0 > timeout:
                if interaction_id:
                    self.c.interactions.cancel(id=interaction_id)
                raise TimeoutError(f"sandbox task timed out after {timeout:.0f}s")
        if status != "completed":
            raise RuntimeError(f"sandbox task ended {status}")
        return "".join(text)
