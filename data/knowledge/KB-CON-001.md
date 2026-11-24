---
{
  "id": "KB-CON-001",
  "title": "VPN disconnects and client version",
  "title_zh": "VPN 断连与客户端版本",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "VPN disconnects every ten minutes; VPN 反复断开; CLIENT_UNSUPPORTED",
    "VPN disconnects and client version",
    "VPN 断连与客户端版本"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-004"
  ]
}
---

# VPN disconnects and client version / VPN 断连与客户端版本

## Symptoms / 症状
VPN disconnects every ten minutes; VPN 反复断开; CLIENT_UNSUPPORTED

## Clarify / 澄清
Ask exact current version, OS, start time and local network behavior.

## Safe investigation / 安全排查
Check gateway status and current supported baseline. Compare actual current version; a previous case used 5.0 but baseline is now 6.2. Guide upgrade only through the approved software portal.

## Verification / 验证
Employee reports a stable connection after an observation interval, not merely successful installation.

## Handoff / 交接
If supported client still disconnects, collect times and non-secret errors for Network Support.

## Employee steps / 员工操作
If your current VPN client is below the supported 6.2 baseline, upgrade using the approved company software portal. Do not install from an untrusted source or disable endpoint protection. Connect using the normal client UI and report whether the connection stays stable and the required internal resource opens. If the supported client still disconnects, report the time and non-secret error to Network Support.
