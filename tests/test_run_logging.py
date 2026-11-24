"""Opt-in logs preserve correlation and diagnostics without configured secrets."""

import json
import os
import socket
import stat
import subprocess
import sys

from src.config import PROJECT_ROOT
from src.run_logging import log_event
from tests.test_pi_agent import make


def test_structured_log_redacts_configured_key_and_private_fields(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUN_LOG_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("RUN_LOG_ID", "acceptance-1")
    key = 'opaque-credential-with-"quote'
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    log_event(
        "probe",
        session_id="session-1",
        text=f"server echoed {key}",
        arguments={"password": "do-not-save-this", "reasoning_content": "hidden-prose"},
        result={"data": ["useful diagnostic", {"chain_of_thought": "also-hidden"}]},
    )
    path = tmp_path / "private/events.jsonl"
    saved = path.read_text()
    terminal = capsys.readouterr().err
    for secret in (key, "do-not-save-this", "hidden-prose", "also-hidden"):
        assert secret not in saved + terminal
    row = json.loads(saved)
    assert row["session_id"] == "session-1" and row["run_id"] == "acceptance-1"
    assert row["result"]["data"][0] == "useful diagnostic"
    assert row["arguments"] == {"password": "[REDACTED_SECRET]"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_agent_logs_input_tool_result_replay_and_final_without_model_api(
    support_env, tmp_path, monkeypatch
):
    monkeypatch.setenv("RUN_LOG_DIR", str(tmp_path / "logs"))
    corpus, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            ("duplicate", "get_current_user", {}),
            ("duplicate", "get_current_user", {}),
        ],
    )
    result = agent.run(ctx.user_id, ctx.session_id, "请查看我的设备", submission_id="logged-submit")
    assert result["status"] == "completed"
    rows = [json.loads(line) for line in (tmp_path / "logs/events.jsonl").read_text().splitlines()]
    events = [row["event"] for row in rows]
    assert (
        events.count("tool_call")
        == events.count("tool_result")
        == events.count("tool_replayed")
        == 1
    )
    assert events[0] == "turn_started" and events[-1] == "turn_finished"
    assert all(row["session_id"] == ctx.session_id for row in rows)
    assert all(row["submission_id"] == "logged-submit" for row in rows)
    assert rows[-1]["result"]["reply"] == result["reply"]
    assert rows[0]["text"] == "请查看我的设备"


def test_busy_port_launcher_refuses_to_start_a_second_server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts/run_logged.py"),
                "--port",
                str(listener.getsockname()[1]),
                "--log-dir",
                str(tmp_path),
            ],
            env=dict(os.environ, DEEPSEEK_API_KEY="must-not-print"),
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode == 2 and "already occupied" in result.stderr
    assert "must-not-print" not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []
