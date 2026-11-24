---
{
  "id": "KB-ACC-003",
  "title": "MFA device unavailable",
  "title_zh": "MFA 设备丢失",
  "category": "account",
  "service_ids": [
    "sso"
  ],
  "tags": [
    "lost phone; MFA unavailable; 多因素认证设备丢失",
    "MFA device unavailable",
    "MFA 设备丢失"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-003"
  ]
}
---

# MFA device unavailable / MFA 设备丢失

## Symptoms / 症状
lost phone; MFA unavailable; 多因素认证设备丢失

## Clarify / 澄清
Is the old device available? Is there a suspected compromise? Do not ask for codes.

## Safe investigation / 安全排查
Check service health. Describe the approved recovery request and required identity verification. Do not disable MFA or offer a bypass.

## Verification / 验证
Recovery is not complete until approved support acts and the employee confirms sign-in.

## Handoff / 交接
Send a structured identity recovery handoff; suspicious activity uses Security Response.
