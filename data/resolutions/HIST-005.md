---
{
  "id": "HIST-005",
  "title": "Authorized access restored after owner approval",
  "title_zh": "获批后权限恢复",
  "category": "access",
  "service_ids": [
    "tableau"
  ],
  "tags": [
    "Tableau project viewer scope was missing.",
    "获批后权限恢复"
  ],
  "updated_at": "2026-04-15T12:00:00Z",
  "valid_until": null,
  "environment": "Historical synthetic environment; recheck current device, permissions and health",
  "policy_refs": [
    "POL-010"
  ],
  "source_type": "synthetic_seed",
  "outcome": "resolved",
  "visibility": "shared",
  "synthetic": true,
  "resolved_at": "2026-04-15T12:00:00Z",
  "symptoms": "Tableau project viewer scope was missing.",
  "result": "Employee confirmed authorized view access. This old approval does not authorize future requests.",
  "evidence_refs": [
    "KB-PER-004",
    "POL-010"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
Tableau project viewer scope was missing.

## Steps
- Collected required project and approval information.
- Outside this agent, Analytics Owner approved and applied the requested scope.

## Result (employee-reported)
Employee confirmed authorized view access. This old approval does not authorize future requests.

Historical only: not proof of current state or a reusable authorization.
