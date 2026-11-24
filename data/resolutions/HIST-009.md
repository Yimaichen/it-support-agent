---
{
  "id": "HIST-009",
  "title": "Device reconnect restored name resolution",
  "title_zh": "重连恢复域名解析",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "VPN connected icon appeared but approved corporate resource did not resolve.",
    "重连恢复域名解析"
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
  "symptoms": "VPN connected icon appeared but approved corporate resource did not resolve.",
  "result": "Employee confirmed required resource opened.",
  "evidence_refs": [
    "KB-CON-003",
    "POL-004"
  ]
}
---

# Synthetic successful case / 合成成功案例

## Symptoms
VPN connected icon appeared but approved corporate resource did not resolve.

## Steps
- Checked gateway.
- Employee reconnected using normal VPN UI, without DNS changes.

## Result (employee-reported)
Employee confirmed required resource opened.

Historical only: not proof of current state or a reusable authorization.
