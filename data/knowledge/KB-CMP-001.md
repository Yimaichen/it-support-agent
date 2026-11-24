---
{
  "id": "KB-CMP-001",
  "title": "Correlate report regression with changes",
  "title_zh": "报表异常与变更关联",
  "category": "complex",
  "service_ids": [
    "jenkins",
    "data_pipeline",
    "tableau"
  ],
  "tags": [
    "report wrong since Friday maintenance; 周五维护后报表异常",
    "Correlate report regression with changes",
    "报表异常与变更关联"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-006"
  ]
}
---

# Correlate report regression with changes / 报表异常与变更关联

## Symptoms / 症状
report wrong since Friday maintenance; 周五维护后报表异常

## Clarify / 澄清
Ask report, last good timestamp, pipeline run IDs and impact.

## Safe investigation / 安全排查
Check each service plus recent changes. Compare maintenance window, run metadata and watermark. Temporal correlation is a hypothesis, not proof. Do not rerun jobs or edit connectors.

## Verification / 验证
Without a verified result, retain unresolved state even if a likely change is identified.

## Handoff / 交接
Send Data Platform the timeline, facts, hypotheses, missing run information and attempted read-only checks.
