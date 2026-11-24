---
{
  "id": "KB-ACC-001",
  "title": "Password sign-in failure",
  "title_zh": "密码登录失败",
  "category": "account",
  "service_ids": [
    "sso"
  ],
  "tags": [
    "Incorrect password; 密码错误; INVALID_CREDENTIALS",
    "Password sign-in failure",
    "密码登录失败"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-001"
  ]
}
---

# Password sign-in failure / 密码登录失败

## Symptoms / 症状
Incorrect password; 密码错误; INVALID_CREDENTIALS

## Clarify / 澄清
Which sign-in page and exact non-secret error? Does the error say locked or MFA instead?

## Safe investigation / 安全排查
Check current identity service status. Guide the employee to the approved self-service reset entry from the company portal. Never ask them to paste a password or code.

## Verification / 验证
Ask the employee to retry sign-in and explicitly report whether access is restored.

## Handoff / 交接
If reset cannot be completed or the account is locked, use identity verification support rather than repeated login attempts.

## Employee steps / 员工操作
Use the approved password self-service entry in the company portal. Do not send your password or one-time code in chat. After completing the reset, try signing in and report whether access is restored. If reset fails or the account is locked, stop repeated attempts and request Identity Support.


## Applicability / 适用范围
This is routine incorrect-password recovery, not account unlock or unavailable-MFA recovery. Suspected disclosure or compromise requires POL-008 and the security-event guide KB-ACC-005. This routine procedure is not an authorized security-remediation sequence.
