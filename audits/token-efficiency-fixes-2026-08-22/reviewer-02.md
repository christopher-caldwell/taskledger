# Independent Audit — Token-Efficiency Proposals

Reviewer: `reviewer-02`  
Audit type: `general_audit`  
Date: 2026-08-22

## Scope and method

I audited the current dirty working tree as-is. I did not inspect the audit output directory or any other reviewer report. I traced the proposed changes through the current CLI dispatch, service read models, database schema, Codex skill, product specification, technical specification, and acceptance tests. I also ran `python3 -m unittest discover -s tests -v`; all 21 tests passed, with one existing `ResourceWarning` for an unclosed SQLite connection.

The supplied 45-day telemetry was treated as empirical input, but not as proof of causation. In particular, command invocation counts do not reveal response size, how many subsequent model turns retained a response, or which prompts caused compaction. The repository itself contains no token/credit telemetry with which to independently reproduce those totals.

## Executive assessment

The efficiency problem is credible and material: 1.410B of 1.575B cumulative tokens (89.5%) are attributed to the financial-tracker workload, and one 19-minute Terra task accumulated 88.8M tokens over 737 token-count events and four compactions. The current workflow repeatedly requests a full recovery graph, returns assignment context twice, and lacks a reviewer-specific read model. Those are sound targets.

However, the evidence does not establish that the seven proposals, individually, caused or will eliminate most of the 1.410B tokens. The 140,366 characters of stored assignment context are small relative to the aggregate; their cost can still amplify when copied into a long-lived prompt, but that amplification is not measured. Likewise, 118 `project recover` calls are suggestive but not enough without serialized response-size and prompt-retention data.

The smallest coherent implementation set is:

1. add response-size/token instrumentation and stable cursors;
2. remove raw tokens and full context from `assignment create`, then serve the authoritative stored assignment snapshot once by hash;
3. add a compact current-state `project resume`, while retaining full `project recover` for cold start and uncertainty;
4. add a bounded, exact-OID `submission review-context` that supplements, never replaces, independent diff inspection and tests;
5. expose exact requirement readiness and support a transactional batch verify operation with per-requirement evidence.

The gate collapse and assurance tiers belong in the orchestration policy, not in ledger authority. They should follow measurement and explicit risk classification. They must not relax product verification, authorization, blocker, integration, or recovery invariants.

## Findings

### TEF-001 — Aggregate telemetry establishes a hotspot, not proposal-level causation

- **Severity:** medium
- **Confidence:** high
- **Category:** evidence and measurement
- **Evidence:** Supplied metrics attribute 1.410B/1.575B cumulative tokens (89.5%) to the tracker and show a single 88.8M-token, 737-event, four-compaction task. Supplied command counts show 118 `project recover`, 115 `worker context`, and 35 `assignment create` calls. Assignment contexts total 140,366 characters (average 3,599; maximum 6,745). The code has no token/credit instrumentation; Taskledger emits command JSON only (`src/taskledger/cli.py:238`).
- **Impact:** Optimizing the wrong layer could add APIs and policy complexity while leaving the dominant repeated-prompt or auto-review cost intact. Raw command counts systematically understate retained-context amplification but cannot quantify it.
- **Recommendation:** Instrument serialized request/response bytes per command, response section sizes, model input/cached/output tokens at the orchestrator boundary, compactions, assurance tier, and task/submission IDs using pseudonymous IDs. Compare before/after cohorts. Do not log credentials, evidence contents, source excerpts, or raw prompts.
- **Proposal disposition:** **Modify proposal 7.** Measurement is prerequisite; automatic escalation rules should wait for attributable data.

### TEF-002 — `project resume --since-event` is valid only as a compact current-state read model, not audit-event replay

