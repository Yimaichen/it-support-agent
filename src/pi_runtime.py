"""One prompt per owned Pi process. No model loop or protocol replay lives here."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from typing import Callable
from uuid import uuid4

from src.config import PROJECT_ROOT
from src.run_logging import log_event


class PiRuntimeError(RuntimeError):
    pass


class PiRuntime:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.process = None
        self.selector = selectors.DefaultSelector()
        self.buffer = b""
        self.request_id = str(uuid4())
        self.started = 0.0
        self.ready = None

    def _send(self, packet: dict):
        packet = {"version": 1, "request_id": self.request_id, **packet}
        data = (json.dumps(packet, ensure_ascii=False) + "\n").encode()
        if len(data) > 2_000_000:
            raise PiRuntimeError("frame_too_large")
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def _next(self):
        while True:
            if b"\n" in self.buffer:
                frame, self.buffer = self.buffer.split(b"\n", 1)
                if len(frame) > 2_000_000:
                    raise PiRuntimeError("frame_too_large")
                try:
                    packet = json.loads(frame)
                except (ValueError, UnicodeError) as exc:
                    raise PiRuntimeError("invalid_frame") from exc
                if packet.get("version") != 1 or packet.get("request_id") != self.request_id:
                    raise PiRuntimeError("invalid_frame")
                return packet
            remaining = self.timeout - (time.monotonic() - self.started)
            if remaining <= 0:
                raise PiRuntimeError("turn_timeout")
            for key, _ in self.selector.select(min(remaining, 1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.data == "stdout":
                        raise PiRuntimeError("runtime_exited")
                elif key.data == "stdout":
                    self.buffer += chunk
                    if len(self.buffer) > 2_000_000 and b"\n" not in self.buffer:
                        raise PiRuntimeError("frame_too_large")
                else:
                    log_event("runtime_stderr", request_id=self.request_id, byte_count=len(chunk))
                # Raw provider stderr is never shown or persisted: it may contain private prose.
            if self.process.poll() is not None and not self.buffer:
                raise PiRuntimeError("runtime_exited")

    def open(self, init: dict, dispatch: Callable[[str, dict, str], dict]) -> dict:
        runtime = PROJECT_ROOT / "pi-runtime"
        executable = runtime / "node_modules/.bin/tsx"
        if not executable.exists():
            raise PiRuntimeError("runtime_not_installed")
        self.started = time.monotonic()
        # Do not pass the developer's PI_* configuration or provider secrets through env.
        allowed = (
            "PATH",
            "HOME",
            "TMPDIR",
            "LANG",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "NODE_EXTRA_CA_CERTS",
            "SSL_CERT_FILE",
        )
        env = {k: os.environ[k] for k in allowed if k in os.environ}
        # Trust the same configured CA bundle as Python without disabling TLS verification.
        if "NODE_EXTRA_CA_CERTS" not in env and env.get("SSL_CERT_FILE"):
            env["NODE_EXTRA_CA_CERTS"] = env["SSL_CERT_FILE"]
        self.process = subprocess.Popen(
            [str(executable), str(runtime / "bridge.ts")],
            cwd=runtime,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        log_event("runtime_started", request_id=self.request_id, runtime_pid=self.process.pid)
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self._send({"type": "init", **init})
        self.ready = self._receive("ready", dispatch)
        return self.ready

    def _receive(self, terminal, dispatch, notify=None):
        while True:
            packet = self._next()
            if packet["type"] == "tool_request":
                result = dispatch(packet["name"], packet["arguments"], packet["call_id"])
                self._send(
                    {"type": "tool_response", "call_id": packet["call_id"], "result": result}
                )
            elif packet["type"] == "progress":
                if notify:
                    notify(packet["event"], packet.get("details", {}))
            elif packet["type"] == "error":
                log_event("runtime_error", request_id=self.request_id, error=packet.get("error"))
                raise PiRuntimeError(packet.get("error", "runtime_interrupted"))
            elif packet["type"] == terminal:
                return packet

    def prompt(self, text, context, submission_id, dispatch, notify=None):
        self._send(
            {"type": "prompt", "text": text, "context": context, "submission_id": submission_id}
        )
        return self._receive("final", dispatch, notify)

    def close(self):
        if self.process:
            if self.process.poll() is None:
                try:
                    self._send({"type": "cancel"})
                    self.process.terminate()
                    self.process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    self.process.kill()
                    self.process.wait(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
        self.selector.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
