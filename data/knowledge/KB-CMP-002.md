---
{
  "id": "KB-CMP-002",
  "title": "Build success versus downstream data",
  "title_zh": "构建成功不代表下游数据正常",
  "category": "complex",
  "service_ids": [
    "jenkins",
    "data_pipeline"
  ],
  "tags": [
    "green build but missing data; 构建成功数据未到",
    "Build success versus downstream data",
    "构建成功不代表下游数据正常"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-006"
  ]
}
---

# Build success versus downstream data / 构建成功不代表下游数据正常

## Symptoms / 症状
green build but missing data; 构建成功数据未到

## Clarify / 澄清
Ask exact build ID and downstream run／dataset.

## Safe investigation / 安全排查
Check Jenkins and pipeline independently. Controller health and green job labels alone do not establish downstream delivery. Collect read-only metadata if available; otherwise mark it unknown.

## Verification / 验证
Only verify the actual downstream result, not service availability.

## Handoff / 交接
Missing lineage or production changes require Data Platform handoff.
