---
{
  "id": "HIST-003",
  "title": "Old VPN client upgrade resolved disconnects",
  "title_zh": "旧 VPN 升级解决断连",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "VPN disconnected on an old client before the baseline changed.",
    "旧 VPN 升级解决断连"
  ],
  "updated_at": "2026-04-15T12:00:00Z",
  "valid_until": null,
  "environment": "Historical synthetic environment; recheck current device, permissions and health",
  "policy_refs": [
    "POL-004"
  ],
  "source_type": "synthetic_seed",
  "outcome": "resolved",
  "visibility": "shared",
  "synthetic": true,
  "resolved_at": "2026-04-15T12:00:00Z",
  "symptoms": "VPN disconnected on an old client before the baseline changed.",
  "result": "Employee reported stable access at that historical time. Version 5.0 is NOT current guidance.",
  "evidence_refs": [
    "KB-CON-001",
    "POL-004"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
VPN disconnected on an old client before the baseline changed.

## Steps
- Confirmed the THEN-supported version was 5.0.
- Employee upgraded through approved portal to 5.0.

## Result (employee-reported)
Employee reported stable access at that historical time. Version 5.0 is NOT current guidance.

Historical only: not proof of current state or a reusable authorization.
