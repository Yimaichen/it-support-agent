# Offline BM25 retrieval results

Split: `tuning`; cutoff: 5; queries: 20; retrieval errors: 0.
Recall@5: **1.000000**; MRR@5: **0.950000**.

Actual offline retrieval, not model evaluation. Fresh seed-only temporary SQLite.
Macro recall includes all relevant labels; MRR is truncated at 5. Empty/error queries score zero.
Predefined paraphrases are not blind/new-intent evaluation. Unjudged candidates are not proven irrelevant.

| Split | Corpus | Language | N | Hits@5 | Recall@5 | MRR@5 | Errors |
|---|---|---|---:|---:|---:|---:|---:|
| tuning | knowledge | en | 5 | 5 | 1.000000 | 0.900000 | 0 |
| tuning | knowledge | zh | 5 | 5 | 1.000000 | 1.000000 | 0 |
| tuning | resolutions | en | 5 | 5 | 1.000000 | 0.900000 | 0 |
| tuning | resolutions | zh | 5 | 5 | 1.000000 | 1.000000 | 0 |

## Incomplete recall / errors

None in this query set.

## Non-top-1 results (not all are top-5 misses)

| Query ID | Query | First relevant rank | Retrieved IDs in order |
|---|---|---:|---|
| Q-03-tuning-en | VPN disconnects client version | 2 | KB-CON-002, KB-CON-001, KB-CON-004, KB-CON-003, KB-ACC-005 |
| Q-08-tuning-en | VPN upgrade fixed disconnects | 2 | HIST-004, HIST-003, HIST-009 |

## Snapshot fingerprints

- data_reference_time: `2026-04-21T12:00:00+00:00`
- corpus_sha256: `8997601342f2ee5a0e3adb9135cd43a6cc7e7fa96ee3644b535741ef3dff5331`
- queries_sha256: `47231eb42f9bc0b3e2477fb4b9baac9c4cc1c0b6edd7dac6eb8e085cc6eea70c`
- retrieval_source_sha256: `41b730af8fbc077e4733d21071a1f1ee33270faa93dea99a75fb265613337508`
- uv_lock_sha256: `20a8ff2ff8d1e81428d9168c143116bc4901ec46be7b106570ec8b83dc467c4b`
- evaluator_source_sha256: `818e4d59f2418b45fb93a496e5b00f9ca9c4fac842fcd7690cfb4830d46702b4`
- storage_source_sha256: `39b780f1a25d2122d3ec2540e408d32c55aaeb006c9f130806debbd93d95499f`
