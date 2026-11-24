"""Validated configuration; constructing settings never calls a model or opens a database."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    data_dir: Path = PROJECT_ROOT / "data"
    database_path: Path = PROJECT_ROOT / "runtime/support.sqlite3"
    max_model_attempts: int = 12
    max_tool_calls: int = 24
    turn_timeout_seconds: float = 180
    request_timeout_seconds: float = 45
    max_additional_retries: int = 2
    thinking_mode: str = "disabled"
    reasoning_effort: str = "high"
    max_output_tokens: int = 8192
    max_reported_tokens: int = 200000
    pi_runtime_dir: Path = PROJECT_ROOT / "runtime/pi"

    def __post_init__(self) -> None:
        if self.thinking_mode not in {"disabled", "enabled"}:
            raise ValueError("THINKING_MODE must be disabled or enabled")
        if self.reasoning_effort not in {"low", "high"}:
            raise ValueError("REASONING_EFFORT must be low or high")
        if (
            not 256 <= self.max_output_tokens <= 32768
            or self.max_reported_tokens < self.max_output_tokens
        ):
            raise ValueError("invalid output/turn token limits")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("MODEL_BASE_URL must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MODEL_BASE_URL must not contain credentials, query or fragment")
        if not self.model.strip():
            raise ValueError("MODEL_NAME must not be empty")
        for name in (
            "max_model_attempts",
            "max_tool_calls",
            "turn_timeout_seconds",
            "request_timeout_seconds",
        ):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.max_additional_retries < 0:
            raise ValueError("max_additional_retries must not be negative")

    @property
    def model_configured(self) -> bool:
        return bool(self.api_key.strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def path(key: str, default: str) -> Path:
            value = Path(env.get(key, default)).expanduser()
            return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()

        return cls(
            api_key=env.get("DEEPSEEK_API_KEY", "").strip(),
            model=env.get("MODEL_NAME", "deepseek-flash"),
            base_url=env.get("MODEL_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            data_dir=path("DATA_DIR", "data"),
            database_path=path("DATABASE_PATH", "runtime/support.sqlite3"),
            max_model_attempts=int(env.get("MAX_MODEL_ATTEMPTS", "12")),
            max_tool_calls=int(env.get("MAX_TOOL_CALLS", "24")),
            turn_timeout_seconds=float(env.get("TURN_TIMEOUT_SECONDS", "180")),
            request_timeout_seconds=float(env.get("REQUEST_TIMEOUT_SECONDS", "45")),
            max_additional_retries=int(env.get("MAX_ADDITIONAL_RETRIES", "2")),
            thinking_mode=env.get("THINKING_MODE", "disabled"),
            reasoning_effort=env.get("REASONING_EFFORT", "high"),
            max_output_tokens=int(env.get("MAX_OUTPUT_TOKENS", "8192")),
            max_reported_tokens=int(env.get("MAX_REPORTED_TOKENS", "200000")),
            pi_runtime_dir=path("PI_RUNTIME_DIR", "runtime/pi"),
        )
