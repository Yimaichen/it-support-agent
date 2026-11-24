---
{
  "id": "HIST-002",
  "title": "Browser session restored application",
  "title_zh": "浏览器会话修复",
  "category": "application",
  "service_ids": [
    "salesforce"
  ],
  "tags": [
    "Salesforce worked in approved private window but not original browser.",
    "浏览器会话修复"
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
  "symptoms": "Salesforce worked in approved private window but not original browser.",
  "result": "Employee confirmed original workflow worked.",
  "evidence_refs": [
    "KB-APP-002",
    "POL-007"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
Salesforce worked in approved private window but not original browser.

## Steps
- Checked service and regional applicability.
- Guided targeted site session refresh.

## Result (employee-reported)
Employee confirmed original workflow worked.

Historical only: not proof of current state or a reusable authorization.