- **Severity:** high
- **Confidence:** high
- **Category:** recovery and state transport
- **Evidence:** `project recover` calls preflight and returns active specs, pending reviews, plan state, progress, all requirement rows, every task and assignment, questions, proposals, blockers, operations, and completion reasons (`src/taskledger/service.py:973-977`). The skill invokes it for every existing project load (`skills/taskledger/SKILL.md:14-16,63-66`). The CLI has no resume command (`src/taskledger/cli.py:156-208`). Audit events have a monotonic sequence (`src/taskledger/db.py:56`), but event payloads are sparse—for example, assignment activation records no assignment body (`src/taskledger/service.py:647`) and submission verification records only the outcome (`src/taskledger/service.py:815`). External repository/specification facts are reconciled during preflight, not reconstructible from event payloads (`src/taskledger/service.py:132-135`). Product recovery must remain complete and conversation-independent (`taskledger-product-spec.md:753-787`), and the technical recovery contract explicitly requires a self-contained snapshot (`taskledger-technical-spec.md:1845-1904`).
- **Impact:** Raw event deltas can omit authoritative current data, mislead an orchestrator after external Git changes, and undermine deterministic recovery. A compact current-state projection can substantially reduce repeated transport while preserving full recovery.
- **Recommendation:** Add `project resume` as an orchestrator-only, preflighted current-state projection containing phase, plan fingerprint, canonical HEAD, counts, next actions, and only actionable entities. Accept a project-scoped `since_event` cursor and return a new high-watermark, change hints, and `full_recovery_required` when the cursor is unknown, belongs to another project, predates an incompatible schema/projection version, or uncertainty exists. Never make the client reconstruct authority by folding sparse audit events. Keep `project recover` unchanged for a cold session, lost cursor, integrity uncertainty, or explicit operator request.
- **Proposal disposition:** **Modify proposal 1.** Accept the compact/resume split; reject audit-event replay as the authoritative delta mechanism.

### TEF-003 — Assignment handoff currently duplicates context, exposes a raw secret, and regenerates instead of serving its stored snapshot

- **Severity:** high
- **Confidence:** high
- **Category:** security, correctness, and context transport
- **Evidence:** `assignment_create` constructs and stores `context_json`, then returns both the raw `worker_token` and parsed full context (`src/taskledger/service.py:644-649`). The skill instructs the orchestrator to hand the returned context to the worker while also instructing it never to expose the raw token (`skills/taskledger/SKILL.md:43-45,53-55`). `worker_context` calls `self.context(a)` again rather than reading `a["context_json"]` (`src/taskledger/service.py:678-681`). That reconstruction reads current requirement revisions (`src/taskledger/service.py:605-609`), whereas the technical contract calls `context_json` an immutable assignment snapshot except after explicit `CONTINUE` (`taskledger-technical-spec.md:1305-1338`). Security controls require tokens redacted from logs and output surfaces (`taskledger-technical-spec.md:2017-2029`).
- **Impact:** The same context can enter the orchestrator prompt and worker prompt, then be resent by `worker context`. Returning the raw token enlarges the accidental-disclosure surface. Regeneration can silently diverge from the durable assigned snapshot, which is a correctness issue independent of token cost.
- **Recommendation:** Return assignment/task IDs, attempt, branch/worktree/base OID, `worker_token_path`, and `context_hash`; remove the raw token and full context. Have the worker authenticate from the path and fetch context once. Serve the stored `context_json` as the authoritative immutable snapshot. Return questions, blockers, and submissions as a separately hashed dynamic overlay. If conditional retrieval is added, define hashes over canonical bytes, compare in constant time only where secrets are involved, and return explicit `not_modified` plus both snapshot and overlay hashes. Do not use a single ETag for static context and mutable side state.
- **Proposal disposition:** **Accept proposal 2 with modification.** The static/dynamic split and use of stored `context_json` are required for correctness.

### TEF-004 — A bounded review context is the highest-value quality-preserving read model, but truncation must fail visibly

