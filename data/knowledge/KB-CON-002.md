---
{
  "id": "KB-CON-002",
  "title": "Local network versus VPN gateway",
  "title_zh": "本地网络与 VPN 网关",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "network unreachable; 公司网络连不上",
    "Local network versus VPN gateway",
    "本地网络与 VPN 网关"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-004"
  ]
}
---

# Local network versus VPN gateway / 本地网络与 VPN 网关

## Symptoms / 症状
network unreachable; 公司网络连不上

## Clarify / 澄清
Does ordinary internet work before connecting? Does another trusted network work?

## Safe investigation / 安全排查
Check service status. Ask the employee to verify ordinary connectivity and try a trusted network where permitted. Do not ask them to disable security software or use an untrusted hotspot.

## Verification / 验证
Employee confirms both ordinary connectivity and required internal access.

## Handoff / 交接
Repeated failures with healthy local network and gateway require Network Support.

## Employee steps / 员工操作
Check whether ordinary internet access works before connecting VPN. Where permitted, compare with another trusted network. Do not use an untrusted hotspot or disable security software. Report whether both ordinary connectivity and required internal access work; persistent failure needs Network Support.
