"""Local React workbench API. Demo identity selection is not enterprise authentication."""

from __future__ import annotations

import json
import os
import queue
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from src import pi_storage as journal
from src.config import PROJECT_ROOT, Settings
from src.data import load_corpus
from src.pi_agent import PiAgent
from src.privacy import redact_text, sanitize
from src.run_logging import build_id, log_event
from src.storage import AccessDenied, ConflictError, Store
from src.user_actions import UserAction
from src.web_view import session_view


class TurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)
    submission_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    action: UserAction | None = None


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str


def create_app(settings: Settings | None = None, *, agent_factory=PiAgent) -> FastAPI:
    settings = settings or Settings.from_env()
    corpus = load_corpus(settings.data_dir)

    @asynccontextmanager
    async def lifespan(_app):
        with Store(settings.database_path) as store:
            journal.ensure_schema(store)
            store.import_resolutions(corpus.resolutions)
        yield

    app = FastAPI(title="IT 工作台", lifespan=lifespan)
    app.state.settings = settings

    def employee(x_employee_id: Annotated[str, Header()] = "EMP-001") -> str:
        if x_employee_id not in {e.id for e in corpus.employees}:
            raise HTTPException(404, "无法访问该资源")
        return x_employee_id

    @app.exception_handler(AccessDenied)
    async def unavailable(_request: Request, _exc: AccessDenied):
        return JSONResponse({"detail": "无法访问该资源"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(_request: Request, _exc: ConflictError):
        return JSONResponse({"detail": "状态已变化，请刷新后继续"}, status_code=409)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        # JSON writes from other websites must not act on the local demo database.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            from urllib.parse import urlparse

            if urlparse(origin).hostname not in {"localhost", "127.0.0.1", "[::1]", "::1"}:
                return JSONResponse({"detail": "不允许的请求来源"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/bootstrap")
    def bootstrap(user: str = Depends(employee)):
        return {
            "employees": [
                {"id": e.id, "name": e.name, "department": e.department, "location": e.location}
                for e in corpus.employees
            ],
            "employee_id": user,
            "model_configured": settings.model_configured,
            "model": settings.model,
            "build": build_id(),
            "snapshot_at": corpus.reference_time.isoformat(),
            "run_id": os.environ.get("RUN_LOG_ID", "manual"),
            "identity_mode": "local_demo",
            "frontend": "react",
        }

    @app.get("/api/sessions")
    def sessions(user: str = Depends(employee)):
        with Store(settings.database_path) as store:
            result = []
            for row in store.list_sessions(user):
                issues = store.list_issues(user, row["session_id"])
                first = store._conn.execute(
                    "SELECT payload_json FROM messages WHERE session_id=? AND json_extract(payload_json,'$.role')='user' ORDER BY sequence LIMIT 1",
                    (row["session_id"],),
                ).fetchone()
                result.append(
                    {
                        **row,
                        "title": issues[0]["title"]
                        if issues
                        else (json.loads(first[0])["content"][:48] if first else "新对话"),
                        "status": issues[0]["status"] if issues else "new",
                    }
                )
            return sanitize(result)

    @app.post("/api/sessions", status_code=201)
    def create_session(user: str = Depends(employee)):
        with Store(settings.database_path) as store:
            return store.create_session(user)

    def owned_view(store, user, session_id):
        journal.ensure_schema(store)
        view = session_view(store, user, session_id)
        binding = journal.binding(store, user, session_id)
        view["current_issue_id"] = binding["current_issue_id"] if binding else None
        latest = view["latest"]
        view["busy"] = bool(
            latest
            and latest["status"] == "running"
            and latest.get("lease_until")
            and datetime.fromisoformat(latest["lease_until"]) > store.clock()
        )
        view["issues"] = [
            {k: row[k] for k in ("issue_id", "title", "category", "status", "updated_at")}
            for row in view["issues"]
        ]
        view["handoffs"] = [json.loads(raw) for raw in view["handoffs"]]
        return sanitize(view)

    @app.get("/api/sessions/{session_id}")
    def read_session(session_id: str, user: str = Depends(employee)):
        with Store(settings.database_path) as store:
            return owned_view(store, user, session_id)

    @app.post("/api/sessions/{session_id}/selection")
    def select_case(session_id: str, body: CaseInput, user: str = Depends(employee)):
        with Store(settings.database_path) as store:
            view = owned_view(store, user, session_id)
            issue = store.get_issue(user, body.issue_id)
            if issue["session_id"] != session_id:
                raise AccessDenied("unavailable")
            if view["busy"] or view["read_only"]:
                raise ConflictError("unavailable")
            # Selection changes no diagnosis, reports or resolution state.
            store._conn.execute(
                "INSERT INTO pi_sessions(session_id,current_issue_id) VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET current_issue_id=excluded.current_issue_id",
                (session_id, body.issue_id),
            )
            return owned_view(store, user, session_id)

    @app.get("/api/handoffs/{issue_id}")
    def download(issue_id: str, user: str = Depends(employee)):
        from src.records import Handoff

        with Store(settings.database_path) as store:
            record = Handoff.model_validate(store.get_handoff(user, issue_id)).model_dump(
                mode="json"
            )
            return JSONResponse(
                sanitize(record),
                headers={
                    "Content-Disposition": f'attachment; filename="handoff-{record["handoff_id"]}.json"'
                },
            )

    @app.get("/api/library/{source_id}")
    def source(source_id: str, user: str = Depends(employee)):
        for item in [*corpus.knowledge, *corpus.policies]:
            if item.id == source_id:
                return sanitize(item.model_dump(mode="json"))
        raise AccessDenied("unavailable")

    @app.post("/api/sessions/{session_id}/turns")
    def turn(session_id: str, body: TurnInput, user: str = Depends(employee)):
        with Store(settings.database_path) as store:
            view = owned_view(store, user, session_id)
            if view["read_only"]:
                raise HTTPException(409, "旧版历史只读，请新建对话")
            if body.action:
                issue = store.get_issue(user, body.action.issue_id)
                if issue["session_id"] != session_id:
                    raise AccessDenied("unavailable")
            if not settings.model_configured and not (
                body.action and body.action.kind == "confirm"
            ):
                raise HTTPException(503, "模型尚未配置，历史仍可查看")
        events: queue.Queue = queue.Queue(maxsize=256)
        disconnected = threading.Event()

        def emit(kind, data):
            if disconnected.is_set():
                return
            entry = {"event": kind, "data": data}
            try:
                events.put_nowait(entry)
            except queue.Full:
                try:
                    events.get_nowait()
                except queue.Empty:
                    pass
                events.put_nowait(entry)

        def notify(event, details):
            # Explicit public allowlist. No thought, system prompt, config or raw provider payload.
            if event in {"reply_started", "reply_snapshot", "reply_finished"}:
                emit(
                    event,
                    {
                        "text": redact_text(details.get("text", "")),
                        "message_index": details.get("message_index"),
                    },
                )
            elif event in {"tool_started", "tool_finished"}:
                emit(event, {"tool": details.get("tool", "")})
            elif event == "model_started":
                emit(event, {})

        def work():
            try:
                with Store(settings.database_path) as store:
                    log_event(
                        "ui_submission",
                        backend="pi",
                        frontend="react",
                        user_id=user,
                        session_id=session_id,
                        submission_id=body.submission_id,
                        text=redact_text(body.text),
                        action=body.action.model_dump() if body.action else None,
                    )
                    result = agent_factory(store, corpus, settings, on_progress=notify).run(
                        user,
                        session_id,
                        body.text,
                        submission_id=body.submission_id,
                        action=body.action.model_dump() if body.action else None,
                    )
                    log_event(
                        "ui_result",
                        user_id=user,
                        session_id=session_id,
                        submission_id=body.submission_id,
                        result=result,
                    )
                    emit("done", {"result": result, "view": owned_view(store, user, session_id)})
            except Exception:
                log_event(
                    "ui_request_failed",
                    user_id=user,
                    session_id=session_id,
                    submission_id=body.submission_id,
                    traceback=traceback.format_exc(),
                )
                emit(
                    "error",
                    {"detail": "操作中断，已保存的进展可刷新查看。请勿重复执行未知结果的操作。"},
                )

        def stream():
            threading.Thread(target=work, daemon=True, name="pi-turn").start()
            try:
                while True:
                    try:
                        event = events.get(timeout=10)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    if event["event"] in {"done", "error"}:
                        break
            finally:
                disconnected.set()
                # Closing a tab does not abort/replay a committed turn; SQLite remains authoritative.

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    dist = PROJECT_ROOT / "frontend/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
        raise HTTPException(
            503, "前端未构建，请运行 npm ci --prefix frontend && npm run build --prefix frontend"
        )

    return app


app = create_app()
