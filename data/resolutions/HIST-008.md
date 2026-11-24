---
{
  "id": "HIST-008",
  "title": "Identity recovery completed by support",
  "title_zh": "身份恢复完成后验证",
  "category": "account",
  "service_ids": [
    "sso"
  ],
  "tags": [
    "Employee had lost the MFA device.",
    "身份恢复完成后验证"
  ],
  "updated_at": "2026-04-15T12:00:00Z",
  "valid_until": null,
  "environment": "Historical synthetic environment; recheck current device, permissions and health",
  "policy_refs": [
    "POL-003"
  ],
  "source_type": "synthetic_seed",
  "outcome": "resolved",
  "visibility": "shared",
  "synthetic": true,
  "resolved_at": "2026-04-15T12:00:00Z",
  "symptoms": "Employee had lost the MFA device.",
  "result": "Employee later confirmed successful sign-in. Handoff alone was not the resolution.",
  "evidence_refs": [
    "KB-ACC-003",
    "POL-003"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
Employee had lost the MFA device.

## Steps
- Prepared recovery request without collecting codes.
- Authorized support completed verification and recovery outside the agent.

## Result (employee-reported)
Employee later confirmed successful sign-in. Handoff alone was not the resolution.

Historical only: not proof of current state or a reusable authorization.
