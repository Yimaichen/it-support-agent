import type { Progress, Turn } from "./types";
export async function api<T>(
  path: string,
  employee: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Employee-ID": employee,
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
export async function runTurn(
  session: string,
  employee: string,
  body: Turn,
  onEvent: (value: Progress) => void,
  signal: AbortSignal,
) {
  const response = await fetch(`/api/sessions/${session}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Employee-ID": employee },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `提交失败 (${response.status})`,
    );
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("无法读取响应，请刷新查看已保存记录");
  const decoder = new TextDecoder();
  let pending = "",
    settled = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let end: number;
      while ((end = pending.indexOf("\n\n")) >= 0) {
        const chunk = pending.slice(0, end);
        pending = pending.slice(end + 2);
        for (const line of chunk.split("\n"))
          if (line.startsWith("data: ")) {
            const event = JSON.parse(line.slice(6)) as Progress;
            onEvent(event);
            if (event.event === "done" || event.event === "error")
              settled = true;
          }
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (!settled)
    throw new Error("连接已断开；刷新查看进展，系统不会自动重复提交");
}
export async function downloadHandoff(issue: string, employee: string) {
  const data = await api<unknown>(`/handoffs/${issue}`, employee);
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `handoff-${issue}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