- **Severity:** high
- **Confidence:** high
- **Category:** verification quality
- **Evidence:** Submissions already persist the exact head OID, changed-file set, worker evidence, risks, questions, and follow-ups (`src/taskledger/service.py:713-762`; product specification `taskledger-product-spec.md:605-627`). Verification requires an explicit decision for every current criterion and four independent assertions (`src/taskledger/service.py:765-819`; `taskledger-product-spec.md:635-677`). The current CLI exposes only `submission verify`, not a reviewer read model (`src/taskledger/cli.py:197`). The skill correctly requires independent inspection and testing of the exact submitted commit and forbids executing worker-supplied command strings (`skills/taskledger/SKILL.md:46-48,56-57`).
- **Impact:** Without a review-focused projection, the reviewer must gather context from recovery output, database-adjacent commands, and the worker narrative, increasing repeated input and the chance of reviewing the wrong revision. Conversely, an aggressively truncated bundle can hide a blocker, risk, criterion, or changed file and degrade review quality.
- **Recommendation:** Add orchestrator-only `submission review-context` containing project/task/assignment/submission IDs; task and assignment revisions; base, submitted, and current canonical OIDs; merge-base/reachability facts; diffstat; complete changed-file metadata; complete criteria; linked current requirement statements; worker claims/evidence/risks/questions/follow-ups; applicable open blockers; and prior verification/correction state. Bound prose fields and optional history, not safety-critical sets. If any criteria, changed files, blockers, risks, or OIDs cannot fit, return structured truncation metadata and pagination handles and forbid acceptance from an incomplete context. The command must not include raw diffs, execute evidence commands, or itself assert semantic correctness. The skill must still independently inspect the exact diff and run appropriate tests.
- **Proposal disposition:** **Accept proposal 3 with modification.** Completeness markers and independent-review instructions are mandatory.

### TEF-005 — Assurance tiers may tune review breadth, but must never lower ledger acceptance invariants

- **Severity:** high
- **Confidence:** high
- **Category:** assurance policy
- **Evidence:** The product requires independent verification by an orchestrator-distinct context and explicit consideration of every criterion, intended behavior, required evidence, and blockers (`taskledger-product-spec.md:635-677`). The service enforces all criterion IDs and acceptance booleans for every accepted submission (`src/taskledger/service.py:774-790`). Taskledger is experimental and lacks the full failure-injection and 66-requirement conformance suite (`skills/taskledger/SKILL.md:79-81`; `README.md:140-144`).
- **Impact:** A vague “lean” tier can become a permission to skip exact-OID inspection, criterion coverage, or relevant tests precisely where model confidence is least reliable. A well-defined tier can still save tokens by reducing duplicate explanation, number of independent passes, and breadth of non-risk-based testing.
- **Recommendation:** Keep one invariant ledger acceptance contract for all tiers. Define assurance only in the skill/orchestrator: `lean` changes presentation and redundant checks for mechanically low-risk work; `standard` is the normal baseline; `critical` adds independent passes, failure injection, and broader evidence. Require explicit, recorded classification inputs. Force critical for authentication/credentials, authorization, schema migration/data integrity, operation journals/recovery, Git mutation, concurrency, destructive behavior, or security boundaries. Unknown risk defaults to standard, not lean. Reclassify upward on test failure, ambiguous evidence, unexpected diff scope, blocker/risk reports, stale OIDs, or repeated correction.
- **Proposal disposition:** **Modify proposal 4.** Implement only after the risk rubric and telemetry fields exist; do not encode weaker verification outcomes in the ledger.

### TEF-006 — Batch requirement verification is safe only with exact readiness and per-requirement judgments

