---
{
  "id": "HIST-001",
  "title": "Password self-service restored sign-in",
  "title_zh": "自助重置恢复登录",
  "category": "account",
  "service_ids": [
    "sso"
  ],
  "tags": [
    "Employee reported incorrect password, not a lockout.",
    "自助重置恢复登录"
  ],
  "updated_at": "2026-04-15T12:00:00Z",
  "valid_until": null,
  "environment": "Historical synthetic environment; recheck current device, permissions and health",
  "policy_refs": [
    "POL-001"
  ],
  "source_type": "synthetic_seed",
  "outcome": "resolved",
  "visibility": "shared",
  "synthetic": true,
  "resolved_at": "2026-04-15T12:00:00Z",
  "symptoms": "Employee reported incorrect password, not a lockout.",
  "result": "Employee reported successful sign-in after reset.",
  "evidence_refs": [
    "KB-ACC-001",
    "POL-001"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
Employee reported incorrect password, not a lockout.

## Steps
- Checked healthy identity service.
- Guided approved self-service reset without collecting secrets.

## Result (employee-reported)
Employee reported successful sign-in after reset.

Historical only: not proof of current state or a reusable authorization.
