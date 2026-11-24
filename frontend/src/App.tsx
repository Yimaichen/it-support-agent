import { useCallback, useEffect, useRef, useState } from "react";
import { Menu, PanelRight, RefreshCw, Settings2 } from "lucide-react";
import { api, downloadHandoff, runTurn } from "./api";
import { ChatThread, Composer } from "./components/Conversation";
import { ContextPanel } from "./components/ContextPanel";
import { Dialog } from "./components/Dialog";
import type { DialogData } from "./components/Dialog";
import { Sidebar } from "./components/Sidebar";
import { EMPTY_VIEW, toolLabel } from "./types";
import type { Bootstrap, Session, Turn, View } from "./types";
function stored(key: string, fallback = "") {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: string) {
  try {
    value ? localStorage.setItem(key, value) : localStorage.removeItem(key);
  } catch {
    /* Browser storage is optional. */
  }
}
export default function App() {
  const [employee, setEmployee] = useState(() =>
    stored("it.employee", "EMP-001"),
  );
  const [selected, setSelected] = useState<string | null>(
    () => stored("it.session." + stored("it.employee", "EMP-001")) || null,
  );
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null),
    [sessions, setSessions] = useState<Session[]>([]),
    [view, setView] = useState<View>(EMPTY_VIEW);
  const [search, setSearch] = useState(""),
    [draft, setDraft] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [live, setLive] = useState(""),
    [activeTool, setActiveTool] = useState(""),
    [error, setError] = useState("");
  const [leftOpen, setLeftOpen] = useState(false),
    [rightOpen, setRightOpen] = useState(false),
    [dialog, setDialog] = useState<DialogData | null>(null);
  const controller = useRef<AbortController | null>(null),
    inFlight = useRef(false),
    context = useRef(0),
    draftRef = useRef(draft),
    last = useRef<{ employee: string; session: string; body: Turn } | null>(
      null,
    );
  draftRef.current = draft;
  const closeDialog = useCallback(() => setDialog(null), []);
  const issue =
    view.issues.find((i) => i.issue_id === view.current_issue_id) ||
    view.issues[0];
  const employeeInfo = bootstrap?.employees.find((e) => e.id === employee);
  const refreshList = useCallback(async () => {
    const rows = await api<Session[]>("/sessions", employee);
    setSessions(rows);
  }, [employee]);
  useEffect(() => {
    let cancel = false;
    setLoading(true);
    setError("");
    setView(EMPTY_VIEW);
    setBusy(false);
    setLive("");
    setActiveTool("");
    inFlight.current = false;
    controller.current?.abort();
    last.current = null;
    context.current++;
    Promise.all([
      api<Bootstrap>("/bootstrap", employee),
      api<Session[]>("/sessions", employee),
    ])
      .then(([boot, rows]) => {
        if (cancel) return;
        setBootstrap(boot);
        setSessions(rows);
      })
      .catch((e) => {
        if (!cancel) setError(e.message);
      })
      .finally(() => {
        if (!cancel) setLoading(false);
      });
    return () => {
      cancel = true;
      controller.current?.abort();
    };
  }, [employee]);
  useEffect(() => {
    let cancel = false;
    context.current++;
    inFlight.current = false;
    controller.current?.abort();
    setView(EMPTY_VIEW);
    setBusy(false);
    setLive("");
    setActiveTool("");
    setError("");
    last.current = null;
    remember("it.session." + employee, selected || "");
    if (selected) {
      setLoading(true);
      api<View>("/sessions/" + selected, employee)
        .then((v) => {
          if (!cancel) {
            setView(v);
            setBusy(v.busy);
          }
        })
        .catch((e) => {
          if (!cancel) {
            setError(e.message);
            if (e.message === "无法访问该资源") setSelected(null);
          }
        })
        .finally(() => {
          if (!cancel) setLoading(false);
        });
    }
    return () => {
      cancel = true;
    };
  }, [selected, employee]);
  useEffect(() => {
    if (!selected || !busy || inFlight.current) return;
    let cancel = false;
    const id = setInterval(() => {
      api<View>("/sessions/" + selected, employee)
        .then((v) => {
          if (!cancel) {
            setView(v);
            setBusy(v.busy);
            if (!v.busy) void refreshList();
          }
        })
        .catch(() => {
          /* Preserve the displayed snapshot until a deliberate refresh. */
        });
    }, 2500);
    return () => {
      cancel = true;
      clearInterval(id);
    };
  }, [busy, selected, employee, refreshList]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setLeftOpen(false);
        setRightOpen(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const newConversation = () => {
    controller.current?.abort();
    setSelected(null);
    setDraft("");
    setLeftOpen(false);
    setRightOpen(false);
    setView(EMPTY_VIEW);
    setError("");
    last.current = null;
  };
  const selectSession = (id: string) => {
    setSelected(id);
    setDraft("");
    setLeftOpen(false);
    setRightOpen(false);
  };
  const switchEmployee = (id: string) => {
    controller.current?.abort();
    remember("it.employee", id);
    setEmployee(id);
    setSelected(stored("it.session." + id) || null);
    pending.current = null;
    setDraft("");
    setSearch("");
    setDialog(null);
    setLeftOpen(false);
    setRightOpen(false);
  };
  async function execute(body: Turn, session: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setLive("");
    setActiveTool("");
    const epoch = context.current;
    const abort = new AbortController();
    controller.current = abort;
    last.current = { employee, session, body };
    let eventError = false;
    setView((v) => ({
      ...v,
      messages: [...v.messages, { role: "user", content: body.text }],
    }));
    setDraft("");
    try {
      await runTurn(
        session,
        employee,
        body,
        (event) => {
          if (epoch !== context.current || abort.signal.aborted) return;
          if (event.event.startsWith("reply_")) {
            setLive(event.data.text || "");
            setActiveTool("");
          } else if (event.event === "tool_started") {
            setLive("");
            setActiveTool(toolLabel(event.data.tool || ""));
          } else if (event.event === "model_started") {
            setActiveTool("正在整理结果…");
          } else if (event.event === "done") {
            if (event.data.view) setView(event.data.view);
            setLive("");
            setActiveTool("");
            void refreshList();
          } else if (event.event === "error") {
            eventError = true;
            setError(event.data.detail || "处理未完成，可以刷新查看进展。");
          }
        },
        abort.signal,
      );
      if (!abort.signal.aborted && epoch === context.current && !eventError)
        last.current = null;
    } catch (e) {
      if (!abort.signal.aborted && epoch === context.current)
        setError(e instanceof Error ? e.message : "连接中断，请刷新查看进展。");
    } finally {
      if (!abort.signal.aborted && epoch === context.current) {
        inFlight.current = false;
        setLive("");
        setActiveTool("");
        try {
          const latest = await api<View>("/sessions/" + session, employee);
          if (epoch === context.current) {
            setView(latest);
            setBusy(latest.busy);
          }
        } catch {
          setBusy(false);
        }
      }
    }
  }
  async function send() {
    const text = draftRef.current.trim();
    if (!text || busy || loading || inFlight.current) return;
    try {
      let session = selected;
      if (!session) {
        const row = await api<{ session_id: string }>("/sessions", employee, {
          method: "POST",
        });
        session = row.session_id;
        pending.current = { session, text };
        setSelected(session);
        remember("it.session." + employee, session);
        void refreshList();
        setDraft(text);
        return;
      }
      await execute({ text, submission_id: crypto.randomUUID() }, session);
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法创建对话");
    }
  }
  const pending = useRef<{ session: string; text: string } | null>(null);
  useEffect(() => {
    if (!loading && selected && pending.current?.session === selected) {
      const queued = pending.current;
      pending.current = null;
      void execute(
        { text: queued.text, submission_id: crypto.randomUUID() },
        selected,
      );
    }
  }, [loading, selected]); // execution is scoped to the selected employee and session
  async function retry() {
    const saved = last.current;
    if (
      !saved ||
      saved.employee !== employee ||
      saved.session !== selected ||
      busy
    )
      return;
    await execute(saved.body, saved.session);
  }
  async function selectIssue(id: string) {
    if (!selected) return;
    try {
      setView(
        await api<View>("/sessions/" + selected + "/selection", employee, {
          method: "POST",
          body: JSON.stringify({ issue_id: id }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法切换问题");
    }
  }
  async function action(kind: "confirm" | "reopen", id: string) {
    if (!selected || busy) return;
    await execute(
      {
        text:
          kind === "confirm"
            ? "我确认这个问题已经解决。"
            : "这个问题又出现了，请重新调查。",
        submission_id: crypto.randomUUID(),
        action: { kind, issue_id: id },
      },
      selected,
    );
  }
  async function openSource(id: string) {
    try {
      const data = await api<Record<string, unknown>>(
        "/library/" + id,
        employee,
      );
      setDialog({
        title: typeof data.title === "string" ? data.title : id,
        subtitle: id + " · 合成公司资料快照",
        kind: "source",
        value: data,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取资料");
    }
  }
  const details = () =>
    setDialog({
      title: "运行详情",
      subtitle: "当前会话的实际记录；不包含模型内部思考。",
      kind: "details",
      value: {
        员工: employee,
        会话: selected,
        模型: bootstrap?.model,
        构建: bootstrap?.build,
        日志批次: bootstrap?.run_id,
        资料快照: bootstrap?.snapshot_at,
        最近状态: view.latest?.status,
        统计: view.latest?.response?.stats,
        查询记录: view.tools,
      },
    });
  const refresh = async () => {
    try {
      setError("");
      if (selected) {
        const v = await api<View>("/sessions/" + selected, employee);
        setView(v);
        setBusy(v.busy);
      }
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新失败");
    }
  };
  return (
    <div className="workbench">
      <Sidebar
        bootstrap={bootstrap}
        employee={employee}
        sessions={sessions}
        selected={selected}
        search={search}
        setSearch={setSearch}
        onSelect={selectSession}
        onNew={newConversation}
        onEmployee={switchEmployee}
        onDetails={details}
        open={leftOpen}
        onClose={() => setLeftOpen(false)}
      />
      <main className="main-panel">
        <header className="main-header">
          <button
            className="icon-button menu-toggle"
            aria-label="打开对话列表"
            onClick={() => setLeftOpen(true)}
          >
            <Menu size={20} />
          </button>
          <div className="header-title">
            <h1>{issue?.title || "一起解决工作中的 IT 问题"}</h1>
            <p>{loading ? "正在读取记录…" : "一起找到原因，让工作继续。"}</p>
          </div>
          <div className="header-actions">
            <button
              className="icon-button"
              onClick={() => void refresh()}
              aria-label="刷新进展"
              title="刷新进展"
            >
              <RefreshCw size={15} />
            </button>
            <button className="details-button" onClick={details}>
              <Settings2 size={14} />
              <span>运行详情</span>
            </button>
            <button
              className="icon-button context-toggle"
              aria-label="打开问题概览"
              onClick={() => setRightOpen(true)}
            >
              <PanelRight size={19} />
            </button>
          </div>
        </header>
        {bootstrap && !bootstrap.model_configured && (
          <div className="configuration-notice">
            模型尚未配置。可以查看历史记录，配置完成后即可继续对话。
          </div>
        )}
        <ChatThread
          name={employeeInfo?.name || ""}
          messages={view.messages}
          live={live}
          busy={busy}
          activeTool={activeTool}
          onSuggest={(text) => {
            setDraft(text);
            document.querySelector<HTMLTextAreaElement>("textarea")?.focus();
          }}
          onSource={(id) => void openSource(id)}
        />
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={() => void send()}
          busy={busy}
          disabled={loading || !bootstrap?.model_configured}
          readOnly={view.read_only}
          onNew={newConversation}
          error={error}
          canRetry={
            !!last.current &&
            last.current.employee === employee &&
            last.current.session === selected
          }
          onRetry={() => void retry()}
        />
      </main>
      <ContextPanel
        view={view}
        issue={issue}
        busy={busy}
        activeTool={activeTool}
        open={rightOpen}
        onClose={() => setRightOpen(false)}
        onSelect={(id) => void selectIssue(id)}
        onAction={(kind, id) => void action(kind, id)}
        onDownload={(id) =>
          void downloadHandoff(id, employee).catch((e) => setError(e.message))
        }
        onSource={(id) => void openSource(id)}
        onTool={(tool) =>
          setDialog({
            title: toolLabel(tool.tool_name || "查询记录"),
            subtitle: "已保存的工具结果 · 当前员工可见",
            kind: "data",
            value: tool,
          })
        }
      />
      <Dialog data={dialog} onClose={closeDialog} />
    </div>
  );
}
