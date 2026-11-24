import {
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  FileText,
  LoaderCircle,
  MessageSquare,
  Plus,
  Search,
  Settings2,
  X,
} from "lucide-react";
import type { Bootstrap, Session } from "../types";
import { statusLabels } from "../types";
export function Mark({ small = false }: { small?: boolean }) {
  return (
    <span className={"brand-mark" + (small ? " small" : "")} aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <path
          d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M5.6 18.4L18.4 5.6"
          stroke="currentColor"
          strokeWidth="1.35"
        />
        <circle cx="12" cy="12" r="3" fill="currentColor" />
      </svg>
    </span>
  );
}
interface Props {
  bootstrap: Bootstrap | null;
  employee: string;
  sessions: Session[];
  selected: string | null;
  search: string;
  setSearch: (v: string) => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onEmployee: (id: string) => void;
  onDetails: () => void;
  open: boolean;
  onClose: () => void;
}
export function Sidebar(p: Props) {
  const person = p.bootstrap?.employees.find((e) => e.id === p.employee);
  const list = p.sessions.filter((s) =>
    s.title.toLowerCase().includes(p.search.toLowerCase()),
  );
  return (
    <>
      {p.open && (
        <button
          className="scrim sidebar-scrim"
          aria-label="关闭对话列表"
          onClick={p.onClose}
        />
      )}
      <aside
        className={"sidebar" + (p.open ? " is-open" : "")}
        aria-label="对话导航"
      >
        <div className="brand">
          <Mark />
          <div>
            <strong>IT 工作台</strong>
            <span>SERVICE DESK</span>
          </div>
          <button
            className="icon-button sidebar-close"
            onClick={p.onClose}
            aria-label="关闭对话列表"
          >
            <X size={19} />
          </button>
        </div>
        <button className="new-conversation" onClick={p.onNew}>
          <Plus size={17} />
          发起新对话
        </button>
        <div className="history-heading">
          我的对话<span>{p.sessions.length || ""}</span>
        </div>
        <label className="session-search">
          <Search size={15} />
          <input
            aria-label="搜索对话"
            placeholder="搜索对话"
            value={p.search}
            onChange={(e) => p.setSearch(e.target.value)}
          />
          {p.search && (
            <button onClick={() => p.setSearch("")} aria-label="清除搜索">
              <X size={13} />
            </button>
          )}
        </label>
        <nav className="session-list">
          {list.map((s) => (
            <button
              key={s.session_id}
              className={
                "session-row" + (p.selected === s.session_id ? " selected" : "")
              }
              onClick={() => p.onSelect(s.session_id)}
            >
              <span className="session-name">
                <MessageSquare size={14} />
                {s.title}
              </span>
              <span className="session-status">
                {s.status === "resolved" ? (
                  <CheckCheck size={12} />
                ) : s.status === "escalated" ? (
                  <FileText size={12} />
                ) : null}
                {statusLabels[s.status] || s.status}
                {p.selected === s.session_id ? " · 当前会话" : ""}
              </span>
            </button>
          ))}
          {!list.length && (
            <p className="empty-history">
              {p.search ? "没有匹配的对话" : "你的对话会保存在这里。"}
            </p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <button className="sidebar-details" onClick={p.onDetails}>
            <Settings2 size={16} />
            运行详情
            <ArrowUpRight size={13} />
          </button>
          <div className="profile">
            <span className="avatar">
              {person?.name
                .split(" ")
                .map((x) => x[0])
                .slice(0, 2)
                .join("") || "IT"}
            </span>
            <div className="identity">
              <label htmlFor="employee-select">模拟员工</label>
              <div className="employee-control">
                <select
                  id="employee-select"
                  value={p.employee}
                  onChange={(e) => p.onEmployee(e.target.value)}
                >
                  {p.bootstrap?.employees.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
                <ChevronDown size={12} />
              </div>
              <span>
                {person?.department || "本地演示"} · {p.employee}
              </span>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
export function Status({ status }: { status: string }) {
  return (
    <span className={"status-tag " + status}>
      {status === "resolved" ? (
        <Check size={12} />
      ) : status === "investigating" ? (
        <LoaderCircle size={12} />
      ) : null}
      {statusLabels[status] || status}
    </span>
  );
}
export function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="empty-hint">
      <CircleHelp size={15} />
      <p>{children}</p>
    </div>
  );
}
