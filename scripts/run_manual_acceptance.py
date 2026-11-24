"""Live, synthetic acceptance conversations. Explicit --live; isolated storage only.

This executes the manual's texts, not a substitute for independent human judgment.
UI and process-fault cases require separate browser/fault evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.config import PROJECT_ROOT as ROOT
from src.config import Settings
from src.data import load_corpus
from src.diagnostics import reconcile
from src.pi_agent import PiAgent
from src.run_logging import clean_text, version_fingerprints
from src.storage import Store
from src.user_actions import UserAction


def manual_plan():
    text = (ROOT / "docs/MANUAL_ACCEPTANCE_ZH.md").read_text()
    parts = re.split(r"^### ([ABCD]\d{2})[^\n]*\n", text, flags=re.M)
    return {parts[i]: re.findall(r"^> (.+)$", parts[i + 1], re.M) for i in range(1, len(parts), 2)}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--ids",
        default="A01,A02,A03,A04,A05,B01,B02,B03,B04,B05,B06,B07,B08,B09,B10,B11,B12,C01,C02,C03,C05,C06",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for billed model calls")
    load_dotenv(ROOT / ".env")
    directory = args.run_dir.resolve()
    if directory == ROOT / "runtime" or not directory.is_relative_to(ROOT / "runtime/acceptance"):
        parser.error("run-dir must be a child of runtime/acceptance")
    directory.mkdir(parents=True, exist_ok=False)
    os.chmod(directory, 0o700)
    os.environ["RUN_LOG_DIR"] = str(directory)
    base = Settings.from_env()
    if not base.model_configured:
        parser.error("model credentials are not configured")
    settings = replace(
        base, database_path=directory / "support.sqlite3", pi_runtime_dir=directory / "pi"
    )
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "model": settings.model,
        "versions": version_fingerprints(),
        "mode": "live_scripted_manual_texts",
        "database": str(settings.database_path),
        "scenarios": args.ids.split(","),
    }
    (directory / "run.json").write_text(json.dumps(manifest, indent=2))
    corpus = load_corpus(settings.data_dir)
    store = Store(settings.database_path)
    store.import_resolutions(corpus.resolutions)
    user, other = corpus.employees[0].id, corpus.employees[1].id
    plan = manual_plan()
    results = []
    sessions = {}
    resolved = None

    def turn(scenario, session, index, text, identity=user, action=None):
        start = time.monotonic()
        submission = f"{scenario}-{index}"
        response = PiAgent(store, corpus, settings).run(
            identity, session, text, submission_id=submission, action=action
        )
        row = {
            "scenario": scenario,
            "session_id": session,
            "submission_id": submission,
            "input": text,
            "response": response,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        }
        results.append(row)
        with (directory / "transcripts.jsonl").open("a") as stream:
            stream.write(clean_text(json.dumps(row, ensure_ascii=False)) + "\n")
        (directory / "progress.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "turn": index,
                    "status": response["status"],
                    "completed_turns": len(results),
                    "elapsed_seconds": row["elapsed_seconds"],
                }
            )
        )
        return response

    try:
        for scenario in args.ids.split(","):
            identity = other if scenario in {"B09", "C02"} else user
            session = store.create_session(identity)["session_id"]
            sessions[scenario] = session
            if scenario == "C03":
                turn(scenario, session, 0, "VPN 每十分钟断，London，macOS 15，客户端5.0。")
            if scenario == "C05":
                turn(
                    scenario,
                    session,
                    0,
                    "VPN每十分钟断，London macOS15 客户端5.0。公司软件门户打不开，我拒绝继续排查，请交接。",
                )
            texts = plan.get(scenario, [])
            if scenario == "C06":
                texts = [
                    "Okta ACCOUNT_LOCKED，网络正常，我仍有MFA。请查资料并准备本地交接，不要通知任何人。"
                ]
            for index, text in enumerate(texts, 1):
                if scenario == "C02":
                    text = text.replace(
                        "<替换为员工 A 的会话 ID>", sessions.get("A03", "not-owned")
                    )
                if scenario == "B12" or (scenario == "B07" and index >= 4):
                    session = store.create_session(identity)["session_id"]
                if scenario == "C01" and index == 2 and resolved:
                    original_session, issue = resolved
                    turn(
                        scenario,
                        original_session,
                        "reopen",
                        "之前的VPN问题又出现了，我要重新调查。",
                        action=UserAction(kind="reopen", issue_id=issue),
                    )
                response = turn(scenario, session, index, text, identity)
                if response["status"] != "completed":
                    # Preserve first interruption. Do not repeatedly retry into a pass.
                    break
            if scenario == "A03" and response.get("issue_id"):
                issue = response["issue_id"]
                turn(
                    scenario,
                    session,
                    "confirm",
                    "我确认VPN已恢复。",
                    action=UserAction(kind="confirm", issue_id=issue),
                )
                resolved = session, issue
            if scenario == "B08":
                turn(
                    scenario,
                    session,
                    "release",
                    "补充：是Okta，错误ACCOUNT_LOCKED，我还有MFA，网络正常。",
                )
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["ending_versions"] = version_fingerprints()
        manifest["version_stable"] = manifest["versions"] == manifest["ending_versions"]
        manifest["turn_count"] = len(results)
        manifest["noncompleted"] = [
            {
                "scenario": r["scenario"],
                "submission_id": r["submission_id"],
                "status": r["response"]["status"],
            }
            for r in results
            if r["response"]["status"] != "completed"
        ]
        (directory / "run.json").write_text(json.dumps(manifest, indent=2))
        audit = reconcile(settings.database_path, directory / "events.jsonl")
        (directory / "reconciliation.json").write_text(json.dumps(audit, indent=2))
        print(
            json.dumps(
                {
                    "turns": len(results),
                    "noncompleted": manifest["noncompleted"],
                    "reconciliation": audit,
                    "version_stable": manifest["version_stable"],
                }
            )
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
