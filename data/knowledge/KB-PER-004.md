---
{
  "id": "KB-PER-004",
  "title": "Tableau project permissions",
  "title_zh": "Tableau 项目权限",
  "category": "access",
  "service_ids": [
    "tableau",
    "grafana"
  ],
  "tags": [
    "dashboard forbidden; 仪表盘权限",
    "Tableau project permissions",
    "Tableau 项目权限",
    "grafana permissions access request"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-010",
    "POL-011"
  ]
}
---

# Tableau project permissions / Tableau 项目权限

## Symptoms / 症状
dashboard forbidden; 仪表盘权限

## Clarify / 澄清
Ask project, requested role and data classification.

## Safe investigation / 安全排查
Compare current employee role with requested scope. Follow Analytics Owner approval. Do not bypass row-level security or copy protected data.

## Verification / 验证
Employee must confirm authorized access after approval; current agent cannot provide it.

## Handoff / 交接
Create an approval handoff with minimum necessary context.

## Additional resource scope / 补充资源范围
Grafana internal dashboard access uses POL-011 in this synthetic organization. Record dashboard/project, requested role and data classification; Analytics Owner must verify approval. Grafana and Tableau are separate systems; an existing Tableau role does not grant Grafana access.
