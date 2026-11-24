---
{
  "id": "KB-CON-003",
  "title": "VPN DNS name resolution",
  "title_zh": "VPN DNS 解析失败",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "NAME_NOT_RESOLVED; 域名解析失败",
    "VPN DNS name resolution",
    "VPN DNS 解析失败"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-004"
  ]
}
---

# VPN DNS name resolution / VPN DNS 解析失败

## Symptoms / 症状
NAME_NOT_RESOLVED; 域名解析失败

## Clarify / 澄清
Is VPN connected? Does the issue affect one internal service or all?

## Safe investigation / 安全排查
Check current gateway status. Collect non-sensitive error text; compare whether approved corporate resources resolve after reconnecting through standard UI. Do not change DNS settings or request internal host lists.

## Verification / 验证
Ask whether the required resource opens; connected icon alone is not enough.

## Handoff / 交接
Unresolved internal names after standard reconnect go to Network Support with sanitized evidence.

## Employee steps / 员工操作
Reconnect VPN through the normal client UI, then try opening the required approved corporate resource. Report the non-secret error if it still fails. Do not change DNS settings or share internal host lists; unresolved names need Network Support.
