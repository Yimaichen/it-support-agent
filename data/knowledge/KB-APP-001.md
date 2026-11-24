---
{
  "id": "KB-APP-001",
  "title": "Regional Salesforce slowness",
  "title_zh": "Salesforce 区域性缓慢",
  "category": "application",
  "service_ids": [
    "salesforce"
  ],
  "tags": [
    "CRM slow in Chicago; 芝加哥 Salesforce 很慢",
    "Regional Salesforce slowness",
    "Salesforce 区域性缓慢"
  ],
  "updated_at": "2026-04-20T08:00:00Z",
  "valid_until": null,
  "environment": "Synthetic managed employee environment",
  "policy_refs": [
    "POL-007"
  ]
}
---

# Regional Salesforce slowness / Salesforce 区域性缓慢

## Symptoms / 症状
CRM slow in Chicago; 芝加哥 Salesforce 很慢

## Clarify / 澄清
Ask location, start time and whether colleagues are affected.

## Safe investigation / 安全排查
Compare current regional status with employee location and incident time. Matching Chicago egress incident is evidence; do not generalize it to all regions.

## Verification / 验证
For a matching incident, wait for recovery; do not mark resolved just because cause is known.

## Handoff / 交接
If symptoms do not match the incident, collect timings and affected actions before application support handoff.
