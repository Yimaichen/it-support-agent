---
{
  "id": "KB-ACC-004",
  "title": "Identity outage versus account issue",
  "title_zh": "身份服务故障与个人问题",
  "category": "account",
  "service_ids": [
    "sso"
  ],
  "tags": [
    "everyone cannot sign in; 多人无法登录",
    "Identity outage versus account issue",
    "身份服务故障与个人问题"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-001",
    "POL-008"
  ]
}
---

# Identity outage versus account issue / 身份服务故障与个人问题

## Symptoms / 症状
everyone cannot sign in; 多人无法登录

## Clarify / 澄清
Which services, locations and start time are affected?

## Safe investigation / 安全排查
Read current SSO status. If there is a matching active incident, explain it and avoid unnecessary password resets. A healthy snapshot does not prove an account is unlocked.

## Verification / 验证
Keep awaiting service recovery until the employee reports restored access.

## Handoff / 交接
No matching incident and unsafe symptoms require appropriate identity or security handoff.
