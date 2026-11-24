import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleAlert,
  FileText,
  LoaderCircle,
  RotateCcw,
  X,
} from "lucide-react";
import { useState } from "react";
import type { Issue, Tool, View } from "../types";
import { toolLabel } from "../types";
import { EmptyHint, Status } from "./Sidebar";
import { Markdown } from "./Conversation";
export function extractSources(tools: Tool[]) {
  const ids = new Map<string, string>();
  const walk = (v: unknown) => {
    if (typeof v === "string") {
      for (const match of v.matchAll(/\b(?:KB-(?:[A-Z]+-)*\d+|POL-\d+)\b/g))
        if (!ids.has(match[0])) ids.set(match[0], match[0]);
    } else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") {
      const item = v as Record<string, unknown>;
      if (typeof item.id === "string" && /^(?:KB-(?:[A-Z]+-)*\d+|POL-\d+)$/.test(item.id))
        ids.set(item.id, typeof item.title === "string" ? item.title : item.id);
      Object.values(item).forEach(walk);
    }
  };
  tools.forEach((t) => {
    walk(t.sources);
    walk(t.data);
  });
  return [...ids].map(([id, title]) => ({ id, title }));
}
interface Props {
  view: View;
  issue: Issue | undefined;
  busy: boolean;
  activeTool: string;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onAction: (kind: "confirm" | "reopen", id: string) => void;
  onDownload: (id: string) => void;
  onSource: (id: string) => void;
  onTool: (tool: Tool) => void;
}
export function ContextPanel(p: Props) {
  const [confirm, setConfirm] = useState(false);
  const handoff = p.view.handoffs.find((h) => h.issue_id === p.issue?.issue_id);
  const tools = p.view.tools.filter(
    (t) => !p.issue || t.issue_id === p.issue.issue_id || !t.issue_id,
  );
  const sources = extractSources(tools);
  return (
    <>
      {p.open && (
        <button
          className="scrim context-scrim"
          aria-label="关闭问题概览"
          onClick={p.onClose}
        />
      )}
      <aside
        className={"context-panel" + (p.open ? " is-open" : "")}
        aria-label="问题概览"
      >
        <div className="context-title">
          <h2>问题概览</h2>
          <button
            className="icon-button context-close"
            onClick={p.onClose}
            aria-label="关闭问题概览"
          >
            <X size={18} />
          </button>
        </div>
        <div className="context-scroll">
          <section className="context-section">
            <span className="section-caption">当前问题</span>
            {p.issue ? (
              <>
                <h3>{p.issue.title}</h3>
                {p.view.issues.length > 1 && (
                  <label className="issue-select">
                    <select
                      aria-label="切换当前问题"
                      value={p.issue.issue_id}
                      disabled={p.busy || p.view.read_only}
                      onChange={(e) => {
                        setConfirm(false);
                        p.onSelect(e.target.value);
                      }}
                    >
                      {p.view.issues.map((i) => (
                        <option key={i.issue_id} value={i.issue_id}>
                          {i.title}
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={13} />
                  </label>
                )}
                <Status status={p.issue.status} />
              </>
            ) : (
              <EmptyHint>
                描述你遇到的情况后，问题和调查进展会显示在这里。
              </EmptyHint>
            )}
          </section>
          <section className="context-section">
            <h4>调查记录</h4>
            {!tools.length && !p.activeTool ? (
              <p className="muted small-text">尚未开始查询。</p>
            ) : (
              <ol className="tool-timeline">
                {tools.slice(-7).map((t, i) => (
                  <li key={i}>
                    <span
                      className={
                        "timeline-node" + (t.ok === false ? " failed" : "")
                      }
                    >
                      {t.ok === false ? (
                        <CircleAlert size={13} />
                      ) : (
                        <Check size={13} />
                      )}
                    </span>
                    <button onClick={() => p.onTool(t)}>
                      {toolLabel(t.tool_name || "查询")}
                      <ArrowUpRight size={12} />
                    </button>
                  </li>
                ))}
                {p.busy && (
                  <li className="active-tool">
                    <LoaderCircle size={13} className="spin" />
                    <span>{p.activeTool || "正在整理结果…"}</span>
                  </li>
                )}
              </ol>
            )}
            {tools.length > 7 && (
              <span className="muted tiny-text">
                最近 7 条 · 完整记录见运行详情
              </span>
            )}
          </section>
          {sources.length > 0 && (
            <section className="context-section">
              <h4>参考资料</h4>
              <div className="source-list">
                {sources.map((s) => (
                  <button key={s.id} onClick={() => p.onSource(s.id)}>
                    <span>{s.title}</span>
                    <small>{s.id} · 合成资料快照</small>
                    <ArrowUpRight size={13} />
                  </button>
                ))}
              </div>
            </section>
          )}
          {handoff && (
            <section className="context-section handoff-section">
              <h4>
                <FileText size={14} />
                人工交接
              </h4>
              <span className="status-tag saved">
                <Check size={12} />
                已保存到本地
              </span>
              <p className="handoff-team">
                建议团队 · {handoff.suggested_team || "待确认"}
              </p>
              <details>
                <summary>
                  查看完整摘要
                  <ChevronDown size={13} />
                </summary>
                <Markdown text={handoff.summary} onSource={p.onSource} />
              </details>
              {handoff.approval_routes?.map((r, i) => (
                <p className="approval-route" key={i}>
                  {r.resource || r.services?.join("、")}
                  <br />
                  <span>{r.approval_team}</span>
                </p>
              ))}
              <p className="handoff-coverage">
                {handoff.summary_stale
                  ? "摘要未覆盖最新消息；完整对话附件可供查看。"
                  : "摘要已覆盖保存时的消息。"}
              </p>
              <button
                className="secondary-button full-width"
                onClick={() => p.onDownload(handoff.issue_id)}
              >
                <ArrowDownToLine size={14} />
                下载交接记录
              </button>
            </section>
          )}
          {p.issue && !p.view.read_only && (
            <section className="case-actions">
              {p.issue.status === "resolved" ? (
                <>
                  <p className="resolved-note">
                    <Check size={14} />
                    这个问题已由你确认解决。
                  </p>
                  <button
                    className="secondary-button full-width"
                    disabled={p.busy}
                    onClick={() => p.onAction("reopen", p.issue!.issue_id)}
                  >
                    <RotateCcw size={14} />
                    问题又出现了
                  </button>
                </>
              ) : confirm ? (
                <div className="confirm-inline">
                  <p>确认目前已经恢复正常？</p>
                  <div>
                    <button
                      className="primary-button"
                      disabled={p.busy}
                      onClick={() => {
                        setConfirm(false);
                        p.onAction("confirm", p.issue!.issue_id);
                      }}
                    >
                      确认已解决
                    </button>
                    <button
                      className="text-button"
                      onClick={() => setConfirm(false)}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  className="secondary-button full-width"
                  disabled={p.busy}
                  onClick={() => setConfirm(true)}
                >
                  确认这个问题已解决
                </button>
              )}
            </section>
          )}
        </div>
        <p className="context-footnote">
          交接记录仅保存在本地。
          <br />
          保存不会通知真实 IT 团队。
        </p>
      </aside>
    </>
  );
}