- **Severity:** medium
- **Confidence:** high
- **Category:** workflow batching
- **Evidence:** Requirement verification currently performs preflight, plan checks, blocker propagation, integration reachability, snapshot construction, and one verification record per call (`src/taskledger/service.py:895-920`). Recovery classifies every non-complete requirement as `awaiting_verification`, including blocked/not-ready requirements (`src/taskledger/service.py:973-977`). `progress.awaiting_final_verification` uses `all(...)` over covering tasks (`src/taskledger/service.py:940-942`), so a directly verifiable requirement with no tasks is counted by vacuous truth even though that is not the same as readiness. Product requirements require orchestrator evidence/rationale and currency per requirement (`taskledger-product-spec.md:820-845`).
- **Impact:** Batching can reduce repeated preflight, plan, spec, and Git queries and reduce tool-call prompt overhead. A blanket shared evidence payload or an imprecise “ready” list would turn distinct semantic judgments into bulk approval and could misreport direct requirements.
- **Recommendation:** First expose a single authoritative readiness function with reason codes, used by list/resume/progress/verify. Return separate `ready_task_backed` and `ready_direct` sets, since direct requirements still require direct evidence. Add batch input as an array of `{requirement_id, evidence, notes}` and preserve one immutable verification and audit event per requirement. Recheck all items under one preflight and transaction; choose all-or-none semantics for v1 so concurrent state changes cannot yield an ambiguous partial batch. Cap batch size and return per-item results.
- **Proposal disposition:** **Accept proposal 5 with modification.** “Ready” must be exact and batch semantics explicit.

### TEF-007 — Two planning approvals are a reasonable default, but the seven current gates encode decisions that cannot be silently discarded

- **Severity:** medium
- **Confidence:** medium-high
- **Category:** orchestration and user approval
- **Evidence:** Seven separate confirmations are imposed by the skill, not by the ledger product/technical command contracts (`skills/taskledger/SKILL.md:22-36`). The product assigns architecture, dependency, parallelism, and verification decisions to the orchestrator (`taskledger-product-spec.md:81-102`) and requires exact branch confirmation at initialization (`taskledger-product-spec.md:265-297`). The seven-gate text already limits each gate to under 150 words and says not to repeat accepted material (`skills/taskledger/SKILL.md:22-24`). No supplied telemetry separates gate tokens from implementation/review tokens.
- **Impact:** Seven user round trips keep earlier context alive and can be costly, particularly for routine work. Collapsing them without a content contract can hide task boundaries, dependency/parallelism decisions, assumptions, repository cleanliness, or the exact initialization mutation.
- **Recommendation:** Use two explicit approvals for normal work: (1) **plan approval**, covering outcome/non-goals, requirements, task chunks/granularity, acceptance criteria, dependencies/parallelism, and material assumptions; (2) **execution approval**, covering exact branch/HEAD/cleanliness/ignore state, initialization or commit mutation, validated plan fingerprint/diagnostics, and first assignment wave. Preserve additional decision-specific gates when material ambiguity remains or the work is critical. A resume of an unchanged validated plan should not repeat planning approval. Exact branch confirmation and permission to launch workers remain explicit even when grouped.
- **Proposal disposition:** **Accept proposal 6 with modification.** Collapse presentation/round trips, not the decision inventory.

### TEF-008 — Credit-aware escalation cannot live solely inside Taskledger and should not be automated from incomplete prices

- **Severity:** medium
- **Confidence:** high
- **Category:** observability and cost control
- **Evidence:** Taskledger does not invoke models; it is a deterministic local ledger (`README.md:1-9`; `taskledger-product-spec.md:133-152`). Supplied Terra and Sol credit estimates exist, but auto-review rates are unknown. Cached input dominates supplied Terra and Sol input, but the economic value and billing treatment of cached tokens cannot be inferred safely from the repository. CLI outputs may contain credentials today (`src/taskledger/service.py:649,671`), making indiscriminate payload logging unsafe.
- **Impact:** A Taskledger-only token counter will miss the dominant model-side input, cache, compaction, and review activity. A guessed credit conversion can choose a cheaper-looking path that increases rework or weakens assurance.
- **Recommendation:** Let Codex/orchestration emit sanitized usage observations linked to project/task/assignment/submission and assurance tier; let Taskledger optionally store aggregates, not prompts. Measure tokens and credits separately and retain provider/model/rate-version provenance. Instrument CLI request/response bytes locally as a causal proxy. Start with advisory escalation; make it automatic only after calibrated outcome data shows a stable threshold. Never downgrade a critical review due to budget.
- **Proposal disposition:** **Modify proposal 7.** Accept instrumentation now; defer automated escalation thresholds.

