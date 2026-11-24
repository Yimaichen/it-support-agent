# 合成资料契约

所有员工、服务、事件和历史都为合成数据，不代表真实企业环境。

- 固定参考时间：`2026-04-21T12:00:00Z`，见 `manifest.json`。这是模拟快照，不能用系统日期假装实时查询。
- 员工／政策／状态／变更：JSON；知识库和预置成功案例：带 YAML front matter 的 Markdown（JSON 格式元数据也是合法 YAML）。
- 字段和引用由 `src/data.py` 的 Pydantic 模型校验。运行 `uv run python -m src.data`。
- ID 在资料内全局唯一。文档引用服务和政策；案例引用实际存在的资料。
- `KB-APP-004` 刻意过期；`HIST-003` 的历史 VPN 5.0 与当前受支持 6.2 不同；Chicago CRM 故障不适用于 London。
- 预置案例只有已确认成功的合成案例；其中外部人员执行过的步骤不意味着 Agent 有该能力。不能用历史审批授权新请求。
- 工具失败由后续工具测试开关模拟，不把失败写成服务正常或无结果。

历史资料先用于编写和审查，再由持久化模块幂等导入 SQLite；不双向同步。

## 员工目录（数据契约版本 2）

`employees.json` 保留 5 位员工，当前共有 7 条设备记录。`EMP-001` 同时有 Windows 和 macOS 设备，`EMP-002` 同时有 macOS 和 Windows 设备。**数组顺序不表示主设备或当前故障设备**，必要时应询问用户。

每位员工包含：

```text
id、name、department、location、updated_at、synthetic
devices[]：id、os、updated_at
permissions[]：service_id、resource_type、resource_id、role、updated_at
```

权限是精确资源上的目录快照，例如：

```json
{
  "service_id": "tableau",
  "resource_type": "project",
  "resource_id": "research_reporting",
  "role": "viewer",
  "updated_at": "2026-04-21T12:00:00Z"
}
```

- 这条记录只涉及 `research_reporting` 项目，不表示能访问 `finance_reporting` 或所有 Tableau 内容。
- `resource_type` 只允许 `service`、`project`、`database`、`network`；`resource_id` 必须非空且不含 `*` 通配符。服务级记录也只对应指定的服务资源实例。
- 目录角色不是完整 RBAC 判定，不能据此批准、授予权限或推断有效审批。未列出的资源代表缺少记录，不自动等于已确认拒绝访问。
- 同一员工不能有重复设备 ID，或重复的“服务＋资源类型＋资源 ID＋角色”记录；设备／权限更新时间不能晚于员工快照，所有快照不晚于固定参考时间。
- 设备及权限列表允许为空，表示没有可用目录记录；不能凭空补齐。设备 OS 也可能过期，不能只凭目录假定本次环境。
- 版本 2 不再接受旧的单个 `device`／`os` 字段和服务到角色列表的 `permissions` 字典，以免静默扩大权限范围。`manifest.json` 的版本用于声明数据格式；SQLite 表结构不变，原会话不重写。

本次只增强员工数据，**不增加审批记录或审批查询工具**。审批状态回流独立列为 SPEC §17.6／TODO OPT07 的可选设计。


## 流程适用性修订
政策的可选 `applicability` 文本说明适用范围和相邻流程的关系，查询按 ID 或服务均完整返回。KB-ACC-005 整理自既有 POL-008 的交接、禁止继续普通排查与禁止索取秘密要求，不新增组织授权。材料仍是固定 2026-04-21 合成快照；事后凭据处置步骤未定义，明确保持未知。口头批准可记为员工报告，不等于正式核验；不新增材料格式或目录变更前置条件。
