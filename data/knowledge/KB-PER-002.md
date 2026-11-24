---
{
  "id": "KB-PER-002",
  "title": "Salesforce access denied versus login",
  "title_zh": "Salesforce 拒绝访问与登录区分",
  "category": "access",
  "service_ids": [
    "salesforce",
    "sso"
  ],
  "tags": [
    "ACCESS_DENIED; CRM 权限不足",
    "Salesforce access denied versus login",
    "Salesforce 拒绝访问与登录区分"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-007"
  ]
}
---

# Salesforce access denied versus login / Salesforce 拒绝访问与登录区分

## Symptoms / 症状
ACCESS_DENIED; CRM 权限不足

## Clarify / 澄清
Can the employee sign in? Which function is denied?

## Safe investigation / 安全排查
Distinguish authentication from authorization. Check current directory and policy for the requested action. Do not reset passwords to fix a permission denial or infer rights from an old case.

## Verification / 验证
Only employee-confirmed legitimate access restoration counts as resolved.

## Handoff / 交接
If requested role lacks an applicable self-service policy, explain uncertainty and route to the application owner.