## Proposal decision matrix

| # | Proposal | Decision | Expected benefit | Principal condition |
|---|---|---|---|---|
| 1 | Compact/delta `project resume`; full `recover` for uncertainty | **Modify** | High for repeated orchestration turns | Resume returns compact current truth plus cursor/change hints; it is not sparse event replay, and full recovery remains available for cold starts and lost/unsafe cursors |
| 2 | Stop duplicating assignment context and raw token | **Accept with modification** | Medium per handoff; high security/correctness value | Serve stored immutable context once; separate mutable overlay hashes; path only, no raw token |
| 3 | Bounded `submission review-context` | **Accept with modification** | High and directly quality-preserving | Exact OIDs and all safety-critical sets are complete or visibly paginated; reviewer still inspects/tests independently |
| 4 | Lean/standard/critical assurance | **Modify** | Potentially high, currently unquantified | Tiers alter breadth/redundancy, never acceptance invariants; deterministic upward triggers and no self-selected lean for unknown risk |
| 5 | Batch requirement verification and expose ready requirements | **Accept with modification** | Medium | One readiness predicate, distinct direct/task-backed readiness, per-requirement evidence, atomic bounded batch |
| 6 | Seven planning gates to two by default | **Accept with modification** | Medium; likely reduced round trips | Preserve all material decisions, explicit branch/init and worker launch approval, and expand for critical/ambiguous work |
| 7 | Usage instrumentation and escalation tipping point | **Modify / partially defer** | Foundational | Instrument now across orchestration and CLI; defer automated thresholds until costs and outcome quality are calibrated |

## Tipping-point framework

Token efficiency should be evaluated on **lifecycle cost**, not the size of one tool response:

`effective context cost = response tokens + downstream reprocessing + compaction/reconstruction + expected rework`

For a proposed extra assurance step, escalate when:

`P(material defect found by extra step) × loss if missed > incremental token/credit cost + delay`

For a context-fetch optimization, implement when:

`calls × mean response tokens × mean downstream retention multiplier` exceeds its implementation/maintenance cost, subject to zero loss of authoritative fields.

Practical policy:

- **Always critical:** auth/credential scope, authorization, schema/data migration, journal/recovery logic, Git integration/mutation, concurrency, destructive paths, secret handling, or a user-designated high-impact system. Cost does not justify downshifting these.
- **Escalate standard to critical:** unexpected changed files, OID/reachability mismatch, unresolved risk/question/blocker, test failure/flakiness, incomplete/truncated review context, repeated rejection, recovery gate, or inability to reproduce worker evidence.
- **Lean eligibility:** small mechanically bounded change, no critical category, exact diff fully inspected, relevant deterministic tests pass, every criterion has independent evidence, no blocker/risk, and no unexpected scope. Unknowns force standard.
- **Stop adding review passes:** all criteria map to independent evidence, exact OIDs are verified, relevant tests and inspection agree, no unresolved safety signal remains, and another pass has low estimated probability of changing the decision. This is a stop rule for redundant assurance, not permission to omit the baseline review.
- **Operational runaway trigger:** treat multiple compactions, sharply rising uncached input, or repeated full-state calls with unchanged cursors as a signal to compact/restart from durable `resume`, not automatically to purchase more reviewers. The supplied four-compaction/88.8M-token task supports an alert, but not a universal numeric cutoff.

