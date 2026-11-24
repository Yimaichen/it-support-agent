export interface Employee {
  id: string;
  name: string;
  department: string;
  location: string;
}
export interface Bootstrap {
  employees: Employee[];
  model_configured: boolean;
  model: string;
  build: string;
  snapshot_at: string;
  run_id: string;
  identity_mode: string;
}
export interface Session {
  session_id: string;
  title: string;
  status: string;
  updated_at: string;
}
export interface Issue {
  issue_id: string;
  title: string;
  category: string;
  status: string;
  updated_at: string;
}
export interface Message {
  role: "user" | "assistant";
  content: string;
  display_kind?: string;
}
export interface Tool {
  tool_name?: string;
  ok?: boolean;
  issue_id?: string;
  queried_at?: string;
  message_id?: string;
  sources?: unknown[];
  arguments?: Record<string, unknown>;
  data?: unknown;
  error?: unknown;
}
export interface Handoff {
  handoff_id: string;
  issue_id: string;
  summary: string;
  suggested_team: string;
  summary_stale: boolean;
  impact: string;
  updated_at: string;
  approval_routes: {
    services: string[];
    resource: string;
    approval_team: string;
  }[];
  conversation: unknown[];
  query_audit: unknown[];
}
export interface Stats {
  model_attempts?: number;
  tool_calls?: number;
  elapsed_seconds?: number;
  reported_usage?: Record<string, number>;
  cost_currency?: string | null;
}
export interface View {
  messages: Message[];
  tools: Tool[];
  issues: Issue[];
  handoffs: Handoff[];
  latest: {
    status: string;
    submission_id: string;
    response: { stats?: Stats; reply?: string } | null;
  } | null;
  current_issue_id: string | null;
  busy: boolean;
  read_only: boolean;
}
export interface Turn {
  text: string;
  submission_id: string;
  action?: { kind: "confirm" | "reopen"; issue_id: string };
}
export interface Progress {
  event: string;
  data: {
    text?: string;
    tool?: string;
    message_index?: number;
    detail?: string;
    view?: View;
    result?: { status: string; reply?: string };
  };
}
export const statusLabels: Record<string, string> = {
  new: "新对话",
  investigating: "调查中",
  awaiting_user: "等待补充",
  awaiting_verification: "等待验证",
  awaiting_service_recovery: "等待恢复",
  resolved: "已确认解决",
  escalated: "交接已保存",
};
export const toolLabels: Record<string, string> = {
  search_knowledge_base: "搜索知识库",
  read_knowledge_document: "阅读知识库",
  check_service_status: "查看服务状态",
  get_current_user: "查看员工资料",
  search_resolution_history: "查找历史经验",
  read_resolution: "阅读历史记录",
  lookup_policy: "查看适用政策",
  get_my_support_history: "读取我的历史对话",
  manage_issue: "整理问题记录",
  save_handoff: "保存本地交接",
};
export function toolLabel(name: string) {
  return toolLabels[name] || name.replaceAll("_", " ");
}
export const EMPTY_VIEW: View = {
  messages: [],
  tools: [],
  issues: [],
  handoffs: [],
  latest: null,
  current_issue_id: null,
  busy: false,
  read_only: false,
};
