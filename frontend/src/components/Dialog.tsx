import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { Markdown } from "./Conversation";
export interface DialogData {
  title: string;
  subtitle?: string;
  kind: "source" | "data" | "details";
  value: unknown;
}
export function Dialog({
  data,
  onClose,
}: {
  data: DialogData | null;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!data) return;
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const items = ref.current?.querySelectorAll<HTMLElement>(
          'button,a[href],input,select,[tabindex="0"]',
        );
        if (!items?.length) return;
        const first = items[0],
          last = items[items.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === ref.current)
        ) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handle);
    return () => {
      document.removeEventListener("keydown", handle);
      previous?.focus();
    };
  }, [data, onClose]);
  if (!data) return null;
  const value = data.value as Record<string, unknown> | null;
  const body =
    data.kind === "source" && value
      ? Object.entries(value).filter(([k]) => !["id", "title"].includes(k))
      : [];
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="dialog"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        tabIndex={-1}
      >
        <header>
          <div>
            <h2 id="dialog-title">{data.title}</h2>
            {data.subtitle && <p>{data.subtitle}</p>}
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="关闭详情"
          >
            <X size={20} />
          </button>
        </header>
        <div className="dialog-body">
          {data.kind === "source" ? (
            body.map(([key, v]) => (
              <section className="source-field" key={key}>
                <h3>
                  {(
                    {
                      body: "正文",
                      content: "内容",
                      steps: "操作步骤",
                      service: "适用服务",
                      services: "适用服务",
                      category: "分类",
                      effective_from: "生效时间",
                      reviewed_at: "审核时间",
                      version: "版本",
                      owner: "负责人",
                      applies_to: "适用范围",
                      conditions: "适用条件",
                      rules: "规则",
                      updated_at: "更新时间",
                      required_approvers: "审批要求",
                      escalation_team: "交接团队",
                      prohibited_actions: "禁止操作",
                      allowed_actions: "允许操作",
                      description: "说明",
                      summary: "摘要",
                      tags: "标签",
                      title: "标题",
                    } as Record<string, string>
                  )[key] || key}
                </h3>
                {typeof v === "string" ? (
                  <Markdown text={v} />
                ) : (
                  <pre>{JSON.stringify(v, null, 2)}</pre>
                )}
              </section>
            ))
          ) : (
            <pre>{JSON.stringify(data.value, null, 2)}</pre>
          )}
        </div>
      </div>
    </div>
  );
}
