---
{
  "id": "HIST-007",
  "title": "Fresh upstream and view refresh restored report",
  "title_zh": "上游正常视图刷新恢复报表",
  "category": "complex",
  "service_ids": [
    "data_pipeline",
    "tableau"
  ],
  "tags": [
    "View timestamp was old while verified upstream was fresh.",
    "上游正常视图刷新恢复报表"
  ],
  "updated_at": "2026-04-15T12:00:00Z",
  "valid_until": null,
  "environment": "Historical synthetic environment; recheck current device, permissions and health",
  "policy_refs": [
    "POL-006",
    "POL-007"
  ],
  "source_type": "synthetic_seed",
  "outcome": "resolved",
  "visibility": "shared",
  "synthetic": true,
  "resolved_at": "2026-04-15T12:00:00Z",
  "symptoms": "View timestamp was old while verified upstream was fresh.",
  "result": "Employee confirmed expected current timestamp.",
  "evidence_refs": [
    "KB-CMP-003",
    "POL-006",
    "POL-007"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
View timestamp was old while verified upstream was fresh.

## Steps
- Checked Tableau and pipeline independently.
- Employee refreshed the view via normal UI.

## Result (employee-reported)
Employee confirmed expected current timestamp.

Historical only: not proof of current state or a reusable authorization.
