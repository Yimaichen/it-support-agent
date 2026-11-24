import {
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  KeyRound,
  Laptop,
  LoaderCircle,
  ShieldCheck,
  Wifi,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import { Mark } from "./Sidebar";
export function Markdown({
  text,
  onSource,
}: {
  text: string;
  onSource?: (id: string) => void;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const source = href?.match(/(?:KB-(?:[A-Z]+-)*\d+|POL-\d+)/)?.[0];
            if (source && onSource)
              return (
                <button
                  className="inline-source"
                  onClick={() => onSource(source)}
                >
                  {children}
                  <ArrowUpRight size={12} />
                </button>
              );
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
const suggestions = [
  { icon: Wifi, title: "网络与 VPN", text: "VPN 连接不稳定，需要帮助排查。" },
  { icon: KeyRound, title: "账号与登录", text: "我的公司账号无法登录。" },
  { icon: ShieldCheck, title: "权限与访问", text: "我需要申请系统访问权限。" },
  { icon: Laptop, title: "应用与工具", text: "工作应用无法正常使用。" },
];
interface ThreadProps {
  name: string;
  messages: Message[];
  live: string;
  busy: boolean;
  activeTool: string;
  onSuggest: (text: string) => void;
  onSource: (id: string) => void;
}
export function ChatThread(p: ThreadProps) {
  const container = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  useEffect(() => {
    if (atBottom)
      container.current?.scrollTo({
        top: container.current.scrollHeight,
        behavior: "instant",
      });
  }, [p.messages, p.live, p.busy, atBottom]);
  return (
    <div className="thread-wrap">
      <div
        className="thread"
        ref={container}
        onScroll={() => {
          const e = container.current;
          if (e)
            setAtBottom(e.scrollHeight - e.scrollTop - e.clientHeight < 120);
        }}
      >
        {!p.messages.length && !p.busy ? (
          <section className="welcome">
            <span className="welcome-mark">
              <Mark />
            </span>
            <h1>你好，{p.name.split(" ")[0] || "同事"}。</h1>
            <h2>今天遇到了什么问题？</h2>
            <p>
              描述现象、开始时间，以及你已经尝试的方法。
              <br />
              我们一起找到原因，让工作继续。
            </p>
            <div className="suggestions">
              {suggestions.map((s) => (
                <button key={s.title} onClick={() => p.onSuggest(s.text)}>
                  <s.icon size={19} />
                  <span>{s.title}</span>
                  <ArrowUpRight size={15} />
                </button>
              ))}
            </div>
            <span className="welcome-footnote">
              <BookOpen size={14} />
              基于公司知识库与服务信息，逐步调查。
            </span>
          </section>
        ) : (
          <div className="conversation">
            <div className="conversation-start">当前对话</div>
            {p.messages.map((m, i) => (
              <article
                key={i}
                className={
                  "message " +
                  m.role +
                  (m.display_kind === "pause" ? " pause" : "")
                }
                aria-label={m.role === "user" ? "你的消息" : "IT 助手回复"}
              >
                {m.role === "assistant" && (
                  <>
                    <Mark small />
                    <div className="message-author">IT 助手</div>
                  </>
                )}
                <div className="message-body">
                  {m.role === "user" ? (
                    <p>{m.content}</p>
                  ) : (
                    <Markdown text={m.content} onSource={p.onSource} />
                  )}
                </div>
              </article>
            ))}
            {p.busy && (
              <article className="message assistant live">
                <Mark small />
                <div className="message-author">IT 助手</div>
                <div className="message-body" aria-live="polite">
                  {p.live ? (
                    <Markdown text={p.live} onSource={p.onSource} />
                  ) : (
                    <div className="working">
                      <LoaderCircle size={15} className="spin" />
                      {p.activeTool || "正在梳理问题…"}
                    </div>
                  )}
                </div>
              </article>
            )}
          </div>
        )}
      </div>
      {!atBottom && (
        <button
          className="scroll-bottom"
          onClick={() => {
            setAtBottom(true);
            container.current?.scrollTo({
              top: container.current.scrollHeight,
              behavior: "smooth",
            });
          }}
          aria-label="回到最新消息"
        >
          <ChevronDown size={17} />
        </button>
      )}
    </div>
  );
}
interface ComposerProps {
  value: string;
  onChange: (s: string) => void;
  onSend: () => void;
  busy: boolean;
  disabled: boolean;
  readOnly: boolean;
  onNew: () => void;
  error: string;
  canRetry: boolean;
  onRetry: () => void;
}
export function Composer(p: ComposerProps) {
  const input = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  useEffect(() => {
    if (input.current) {
      input.current.style.height = "auto";
      input.current.style.height =
        Math.min(input.current.scrollHeight, 170) + "px";
    }
  }, [p.value]);
  return (
    <footer className="composer-area">
      {p.error && (
        <div className="error-banner" role="alert">
          <span>{p.error}</span>
          {p.canRetry && (
            <button onClick={p.onRetry} disabled={p.busy}>
              重试本次提交
            </button>
          )}
        </div>
      )}
      {p.readOnly ? (
        <div className="readonly-banner">
          这份旧版历史仅供查看。
          <button onClick={p.onNew}>
            开始新对话
            <ArrowUpRight size={14} />
          </button>
        </div>
      ) : (
        <form
          className={"composer" + (p.busy ? " processing" : "")}
          onSubmit={(e) => {
            e.preventDefault();
            p.onSend();
          }}
        >
          <textarea
            ref={input}
            aria-label="描述你的 IT 问题"
            placeholder="告诉我现在的情况，或补充你已经尝试过的方法…"
            value={p.value}
            onChange={(e) => p.onChange(e.target.value)}
            disabled={p.disabled}
            onCompositionStart={() => (composing.current = true)}
            onCompositionEnd={() => (composing.current = false)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing &&
                !composing.current &&
                e.keyCode !== 229
              ) {
                e.preventDefault();
                if (!p.busy && !p.disabled && p.value.trim()) p.onSend();
              }
            }}
          />
          <div className="composer-controls">
            <span>
              {p.busy
                ? "正在处理，进展会自动保存"
                : "Enter 发送 · Shift + Enter 换行"}
            </span>
            <button
              className="send-button"
              type="submit"
              disabled={p.busy || p.disabled || !p.value.trim()}
              aria-label="发送消息"
            >
              {p.busy ? (
                <LoaderCircle size={17} className="spin" />
              ) : (
                <ArrowUp size={18} />
              )}
            </button>
          </div>
        </form>
      )}
      <p className="security-note">请勿输入密码、验证码、恢复码或访问令牌。</p>
    </footer>
  );
}
export function Dismiss({
  onClick,
  label = "关闭",
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <button className="icon-button" onClick={onClick} aria-label={label}>
      <X size={19} />
    </button>
  );
}
