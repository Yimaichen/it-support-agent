---
{
  "id": "KB-ACC-002",
  "title": "Locked account",
  "title_zh": "账号被锁定",
  "category": "account",
  "service_ids": [
    "sso"
  ],
  "tags": [
    "ACCOUNT_LOCKED; 账号锁定; too many attempts",
    "Locked account",
    "账号被锁定"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-002"
  ]
}
---

# Locked account / 账号被锁定

## Symptoms / 症状
ACCOUNT_LOCKED; 账号锁定; too many attempts

## Clarify / 澄清
When did it start? Is ACCOUNT_LOCKED shown rather than incorrect password?

## Safe investigation / 安全排查
Stop repeated attempts. Check identity service health and current employee identity context. Explain that account unlock requires the approved verification process.

## Verification / 验证
Do not claim unlocked without a current employee confirmation after authorized support action.

## Handoff / 交接
Collect non-secret error and time for Identity Support. The agent cannot unlock accounts.
