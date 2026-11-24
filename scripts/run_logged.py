#!/usr/bin/env python3
"""Start the real workbench with private per-run JSONL and terminal logs."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8503)
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runtime/logs")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"127.0.0.1:{args.port} is already occupied; choose another --port")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from src.config import Settings
    from src.run_logging import build_id, clean_text, version_fingerprints

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    try:
        settings = Settings.from_env(env)
    except ValueError:
        parser.error("invalid configuration; inspect .env (secret values are not printed)")
    if not (ROOT / "pi-runtime/node_modules/.bin/tsx").exists():
        parser.error("Pi dependencies missing; run: npm ci --prefix pi-runtime")
    if not (ROOT / "frontend/dist/index.html").exists():
        parser.error(
            "React build missing; run: npm ci --prefix frontend && npm run build --prefix frontend"
        )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    directory = args.log_dir.expanduser().resolve() / run_id
    directory.mkdir(parents=True, mode=0o700)
    env.update(RUN_LOG_DIR=str(directory), RUN_LOG_ID=run_id)
    url = f"http://127.0.0.1:{args.port}"
    meta = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "backend": "pi",
        "frontend": "react",
        "build": build_id(),
        "versions": version_fingerprints(),
        "launcher_pid": os.getpid(),
        "model": settings.model,
        "database": str(settings.database_path),
        "pi_directory": str(settings.pi_runtime_dir),
        "model_configured": settings.model_configured,
        "thinking_mode": settings.thinking_mode,
        "limits": {
            key: getattr(settings, key)
            for key in (
                "max_model_attempts",
                "max_tool_calls",
                "turn_timeout_seconds",
                "request_timeout_seconds",
                "max_reported_tokens",
                "max_output_tokens",
            )
        },
        "events": str(directory / "events.jsonl"),
        "console": str(directory / "console.log"),
    }
    with os.fdopen(
        os.open(directory / "run.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as handle:
        handle.write(clean_text(json.dumps(meta, ensure_ascii=False, indent=2)) + "\n")
    (directory / "events.jsonl").touch(mode=0o600)
    print(
        f"\nOpen: {url}\nRuntime: Pi SDK | Build: {meta['build']}\nLogs: {directory}",
        flush=True,
    )
    print(
        "All active conversations use Pi. Pre-Pi history is read-only. Ctrl+C stops this server.\n",
        flush=True,
    )
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )

    meta["server_pid"] = process.pid
    (directory / "run.json").write_text(
        clean_text(json.dumps(meta, ensure_ascii=False, indent=2)) + "\n"
    )

    def stop(_signal=None, _frame=None):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    try:
        with os.fdopen(
            os.open(directory / "console.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as output:
            for line in process.stdout:
                safe = clean_text(line)
                print(safe, end="", flush=True)
                output.write(safe)
                output.flush()
        return process.wait()
    finally:
        stop()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        process.stdout.close()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        meta.update(ended_at=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode)
        (directory / "run.json").write_text(
            clean_text(json.dumps(meta, ensure_ascii=False, indent=2)) + "\n"
        )
        print(f"\nLogs saved: {directory}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
