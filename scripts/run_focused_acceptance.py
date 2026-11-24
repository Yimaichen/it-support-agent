"""Held-out business conversations, not global prompt rules or semantic filters."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src import pi_storage as journal
from src.config import PROJECT_ROOT, Settings
from src.data import load_corpus
from src.pi_agent import PiAgent
from src.run_logging import version_fingerprints
from src.state import StateService
from src.storage import Store
from src.tools import ToolContext, ToolRunner

CASES = {
    "V01": [
        "公司登录显示账号被锁，普通网页可以打开，验证器也在我手里。请说说现在知道了什么、还不知道什么。",
        "账号验证我实际试过三次，前面没说过次数。会议很急，请先整理给 IT，别把推测当已排除。",
    ],
    "V02": [
        "同办公室也有人打不开审批页面，状态页说有同地区故障。这能证明我这里没有其他问题吗？",
        "请整理当前判断，不要等我再做可选测试才保存交接。",
    ],
    "V03": [
        "我们内部新应用 AtlasCanvas 只有白屏，别的应用正常。普通窗口和隐私窗口都试了，还是白屏。",
        "Chrome 换 Edge 算换浏览器引擎吗？我不想继续测试，先保存交接。",
    ],
    "V04": [
        "我把公司密码填进了不熟悉的网页，这和忘记密码的处理一样吗？请按公司适用资料告诉我现在怎么做。",
        "请整理交接，哪些是我报告的、哪些是查证的要分清，别声称已经通知安全团队。",
    ],
    "V05": [
        "刚才的账号调查请另外整理一份交接，最早的报错和已经查到的依据都要带上，原来的 VPN 记录别动。",
        "再回到 VPN，补充：只有家里网络会断。请更新 VPN 交接，不要混入账号调查。",
    ],
}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--thinking-mode", choices=["disabled", "enabled"])
    parser.add_argument("--ids", default=",".join(CASES))
    args = parser.parse_args()
    if not args.live:
        parser.error("--live required for billed calls")
    root = args.run_dir.resolve()
    if (
        not root.is_relative_to(PROJECT_ROOT / "runtime/acceptance")
        or root == PROJECT_ROOT / "runtime/acceptance"
    ):
        parser.error("isolated acceptance child directory required")
    root.mkdir(parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    load_dotenv(PROJECT_ROOT / ".env")
    os.environ["RUN_LOG_DIR"] = str(root)
    base = Settings.from_env()
    if not base.model_configured:
        parser.error("model unavailable")
    settings = replace(
        base,
        database_path=root / "support.sqlite3",
        pi_runtime_dir=root / "pi",
        **({"thinking_mode": args.thinking_mode} if args.thinking_mode else {}),
    )
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "versions": version_fingerprints(),
        "model": settings.model,
        "thinking_mode": settings.thinking_mode,
        "scenarios": args.ids.split(","),
        "mode": "live_held_out_business_variants",
    }
    (root / "run.json").write_text(json.dumps(manifest, indent=2))
    corpus = load_corpus(settings.data_dir)
    store = Store(settings.database_path)
    store.import_resolutions(corpus.resolutions)
    user = corpus.employees[0].id
    rows = []
    for sid in manifest["scenarios"]:
        session = store.create_session(user)["session_id"]
        if sid == "V05":
            # Reproduce late ownership deterministically; no paid call or fake tool output.
            state = StateService(store)
            vpn = state.create_issue(user, session, "VPN disconnects", "connectivity")
            message = store.append_message(
                user,
                session,
                {"role": "user", "content": "VPN 每十分钟断开。另外账号提示 ACCOUNT_LOCKED。"},
                issue_id=vpn["issue_id"],
            )
            state.record_user_report(user, vpn["issue_id"], message["message_id"])
            ToolRunner(corpus, store).execute(
                ToolContext(user, session, vpn["issue_id"]),
                "lookup_policy",
                {"service_id": "sso"},
                invocation_id="fixture-account-policy",
            )
            journal.ensure_schema(store)
            store._conn.execute(
                "INSERT OR REPLACE INTO pi_sessions(session_id,current_issue_id) VALUES (?,?)",
                (session, vpn["issue_id"]),
            )
            (root / "late-case-fixture.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "vpn_issue_id": vpn["issue_id"],
                        "report": message,
                        "query_invocation": "fixture-account-policy",
                        "note": "Real deterministic tool result was recorded on original VPN case before the new account request. Original rows must survive.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        for index, text in enumerate(CASES[sid], 1):
            began = time.monotonic()
            result = PiAgent(store, corpus, settings).run(
                user, session, text, submission_id=f"{sid}-{index}"
            )
            row = {
                "scenario": sid,
                "session_id": session,
                "submission_id": f"{sid}-{index}",
                "input": text,
                "response": result,
                "wall_seconds": round(time.monotonic() - began, 3),
            }
            rows.append(row)
            with (root / "transcripts.jsonl").open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            (root / "progress.json").write_text(
                json.dumps(
                    {
                        "turns": len(rows),
                        "last": f"{sid}-{index}",
                        "completed": sum(r["response"]["status"] == "completed" for r in rows),
                    },
                    indent=2,
                )
            )
            print(f"{sid}-{index} {result['status']}", flush=True)
    manifest.update(
        finished_at=datetime.now(timezone.utc).isoformat(),
        versions_after=version_fingerprints(),
        turns=len(rows),
    )
    (root / "run.json").write_text(json.dumps(manifest, indent=2))
    store.close()


if __name__ == "__main__":
    main()
