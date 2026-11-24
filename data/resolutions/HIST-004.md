---
{
  "id": "HIST-004",
  "title": "Trusted network restored connectivity",
  "title_zh": "可信网络恢复连接",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "Ordinary internet was unavailable before VPN connection.",
    "可信网络恢复连接"
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
  "symptoms": "Ordinary internet was unavailable before VPN connection.",
  "result": "Employee confirmed internet and VPN access.",
  "evidence_refs": [
    "KB-CON-002",
    "POL-004"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
Ordinary internet was unavailable before VPN connection.

## Steps
- Checked gateway healthy.
- Employee restored ordinary network connectivity on a trusted network.

## Result (employee-reported)
Employee confirmed internet and VPN access.

Historical only: not proof of current state or a reusable authorization.
