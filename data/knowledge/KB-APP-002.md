---
{
  "id": "KB-APP-002",
  "title": "Browser-specific application failure",
  "title_zh": "单个浏览器应用异常",
  "category": "application",
  "service_ids": [
    "salesforce"
  ],
  "tags": [
    "one browser fails; 空白页面; stale session",
    "Browser-specific application failure",
    "单个浏览器应用异常"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-007"
  ]
}
---

# Browser-specific application failure / 单个浏览器应用异常

## Symptoms / 症状
one browser fails; 空白页面; stale session

## Clarify / 澄清
Does another approved browser or private window work? What error is shown?

## Safe investigation / 安全排查
Check current service status first. Try an approved private window without sharing cookies. If that works, guide a targeted site session refresh after warning about sign-out. Do not wipe all browser data.

## Verification / 验证
Ask whether the original workflow now works in the original browser.

## Handoff / 交接
Persistent failure with a healthy service: collect non-sensitive browser version and error for application support.

## Employee steps / 员工操作
Try the affected workflow in an approved browser's private window. Do not share cookies or session information. If it works there, refresh only that site's session in the original browser; this signs you out of that site. Do not clear all browser data. Retry the original workflow and report the result. If the private window also fails, report the browser version and non-secret error for application support.
