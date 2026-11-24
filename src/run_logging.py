"""Private opt-in diagnostic events. Log only whitelisted nonsecret configuration; never credentials or hidden reasoning."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT
from src.privacy import redact_text, sanitize

_LOCK = threading.Lock()


def version_fingerprints(root: Path = PROJECT_ROOT) -> dict:
    """Hash names and contents; never include .env, runtime records or secrets."""
    groups = {
        "code": [
            *sorted((root / "src").glob("*.py")),
            root / "pi-runtime/bridge.ts",
            *sorted((root / "scripts").glob("*.py")),
        ],
        "frontend": [
            root / "frontend/index.html",
            root / "frontend/vite.config.ts",
            root / "frontend/tsconfig.json",
            *sorted(p for p in (root / "frontend/src").rglob("*") if p.is_file()),
        ],
        "data": sorted(p for p in (root / "data").rglob("*") if p.is_file()),
        "dependencies": [
            root / name
            for name in (
                "pyproject.toml",
                "uv.lock",
                "pi-runtime/package.json",
                "pi-runtime/package-lock.json",
                "frontend/package.json",
                "frontend/package-lock.json",
            )
        ],
    }
    versions = {}
    for group, files in groups.items():
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(root)).encode() + b"\0")
            digest.update(path.read_bytes() if path.exists() else b"<missing>")
            digest.update(b"\0")
        versions[group] = digest.hexdigest()
    combined = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()
    return {**versions, "build": combined[:12]}


def build_id() -> str:
    return version_fingerprints()["build"]


def clean_text(text: str) -> str:
    # Pattern matching alone cannot identify an arbitrary provider key.
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "[REDACTED_SECRET]")
            text = text.replace(json.dumps(value, ensure_ascii=False)[1:-1], "[REDACTED_SECRET]")
    return redact_text(text)


def log_event(event: str, **details) -> None:
    directory = os.environ.get("RUN_LOG_DIR")
    if not directory:
        return
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": os.environ.get("RUN_LOG_ID", "manual"),
        "pid": os.getpid(),
        "event": event,
        **details,
    }
    # Structured filtering removes private protocol fields before JSON serialization.
    line = clean_text(json.dumps(sanitize(row), ensure_ascii=False, default=str))
    try:
        with _LOCK:
            root = Path(directory)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                root / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as output:
                output.write(line + "\n")
            print(line, file=sys.stderr, flush=True)
    except OSError:
        # An unavailable diagnostic destination must not roll back a valid business action.
        print("[run-log] Unable to write diagnostic event", file=sys.stderr, flush=True)