The first measurement milestone should report p50/p95 response bytes and retained model-input tokens by command, plus acceptance/rejection/rework rates by assurance tier. Only then should the project set a credit threshold. Auto-review credits must remain “unknown” until the rate is observed rather than imputed.

## Risks and regressions

- A delta cursor can become a false authority if sparse audit payloads are treated as state reconstruction.
- Cursor/project mix-ups can disclose unrelated metadata; cursors must be authenticated and project-scoped.
- Conditional context retrieval can conceal changed questions or blockers if immutable and mutable data share one hash.
- Removing raw tokens is a compatibility break for tests and clients currently reading `worker_token`; ship a versioned response/schema change and update callers together.
- A bounded review context can create omission bias. Critical sets need complete enumeration, paging, and an `incomplete` acceptance gate.
- Batch verification can become bulk rubber-stamping if evidence is shared or partial success is ambiguous.
- Assurance labels can become cost-driven quality downgrades without mandatory risk triggers and recorded rationale.
- Two approvals can become overlong prompts; use a fixed decision schema and reference the durable plan fingerprint rather than repeating the specification.
- Usage logs can leak secrets, source excerpts, evidence, or user content. Store counts/bytes/IDs, not payloads, and apply retention limits.
- Instrumentation itself adds events and response data; telemetry must be out-of-band or omitted from normal command responses unless explicitly requested.
- Current tests are happy-path-heavy, so changes to recovery, batching, and conditional reads need failure injection before relying on their safety properties.

## Tests needed

1. **Resume/cursor:** empty delta, changed state, external canonical rewrite, new spec observation during preflight, foreign-project cursor, malformed/future cursor, schema/projection version mismatch, lost cursor fallback, uncertain operation forcing full recovery, deterministic ordering, and no token fields.
2. **Assignment handoff:** create returns no raw token/context; token file permissions and authentication work; stored snapshot hash is stable; explicit `CONTINUE` changes it; current requirement changes do not silently alter an active stored snapshot; static-context and dynamic-overlay conditional retrieval behave independently.
3. **Review context:** exact base/submitted/canonical OIDs, rename/copy/binary/deleted files, diffstat correctness, criterion and requirement revisions, open blocker propagation, rejected correction history, pagination/truncation markers, stale canonical HEAD, huge evidence, and proof that worker command strings are never executed.
4. **Assurance policy:** deterministic tier classification, mandatory critical categories, upward triggers, unknown-risk default, and identical ledger acceptance validation across tiers.
5. **Requirement readiness/batch:** task-backed and direct readiness, blocked/review/recovery/invalid-plan cases, integration invalidation between list and batch, duplicate IDs, mixed ready/not-ready batch, rollback on one invalid item, one verification/audit event per requirement, capped batch size, and idempotent retry semantics.
6. **Two-gate workflow:** exact branch confirmation, dirty/unborn repository, material assumption change, parallel-wave approval, unchanged-plan resume, critical expansion, and no mutation before the applicable approval.
7. **Telemetry:** accurate byte/token aggregation, cached versus uncached separation, unknown rate handling, model/rate versioning, redaction tests for raw tokens and content, retention limits, and instrumentation-disabled behavior.
8. **Regression:** existing recovery, authorization, blocker propagation, exact-commit integration, external rewrite invalidation, and project-completion scenarios. Run failure injection around every new transaction and cursor watermark boundary.

## Conclusion

The proposals are directionally sound, but the defensible optimization is to reduce repeated transport and redundant dialogue while retaining one uncompromised authority and verification contract. Implement instrumentation, immutable assignment-context retrieval, compact resume, bounded exact-OID review context, and exact requirement readiness/batching as one staged core. Adopt two planning approvals as the normal skill presentation. Treat assurance tiers and automated credit escalation as a measured orchestration policy, not a shortcut around Taskledger’s independent review, blocker, integration, or recovery invariants.
