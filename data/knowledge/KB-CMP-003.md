---
{
  "id": "KB-CMP-003",
  "title": "Trace stale report safely",
  "title_zh": "安全追踪报表数据新鲜度",
  "category": "complex",
  "service_ids": [
    "data_pipeline",
    "tableau"
  ],
  "tags": [
    "stale dashboard despite refresh; 刷新仍是旧数据",
    "Trace stale report safely",
    "安全追踪报表数据新鲜度"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-006",
    "POL-007"
  ]
}
---

# Trace stale report safely / 安全追踪报表数据新鲜度

## Symptoms / 症状
stale dashboard despite refresh; 刷新仍是旧数据

## Clarify / 澄清
Ask expected data time, view time and upstream watermark.

## Safe investigation / 安全排查
Separate presentation cache from stale upstream data. If upstream is fresh, normal view refresh is safe; if not, gather lineage and stop repeated refresh attempts.

## Verification / 验证
Employee confirms expected data timestamp after a safe refresh; otherwise no resolution claim.

## Handoff / 交接
Escalate upstream delay with report ID placeholders and sanitized times.

## Employee steps / 员工操作
If upstream data is current, refresh the view through its normal UI and check the expected data timestamp. If upstream is stale or refreshing does not restore the expected data, report the view timestamp, expected timestamp and non-sensitive report reference for Data Platform.
