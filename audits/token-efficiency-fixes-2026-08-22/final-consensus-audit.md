# Final consensus audit: Taskledger token efficiency

Date: 2026-08-22  
Audit type: `general_audit`  
Reviewers: 5 identical, independent reviewers  
Quorum: 60% (3 of 5)

## Executive conclusion

The usage anomaly is valid, but the supplied totals prove concentration and runaway context replay rather than proposal-level causation. All five reviewers agreed that Taskledger should reduce repeated broad state transport and low-information turns without weakening its authority, recovery, or independent-verification floor.

The quorum-supported implementation is deliberately narrower than the original proposal set: add a compact current-state resume view while retaining full recovery for cold/lost baselines and uncertainty; remove plaintext worker credentials and duplicate context from assignment responses; serve the persisted assignment snapshot with separate dynamic state; add an exact-OID bounded reviewer packet; expose accurately computed ready requirements; and reduce seven default conversational confirmations to two approvals that preserve the same decisions. Instrumentation must be privacy-safe and split between Taskledger byte/command metadata and the model host's token/credit counters.

Adaptive assurance and batch verification do not yet cross the implementation threshold. Assurance tiers are deferred until risk classification and outcome telemetry exist. Batch verification is deferred until the ready view shows meaningful residual call overhead.

## Normalized consensus findings

| ID | Finding | Support | Confidence | Disposition |
|---|---|---:|---|---|
| CA-001 | `project resume` must be a preflighted compact projection of current authoritative state, not audit-event replay. Full `recover` remains required for a new/lost baseline and uncertain operations. | 5/5 | High | Implement modified proposal |
| CA-002 | Assignment creation should return path/hash metadata, not plaintext credentials or full context; worker context should serve the persisted snapshot with a separately hashed dynamic overlay. | 5/5 | High | Implement modified proposal |
| CA-003 | An orchestrator-only `submission review-context` is the strongest quality-preserving consolidation, provided it binds exact OIDs, exposes completeness, and never replaces diff inspection/tests. | 5/5 | High | Implement |
| CA-004 | A trustworthy ready-requirement view is safe and useful; batch mutation requires per-item evidence and atomic semantics and is not justified by the current 20-call sample. | 5/5 | High | Implement ready view; defer batch |
| CA-005 | Seven separate chat gates are skill policy, not a product invariant. Two default approvals may preserve all decision content while avoiding five accumulated-context round trips. | 5/5 | High | Implement modified proposal |
| CA-006 | Taskledger cannot authoritatively observe model tokens or credits. Record only compact command/byte metadata locally; correlate host counters externally and never log content or secrets. | 5/5 | High | Document boundary; avoid fabricated credit accounting |
| CA-007 | Lean/standard/critical assurance is under-specified and could weaken review. Every tier would need the same exact-OID, criterion, blocker, integration, and recovery floor. | 5/5 | High | Defer |
| CA-008 | Aggregate command counts do not establish causal savings. Assignment duplication is a security/correctness cleanup but cannot explain billion-token use by itself. | 5/5 | High | Treat savings as hypotheses; benchmark separately |

## Disputed or qualified points

- Reviewers differed on whether later batch verification should be all-or-none or per-item atomic. No batch command is approved in this release.
- Reviewers differed on whether assurance profiles should be piloted soon or fully deferred. Quorum agrees they must not be introduced before telemetry and an objective risk rubric.
- Exact numeric payback thresholds varied (5-20% workflow savings, 30-90 day payback, or frequency bands). The shared rule is non-inferior quality plus measured lifecycle savings exceeding implementation, migration, and expected rework cost.

## Tipping point

The practical tipping point is reached when another pass is unlikely to produce evidence that can change a named criterion or close a named proof gap. Stop broad replay and restart from bounded durable state after either:

- two repeated broad state loads with no cursor/state advance;
- three model/tool cycles with no new ledger fact, repository fact, test result, or user decision;
- a second compaction in one workflow span without durable progress; or
- task/span cost exceeding roughly 2-3 times the rolling median for comparable work.

Escalate regardless of token cost for credentials/auth, authorization boundaries, schema/data migration, recovery or Git mutation, concurrency, destructive/irreversible operations, or conflicting evidence about an invariant. The observed 88.8M-token, four-compaction task was well beyond the stop/reset threshold.

## Implementation authorization

The audit authorizes CA-001 through CA-005 as the smallest coherent repository-local change set. CA-006 is implemented only as documentation and compact local measurement hooks where they do not add model-visible payload. CA-007 and batch mutation remain deferred.

## Required verification

Regression coverage must prove safe resume fallback, no credential/context leakage in assignment results, immutable snapshot hashing and dynamic-overlay invalidation, exact-OID review context with explicit bounds, ready-filter parity with verification preconditions, and preservation of explicit plan versus mutation/launch approval.
