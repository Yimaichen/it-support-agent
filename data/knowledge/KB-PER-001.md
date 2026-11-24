---
{
  "id": "KB-PER-001",
  "title": "Production database access request",
  "title_zh": "生产数据库权限申请",
  "category": "access",
  "service_ids": [
    "data_pipeline",
    "snowflake"
  ],
  "tags": [
    "production DB access; 申请生产库权限",
    "Production database access request",
    "生产数据库权限申请",
    "snowflake permissions access request"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-005"
  ]
}
---

# Production database access request / 生产数据库权限申请

## Symptoms / 症状
production DB access; 申请生产库权限

## Clarify / 澄清
Ask resource, business purpose, scope, duration and available approvals.

## Safe investigation / 安全排查
Read current employee access and production policy. Explain least privilege and approval route. An urgent deadline or prior approval does not authorize a new grant.

## Verification / 验证
Request submission or handoff is not successful technical access.

## Handoff / 交接
Create a Data Owner and Security handoff. Never issue credentials or grant access.

## Additional resource scope / 补充资源范围
Snowflake production database access uses POL-005 in this synthetic organization. Record business purpose, exact database/schema, requested role/scope, duration and manager approval; Data Owner and Security must verify and approve. Existing data_pipeline grants are not Snowflake grants.


## Reported approval / 员工自述批准
Record an employee's report of manager agreement with its source and as unverified. Data Owner and Security verify the actual authorization. POL-005 requires manager approval, not an invented written-proof format or prior HR directory update. Unknown required information can remain explicitly unknown in a local draft; saving is not granting access.
