"""Business-boundary and real SDK tests; the local provider never uses a paid API."""

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src import pi_storage as journal
from src.config import PROJECT_ROOT, Settings
from src.pi_agent import PiAgent
from src.state import StateService
from src.storage import AccessDenied, ConflictError
from src.tools import ToolContext, ToolRunner


def checked_reply(dispatch, text):
    """Synthetic runtime returns ordinary Pi text; no semantic reviewer exists."""
    return {"reply": text}


class ScriptRuntime:
    script = []
    prompts = []

    def __init__(self, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def open(self, init, dispatch):
        self.init = init
        return {"session_file": str(Path(init["root"]) / "synthetic.jsonl")}

    def prompt(self, text, context, submission_id, dispatch, notify):
        json.dumps(context)  # Same JSON boundary as the actual Node transport.
        self.prompts.append((text, context))
        for call, name, arguments in self.script:
            result = dispatch(name, arguments, call)
            assert result["ok"], result
        return checked_reply(dispatch, "已记录你的反馈，接下来核实连接是否稳定。")


def make(env, tmp_path, script=()):
    corpus, store, ctx = env
    journal.ensure_schema(store)
    store._conn.execute(
        "INSERT OR IGNORE INTO pi_sessions(session_id) VALUES (?)", (ctx.session_id,)
    )
    ScriptRuntime.script, ScriptRuntime.prompts = list(script), []
    cfg = Settings(api_key="offline-test-key", pi_runtime_dir=tmp_path / "pi")
    return PiAgent(store, corpus, cfg, runtime_factory=ScriptRuntime)


def test_followup_after_handoff_keeps_case_without_progress_tools(support_env, tmp_path):
    _, store, ctx = support_env
    state = StateService(store)
    store.save_handoff(ctx.user_id, ctx.issue_id, {"summary": "待人工排查"})
    state.propose(
        ctx.user_id,
        ctx.issue_id,
        {"status": "escalated"},
        expected_revision=store.get_issue(ctx.user_id, ctx.issue_id)["revision"],
    )
    agent = make(support_env, tmp_path, [("read", "manage_issue", {"mode": "read"})])
    store._conn.execute(
        "UPDATE pi_sessions SET current_issue_id=? WHERE session_id=?",
        (ctx.issue_id, ctx.session_id),
    )
    result = agent.run(ctx.user_id, ctx.session_id, "升级到6.2后仍然断线", submission_id="followup")
    assert result["status"] == "completed" and result["issue_id"] == ctx.issue_id
    assert len(store.list_issues(ctx.user_id, ctx.session_id)) == 1
    assert not state.memory(ctx.user_id, ctx.issue_id).attempted_steps
    assert not state.memory(ctx.user_id, ctx.issue_id).confirmation
    assert store.get_handoff(ctx.user_id, ctx.issue_id)["summary"] == "待人工排查"


def test_repeat_submission_does_not_call_runtime_again(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(support_env, tmp_path)
    first = agent.run(ctx.user_id, ctx.session_id, "你好", submission_id="same")
    other = make(support_env, tmp_path)
    assert other.run(ctx.user_id, ctx.session_id, "你好", submission_id="same") == first
    assert not ScriptRuntime.prompts
    with pytest.raises(ConflictError):
        other.run(ctx.user_id, ctx.session_id, "另一个输入", submission_id="same")
    with pytest.raises(AccessDenied):
        other.run(corpus_other_user(support_env), ctx.session_id, "你好", submission_id="other")


def corpus_other_user(env):
    return next(e.id for e in env[0].employees if e.id != env[2].user_id)


def test_duplicate_business_call_is_atomic_and_replayable(support_env, tmp_path):
    _, store, ctx = support_env
    args = {"mode": "create", "title": "新 VPN 问题", "category": "connectivity"}
    agent = make(
        support_env,
        tmp_path,
        [("same-call", "manage_issue", args), ("same-call", "manage_issue", args)],
    )
    agent.run(ctx.user_id, ctx.session_id, "新 VPN 问题", submission_id="dup")
    assert len(store.list_issues(ctx.user_id, ctx.session_id)) == 2
    assert store._conn.execute("SELECT COUNT(*) FROM pi_tool_calls").fetchone()[0] == 1


def test_failed_progress_rolls_back_partial_notes(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(support_env, tmp_path)
    agent.user_id, agent.session_id = ctx.user_id, ctx.session_id
    agent.turn = journal.claim(store, ctx.user_id, ctx.session_id, "我在London", "tx", None, 30)
    agent.current = ctx.issue_id
    from src.budget import TurnBudget

    agent.budget = TurnBudget(agent.settings)
    reply = agent._dispatch(
        "record_progress",
        {
            "context_updates": [{"field": "region", "value": "London"}],
            "step": "step_99",
            "result": "succeeded",
        },
        "bad-step",
    )
    assert not reply["ok"]
    assert not StateService(store).memory(ctx.user_id, ctx.issue_id).reported_context


def test_case_free_profile_and_history_keep_identity(support_env):
    corpus, store, ctx = support_env
    tools = ToolRunner(corpus, store)
    result = tools.execute(ToolContext(ctx.user_id, ctx.session_id, None), "get_current_user", {})
    assert result["ok"] and result["data"]["id"] == ctx.user_id
    assert result["evidence_refs"] == []
    assert not tools.execute(
        ToolContext(corpus_other_user(support_env), ctx.session_id, None), "get_current_user", {}
    )["ok"]
    assert not tools.execute(
        ToolContext(ctx.user_id, ctx.session_id, None),
        "check_service_status",
        {"service_id": "vpn"},
    )["ok"]


def test_vpn_region_status_uses_same_global_scope(support_env):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(
        ctx, "check_service_status", {"service_id": "vpn", "region": "London"}
    )
    memory = StateService(store).memory(ctx.user_id, ctx.issue_id)
    assert result["ok"]
    assert "vpn:global" in {
        fact.subject for fact in memory.current_facts.values() if fact.topic == "service_status"
    }
    from src.investigation import effective_status_region

    service = next(s for s in corpus.services if s.id == "vpn")
    assert effective_status_region(service, "London") is None
    assert result["data"]["effective_scope"] == "global"


def test_uncertain_submission_is_not_replayed(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(support_env, tmp_path)
    turn = journal.claim(store, ctx.user_id, ctx.session_id, "继续", "stale", None, 30)
    store._conn.execute(
        "UPDATE pi_turns SET lease_until='2000-01-01' WHERE turn_id=?", (turn["turn_id"],)
    )
    result = agent.run(ctx.user_id, ctx.session_id, "继续", submission_id="stale", resume=True)
    assert result["outcome"] == "interrupted"
    assert not ScriptRuntime.prompts


def test_historical_history_is_read_only_without_a_backend_switch(support_env, tmp_path):
    _, store, ctx = support_env
    journal.ensure_schema(store)
    store.append_message(ctx.user_id, ctx.session_id, {"role": "user", "content": "old history"})
    assert journal.is_historical_session(store, ctx.user_id, ctx.session_id)
    agent = PiAgent(store, support_env[0], Settings())
    with pytest.raises(ConflictError, match="read-only"):
        agent.run(ctx.user_id, ctx.session_id, "continue", submission_id="blocked")
    assert store._conn.execute("SELECT COUNT(*) FROM pi_turns").fetchone()[0] == 0
    fresh = store.create_session(ctx.user_id)["session_id"]
    assert not journal.is_historical_session(store, ctx.user_id, fresh)
    journal.claim(store, ctx.user_id, fresh, "hello", "bind", None, 30)
    assert journal.is_pi_session(store, ctx.user_id, fresh)


def test_real_sdk_tool_callback_persistence_and_reopen(support_env, tmp_path, monkeypatch):
    if not (PROJECT_ROOT / "pi-runtime/node_modules/.bin/tsx").exists():
        pytest.skip("npm ci --prefix pi-runtime required")
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    requests = []
    secret = "synthetic_secret_abc123"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            if len(requests) == 1:
                delta = {
                    "role": "assistant",
                    "content": "我先查询你的员工资料。",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-profile",
                            "type": "function",
                            "function": {"name": "get_current_user", "arguments": "{}"},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {
                    "role": "assistant",
                    "content": "我不能为你关闭MFA。\npassword: " + secret + "\n请提供报错文字。",
                }
                finish = "stop"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {
                        "prompt_tokens": 50000 if "NETWORK_SAMPLE" in json.dumps(payload) else 10,
                        "completion_tokens": 10,
                        "total_tokens": 50010 if "NETWORK_SAMPLE" in json.dumps(payload) else 20,
                    },
                },
            ]
            body = (
                "".join(
                    "data: "
                    + json.dumps(
                        {"id": "local", "model": "local", "object": "chat.completion.chunk", **c}
                    )
                    + "\n\n"
                    for c in chunks
                )
                + "data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    corpus, store, ctx = support_env
    cfg = Settings(
        api_key="offline-test-key",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        model="local",
        pi_runtime_dir=tmp_path / "private-pi",
        turn_timeout_seconds=20,
    )
    sid = store.create_session(ctx.user_id)["session_id"]
    try:
        previews = []
        first = PiAgent(
            store, corpus, cfg, on_progress=lambda event, details: previews.append((event, details))
        ).run(ctx.user_id, sid, "查询员工资料\u2028这是同一条消息", submission_id="sdk1")
        assert first["status"] == "completed", first
        assert secret not in first["reply"]
        assert "我不能为你关闭MFA" in first["reply"]
        assert first["stats"]["model_attempts"] == 2
        snapshots = [details["text"] for event, details in previews if event == "reply_snapshot"]
        assert snapshots and any("REDACTED" in text for text in snapshots)
        assert secret not in "".join(snapshots)
        finished = [details for event, details in previews if event == "reply_finished"]
        assert [item["message_index"] for item in finished] == [1]
        assert any(event == "investigation_note" for event, _ in previews)
        replies = [
            m["message"]["content"]
            for m in store.list_messages(ctx.user_id, sid)
            if m["message"]["role"] == "assistant"
        ]
        assert replies == [first["reply"]]
        replay = PiAgent(store, corpus, cfg).run(
            ctx.user_id, sid, "查询员工资料\u2028这是同一条消息", submission_id="sdk1"
        )
        assert replay == first
        assert len(requests) == 2
        assert len(store.list_messages(ctx.user_id, sid)) == 2
        file = Path(journal.binding(store, ctx.user_id, sid)["session_file"])
        before = file.read_text()
        assert secret not in before
        assert "offline-test-key" not in before
        assert "REDACTED" in before
        assert file.stat().st_mode & 0o777 == 0o600
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM pi_tool_calls WHERE name='get_current_user'"
            ).fetchone()[0]
            == 1
        )
        second = PiAgent(store, corpus, cfg).run(
            ctx.user_id, sid, "继续刚才的话题", submission_id="sdk2"
        )
        assert second["status"] == "completed", second
        assert journal.binding(store, ctx.user_id, sid)["session_file"] == str(file)
        assert "查询员工资料" in json.dumps(requests[-1], ensure_ascii=False)
        assert len(file.read_text()) > len(before)
        assert not store.list_issues(ctx.user_id, sid)

        from src.pi_runtime import PiRuntime

        class CompactRuntime(PiRuntime):
            def open(self, init, dispatch):
                init["config"]["context_window"] = 32768
                return super().open(init, dispatch)

        # Force a genuine SDK compaction with a large synthetic diagnostic log.
        third = PiAgent(store, corpus, cfg, runtime_factory=CompactRuntime).run(
            ctx.user_id,
            sid,
            "这是一段用于验证压缩的合成日志：" + "NETWORK_SAMPLE " * 7000,
            submission_id="sdk3",
        )
        assert third["status"] == "completed", third
        fourth = PiAgent(store, corpus, cfg, runtime_factory=CompactRuntime).run(
            ctx.user_id, sid, "继续刚才的日志排障。", submission_id="sdk4"
        )
        assert fourth["status"] == "completed", fourth
        entries = [json.loads(line) for line in file.read_text().split("\n") if line.strip()]
        assert any(e.get("type") == "compaction" for e in entries)
        assert secret not in file.read_text()
        assert max(third["stats"]["model_attempts"], fourth["stats"]["model_attempts"]) >= 2

        # A failed run is never replayed; a NEW submission resumes the same Pi file.
        requests.clear()
        interrupted = PiAgent(store, corpus, replace(cfg, max_model_attempts=1)).run(
            ctx.user_id, sid, "再查询一次我的资料", submission_id="sdk-budget"
        )
        assert interrupted["status"] == "paused", interrupted
        assert interrupted["error"]["type"] == "run_budget"
        assert len(requests) == 1  # only the main model consumes the budget
        assert (
            PiAgent(store, corpus, cfg).run(
                ctx.user_id, sid, "继续查询", submission_id="sdk-recover"
            )["status"]
            == "completed"
        )
        assert journal.binding(store, ctx.user_id, sid)["session_file"] == str(file)
        assert secret not in file.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_query_then_name_request_keeps_evidence_and_single_case(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            ("status", "check_service_status", {"service_id": "vpn", "region": "London"}),
            (
                "name",
                "manage_issue",
                {"mode": "create", "title": "VPN 远程断连", "category": "connectivity"},
            ),
        ],
    )
    result = agent.run(
        ctx.user_id, ctx.session_id, "VPN 在伦敦每十分钟断连", submission_id="autocase"
    )
    assert result["status"] == "completed"
    issues = store.list_issues(ctx.user_id, ctx.session_id)
    assert len(issues) == 2  # Existing fixture + exactly one new request.
    issue = store.get_issue(ctx.user_id, result["issue_id"])
    assert issue["title"] == "VPN 远程断连"
    memory = StateService(store).memory(ctx.user_id, result["issue_id"])
    assert any(f.topic == "service_status" for f in memory.current_facts.values())


def test_confirm_archive_and_reopen_use_existing_business_rules(support_env, tmp_path):
    corpus, store, ctx = support_env
    state = StateService(store)
    ToolRunner(corpus, store).execute(ctx, "search_knowledge_base", {"query": "VPN"})
    state.add_step(ctx.user_id, ctx.issue_id, "Use the managed VPN client.")
    agent = make(support_env, tmp_path)
    result = agent.run(
        ctx.user_id,
        ctx.session_id,
        "我确认按此步骤已解决",
        submission_id="confirm",
        action={"kind": "confirm", "issue_id": ctx.issue_id},
    )
    assert result["status"] == "completed"
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "resolved"
    archives = [
        r for r in store.list_resolutions(ctx.user_id) if r.get("source_issue_id") == ctx.issue_id
    ]
    assert len(archives) == 1 and archives[0]["valid"]
    followup = make(support_env, tmp_path).run(
        ctx.user_id,
        ctx.session_id,
        "问题又复发了",
        submission_id="reopen",
        action={"kind": "reopen", "issue_id": ctx.issue_id},
    )
    assert followup["status"] == "completed"
    assert followup["issue_id"] == ctx.issue_id
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "investigating"
    assert not store._conn.execute(
        "SELECT valid FROM resolution_history WHERE source_issue_id=?", (ctx.issue_id,)
    ).fetchone()[0]


def test_auto_created_case_is_named_without_stranding_evidence(support_env, tmp_path):
    corpus, store, ctx = support_env
    sid = store.create_session(ctx.user_id)["session_id"]
    agent = make(
        support_env,
        tmp_path,
        [
            ("query", "check_service_status", {"service_id": "vpn", "region": "London"}),
            (
                "name",
                "manage_issue",
                {"mode": "create", "title": "VPN 断连", "category": "connectivity"},
            ),
        ],
    )
    result = agent.run(ctx.user_id, sid, "London 的 VPN 断连", submission_id="autoname")
    cases = store.list_issues(ctx.user_id, sid)
    assert len(cases) == 1
    assert cases[0]["title"] == "VPN 断连"
    assert StateService(store).memory(ctx.user_id, result["issue_id"]).current_facts


def test_new_topic_does_not_copy_employee_report_into_previous_case(support_env, tmp_path):
    _, store, ctx = support_env
    journal.ensure_schema(store)
    store._conn.execute("INSERT INTO pi_sessions VALUES (?,NULL,?)", (ctx.session_id, ctx.issue_id))
    before = StateService(store).memory(ctx.user_id, ctx.issue_id).user_reports
    agent = make(
        support_env,
        tmp_path,
        [
            (
                "create",
                "manage_issue",
                {"mode": "create", "title": "Salesforce 慢", "category": "application"},
            )
        ],
    )
    result = agent.run(
        ctx.user_id,
        ctx.session_id,
        "另外一个问题，Salesforce 在 Chicago 很慢",
        submission_id="new-topic",
    )
    assert result["issue_id"] != ctx.issue_id
    assert StateService(store).memory(ctx.user_id, ctx.issue_id).user_reports == before


@pytest.mark.parametrize("escalated", [False, True])
def test_employee_confirmation_archives_and_reopen_invalidates(support_env, tmp_path, escalated):
    corpus, store, ctx = support_env
    ToolRunner(corpus, store).execute(ctx, "check_service_status", {"service_id": "vpn"})
    state = StateService(store)
    state.add_step(ctx.user_id, ctx.issue_id, "连接稳定性验证")
    if escalated:
        store.save_handoff(ctx.user_id, ctx.issue_id, {"summary": "先前转人工，员工随后报告恢复"})
        state.propose(
            ctx.user_id,
            ctx.issue_id,
            {"status": "escalated"},
            expected_revision=store.get_issue(ctx.user_id, ctx.issue_id)["revision"],
        )
    barrier = state.memory(ctx.user_id, ctx.issue_id).confirmation_after_sequence
    first = make(support_env, tmp_path).run(
        ctx.user_id,
        ctx.session_id,
        "确认验证成功，问题已解决",
        submission_id="confirm",
        action={"kind": "confirm", "issue_id": ctx.issue_id},
    )
    assert first["status"] == "completed"
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "resolved"
    memory = state.memory(ctx.user_id, ctx.issue_id)
    assert memory.confirmation_after_sequence == barrier
    assert not any(event.kind == "reopen" for event in memory.processed_events.values())
    replay = make(support_env, tmp_path).run(
        ctx.user_id,
        ctx.session_id,
        "确认验证成功，问题已解决",
        submission_id="confirm",
        action={"kind": "confirm", "issue_id": ctx.issue_id},
    )
    assert replay == first
    assert (
        store._conn.execute(
            "SELECT count(*) FROM resolution_history WHERE source_issue_id=?", (ctx.issue_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        store._conn.execute(
            "SELECT valid FROM resolution_history WHERE source_issue_id=?", (ctx.issue_id,)
        ).fetchone()[0]
        == 1
    )
    second = make(support_env, tmp_path).run(
        ctx.user_id,
        ctx.session_id,
        "问题复发了",
        submission_id="reopen",
        action={"kind": "reopen", "issue_id": ctx.issue_id},
    )
    assert second["issue_id"] == ctx.issue_id
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "investigating"
    assert (
        store._conn.execute(
            "SELECT valid FROM resolution_history WHERE source_issue_id=?", (ctx.issue_id,)
        ).fetchone()[0]
        == 0
    )


def test_removed_guidance_and_progress_are_not_model_tools():
    from src.pi_agent import schemas

    names = {tool["name"] for tool in schemas()}
    assert not names & {"offer_guidance", "record_progress", "confirm_resolved", "archive"}
    assert {"read_resolution", "read_knowledge_document", "save_handoff"} <= names


def test_removed_guidance_tool_cannot_write_steps(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(support_env, tmp_path)
    agent.user_id, agent.session_id, agent.current = ctx.user_id, ctx.session_id, ctx.issue_id
    agent.turn = journal.claim(store, ctx.user_id, ctx.session_id, "请指导", "removed", None, 30)
    from src.budget import TurnBudget

    agent.budget = TurnBudget(agent.settings)
    result = agent._dispatch("offer_guidance", {"steps": []}, "removed-call")
    assert not result["ok"]
    assert not StateService(store).memory(ctx.user_id, ctx.issue_id).attempted_steps


def test_completed_assistant_messages_survive_interruption(support_env, tmp_path):
    from src.pi_runtime import PiRuntimeError

    class InterruptedRuntime(ScriptRuntime):
        def prompt(self, text, context, submission_id, dispatch, notify):
            checked_reply(dispatch, "已核实服务状态，正在继续查询。")
            notify("reply_finished", {"text": "已核实服务状态，正在继续查询。", "message_index": 1})
            raise PiRuntimeError("turn_timeout")

    corpus, store, ctx = support_env
    cfg = Settings(api_key="offline-test-key", pi_runtime_dir=tmp_path / "pi")
    result = PiAgent(store, corpus, cfg, runtime_factory=InterruptedRuntime).run(
        ctx.user_id, ctx.session_id, "请继续查询", submission_id="interrupted-messages"
    )
    replies = [
        m["message"]["content"]
        for m in store.list_messages(ctx.user_id, ctx.session_id)
        if m["message"]["role"] == "assistant"
    ]
    assert result["status"] == "paused"
    assert replies == ["已核实服务状态，正在继续查询。", result["reply"]]


def test_final_event_never_replaces_a_published_message(support_env):
    _, store, ctx = support_env
    journal.ensure_schema(store)
    turn = journal.claim(store, ctx.user_id, ctx.session_id, "请整理交接", "receipt", None, 30)
    journal.record_assistant(store, ctx.user_id, turn, 1, "先检查资料。", ctx.issue_id)
    final = journal.record_assistant(store, ctx.user_id, turn, 2, "需要人工处理。", ctx.issue_id)
    result = {"status": "completed", "reply": "需要人工处理。", "issue_id": ctx.issue_id}
    journal.finish(store, ctx.user_id, turn, result, assistant_message_id=final["message_id"])
    replies = [
        m["message"]["content"]
        for m in store.list_messages(ctx.user_id, ctx.session_id)
        if m["message"]["role"] == "assistant"
    ]
    assert replies == ["先检查资料。", "需要人工处理。"]
    with pytest.raises(ConflictError):
        journal.finish(
            store,
            ctx.user_id,
            turn,
            {**result, "reply": "需要人工处理。附加替换内容"},
            assistant_message_id=final["message_id"],
        )


def test_handoff_explicit_case_does_not_write_to_selected_topic(support_env, tmp_path):
    _, store, ctx = support_env
    state = StateService(store)
    other = state.create_issue(ctx.user_id, ctx.session_id, "Okta locked", "account")
    agent = make(support_env, tmp_path)
    agent.user_id, agent.session_id, agent.current = ctx.user_id, ctx.session_id, ctx.issue_id
    agent.turn = journal.claim(
        store, ctx.user_id, ctx.session_id, "只更新账号交接", "handoff-case", None, 30
    )
    result = agent._business(
        "save_handoff",
        {
            "case": "case_2",
            "summary": "Account locked",
            "impact": "Cannot sign in",
            "urgency_reason": "Employee cannot work",
            "escalation_reason": "Needs human support",
        },
    )
    assert result["data"]["issue_id"] == other["issue_id"]
    assert store.get_handoff(ctx.user_id, other["issue_id"])["summary"] == "Account locked"
    with pytest.raises(AccessDenied):
        store.get_handoff(ctx.user_id, ctx.issue_id)
    with pytest.raises(AccessDenied):
        agent._business(
            "save_handoff",
            {
                "case": "case_99",
                "summary": "Wrong case",
                "impact": "Unknown",
                "urgency_reason": "Unknown",
                "escalation_reason": "Unknown",
            },
        )


def test_trusted_confirmation_after_handoff_does_not_reopen_same_event(support_env, tmp_path):
    """A saved draft must not make the employee's real confirmation impossible."""
    corpus, store, ctx = support_env
    ToolRunner(corpus, store).execute(ctx, "check_service_status", {"service_id": "vpn"})
    agent = make(
        support_env,
        tmp_path,
        [("draft", "save_handoff", {"summary": "Awaiting stability confirmation."})],
    )
    store._conn.execute(
        "UPDATE pi_sessions SET current_issue_id=? WHERE session_id=?",
        (ctx.issue_id, ctx.session_id),
    )
    saved = agent.run(
        ctx.user_id, ctx.session_id, "Please save a handoff.", submission_id="save-draft"
    )
    assert saved["status"] == "completed"
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "escalated"
    state = StateService(store)
    before = state.memory(ctx.user_id, ctx.issue_id)
    agent = make(support_env, tmp_path)
    result = agent.run(
        ctx.user_id,
        ctx.session_id,
        "I confirm it is working.",
        submission_id="confirm-draft",
        action={"kind": "confirm", "issue_id": ctx.issue_id},
    )
    assert result["status"] == "completed"
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "resolved"
    memory = state.memory(ctx.user_id, ctx.issue_id)
    assert memory.confirmation_after_sequence == before.confirmation_after_sequence
    assert not any(e.kind == "reopen" for e in memory.processed_events.values())
    assert memory.confirmation
    replay = make(support_env, tmp_path).run(
        ctx.user_id,
        ctx.session_id,
        "I confirm it is working.",
        submission_id="confirm-draft",
        action={"kind": "confirm", "issue_id": ctx.issue_id},
    )
    assert replay == result
    archives = [
        r for r in store.list_resolutions(ctx.user_id) if r.get("source_issue_id") == ctx.issue_id
    ]
    assert len(archives) == 1 and archives[0]["valid"]
