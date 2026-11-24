---
{
  "id": "HIST-010",
  "title": "Regional incident recovery confirmed",
  "title_zh": "区域故障恢复后确认",
  "category": "application",
  "service_ids": [
    "salesforce"
  ],
  "tags": [
    "Earlier regional CRM incident affected an employee.",
    "区域故障恢复后确认"
  ],
  "updated_at": "2026-04-15T12:00:00Z",
  "valid_until": null,
  "environment": "Historical synthetic environment; recheck current device, permissions and health",
  "policy_refs": [
    "POL-007"
  ],
  "source_type": "synthetic_seed",
  "outcome": "resolved",
  "visibility": "shared",
  "synthetic": true,
  "resolved_at": "2026-04-15T12:00:00Z",
  "symptoms": "Earlier regional CRM incident affected an employee.",
  "result": "After service team recovery, employee confirmed workflow restored. This does not prove the current Chicago incident recovered.",
  "evidence_refs": [
    "KB-APP-001",
    "POL-007"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
Earlier regional CRM incident affected an employee.

## Steps
- Matched region and active incident.
- Waited for external recovery rather than resetting credentials.

## Result (employee-reported)
After service team recovery, employee confirmed workflow restored. This does not prove the current Chicago incident recovered.

Historical only: not proof of current state or a reusable authorization.
