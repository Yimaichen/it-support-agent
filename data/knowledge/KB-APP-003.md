---
{
  "id": "KB-APP-003",
  "title": "Tableau view refresh",
  "title_zh": "Tableau 视图刷新",
  "category": "application",
  "service_ids": [
    "tableau",
    "data_pipeline"
  ],
  "tags": [
    "dashboard old; 报表旧数据; cached view",
    "Tableau view refresh",
    "Tableau 视图刷新"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-007"
  ]
}
---

# Tableau view refresh / Tableau 视图刷新

## Symptoms / 症状
dashboard old; 报表旧数据; cached view

## Clarify / 澄清
Compare the view timestamp and upstream freshness watermark. Is only one view affected?

## Safe investigation / 安全排查
Check Tableau and pipeline separately. A healthy Tableau web page does not prove data freshness. Refresh only the view through standard UI if upstream data is fresh.

## Verification / 验证
Employee confirms the expected current timestamp and values; do not infer freshness from page availability.

## Handoff / 交接
If upstream is stale, collect lineage and use Data Platform handoff rather than repeated browser refresh.

## Employee steps / 员工操作
Only when the upstream freshness watermark is current, refresh the affected Tableau view using its normal UI. Compare the displayed timestamp and values with the expected data. If upstream data is stale or the view stays stale, report the timestamps for Data Platform rather than repeatedly refreshing.
