---
{
  "id": "KB-CON-004",
  "title": "Device-specific connection issue",
  "title_zh": "单设备连接异常",
  "category": "connectivity",
  "service_ids": [
    "vpn"
  ],
  "tags": [
    "only laptop fails; 只有我的电脑断网",
    "Device-specific connection issue",
    "单设备连接异常"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-004"
  ]
}
---

# Device-specific connection issue / 单设备连接异常

## Symptoms / 症状
only laptop fails; 只有我的电脑断网

## Clarify / 澄清
Ask OS, version and whether other approved devices on the same network work.

## Safe investigation / 安全排查
Use current directory as a recorded baseline, but confirm changes with the employee. Check supported client and gateway status. Historical device details may be stale.

## Verification / 验证
Employee validates the affected device rather than another machine.

## Handoff / 交接
No safe applicable step: hand off device context without remote control or admin changes.
