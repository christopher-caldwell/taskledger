# Independent token-efficiency audit — reviewer 01

## Scope and method

This is an independent `general_audit` of the current dirty working tree at `/Users/christophercaldwell/Code/oss/taskledger`. I inspected the implementation, CLI dispatch, schema, Taskledger skill, command reference, product specification, technical specification, and acceptance tests. I did not inspect the audit output directory or any other reviewer's work. I treated the supplied telemetry as empirical input, but distinguished what it proves from causal claims it cannot prove. I also ran `python3 -m unittest -v`: all 21 current acceptance tests passed; one test emitted an unclosed-SQLite `ResourceWarning`, which is outside this audit's proposal scope.

The assessment criteria were: likely recurring token/credit savings; preservation of orchestrator/worker authority, exact-OID review, recovery, traceability, and explicit-decision invariants; compatibility with the governing specifications; and whether review quality can remain unchanged.

## Executive assessment

The problem is real and large, but attribution is weak. The supplied tracker accounts for 1.410B of 1.575B cumulative tokens (89.5%), and one 19-minute task accumulated 88.8M tokens across 737 token events and four compactions. Those figures justify immediate measurement and payload/turn reduction. They do **not** establish that any individual Taskledger command caused most of the burn: command counts are not joined to response bytes, subsequent-context amplification, model, cache billing, compactions, or quality outcomes.

The safest high-value direction is to reduce repeated orchestration context while leaving decision quality and durable gates intact. The smallest coherent first release is: (1) usage/payload instrumentation with provenance, (2) a compact authoritative `project resume` that always surfaces safety gates and uses an audit cursor only as a change hint, and (3) a bounded orchestrator-only `submission review-context`. Assignment output cleanup belongs in that release because it removes a credential from model-visible JSON and a duplicate context payload, but the supplied context-size data shows that it is not by itself a material explanation for billion-token usage.

Batch verification, assurance profiles, and collapsing seven gates should not ship as blunt shortcuts. They are acceptable only when modified so that every requirement/criterion decision, exact-OID inspection, blocker check, and recovery/security rule remains unchanged. The seven conversational gates can become two approvals without losing their content; “lean” cannot mean weaker verification.

## Findings

### TEF-001 — A delta resume must be authoritative, not event-sourced

- **Severity:** High
- **Confidence:** High
- **Evidence:** `skills/taskledger/SKILL.md:14-16,65` directs routine reopening through `project recover`. `src/taskledger/service.py:973-977` builds and returns the entire project graph. `src/taskledger/cli.py:171-175` has `show` and `recover`, but no `resume`. `src/taskledger/db.py:56` provides a monotonic audit sequence, while `taskledger-technical-spec.md:675` explicitly says audit history is diagnostic and not event-sourced authority. The rollout count reports 118 `project recover` calls.
- **Impact:** Reinjecting a complete graph into long conversations can be multiplied through every later model input and compaction. A compact projection is therefore likely to save materially more than its raw response size suggests. However, deriving state from audit payloads would create stale or incomplete resumes and weaken recovery.
- **Recommendation:** Add orchestrator-only `project resume`, returning a compact current-state projection plus `latest_event_sequence`. It must run the same preflight/reconciliation as other safe reads and must always include phase, plan validity/fingerprint, current canonical OID, pending spec reviews, open blockers, unresolved operations, active assignments, submissions awaiting review, ready tasks/requirements, completion blockers, and next safe actions. `--since-event` may add a bounded list of changed entity IDs/types, but the current relational tables must be queried for their current state. Validate that the cursor belongs to the project; on an unavailable/invalid cursor, return a clear `refresh_required` result rather than silently claiming a complete delta. Keep `project recover` as the full self-contained snapshot and the required view when an operation is `STARTED`/`UNCERTAIN` or compact state is insufficient. Update both specifications and the skill; the current recovery snapshot contract expressly designates `recover` as first-session input (`taskledger-technical-spec.md:1845-1847`).
- **Proposal disposition:** **Modify.** Accept compact resume, but reject a pure audit-event delta and reject making full recovery inaccessible. The tipping point is crossed when measured resume-plus-downstream context is at least 20% smaller than recover on routine sessions with no increase in follow-up state-fetch calls or missed gates; the supplied recover frequency makes that plausible but not yet measured.

### TEF-002 — Assignment output cleanup improves security, but its direct token benefit is small

- **Severity:** High (credential exposure); Low (direct token savings)
- **Confidence:** High
- **Evidence:** `src/taskledger/service.py:644-649` stores a scoped credential in a restrictive path but also returns the raw token and a full context. `skills/taskledger/SKILL.md:43-45,55` tells the orchestrator to pass the returned context and token path and never expose the raw token. `worker context` then regenerates the same context (`src/taskledger/service.py:678-681`). The technical command contract currently requires `worker_token` and `context` in assignment output (`taskledger-technical-spec.md:1046-1067`), so the proposal is a deliberate contract change. Supplied assignment contexts total 140,366 characters across 39 assignments (3,599 average; 6,745 maximum), roughly tens of thousands—not millions—of tokens for one duplicate pass.
- **Impact:** Removing `worker_token` from model-visible JSON closes an avoidable secret-handling hazard. Removing the duplicated context saves tokens, but the supplied size is under 0.003% of the tracker's 1.410B cumulative tokens before any downstream amplification; it cannot credibly be the primary efficiency fix.
- **Recommendation:** Change `assignment create` to return IDs, worktree/branch/base OID, credential path, and `context_hash`, but no raw token or full context. Preserve file-based credential delivery. `worker context` should return the stored assignment snapshot (not silently rebuild a potentially different one), its hash, and separately identified dynamic status. Support `if_none_match`; a match should still return bounded safety-relevant dynamic changes or a distinct status cursor, because blockers/questions/submissions can change without assignment context changing. On explicit `CONTINUE`, regenerate and atomically store a new snapshot/hash as current code already refreshes context on plan revisions. Version and document the breaking output contract; keep rotation path-only as well for consistency.
- **Proposal disposition:** **Accept with contract modifications.** Ship primarily for secret hygiene and clean handoff semantics. For token savings alone, implement only if bundled with the resume/review projection work; it does not independently cross the materiality threshold shown by the evidence.

### TEF-003 — A bounded review projection is the clearest quality-preserving optimization

- **Severity:** High
- **Confidence:** High
- **Evidence:** Submission records already persist exact `head_commit_oid`, changed files, summary, evidence, risks, questions, and follow-up work (`src/taskledger/db.py:48`; `src/taskledger/service.py:737-762`). Verification requires every current criterion and the behavior/evidence/blocker assertions (`src/taskledger/service.py:765-818`; product requirements PS-090 through PS-095 at `taskledger-product-spec.md:635-677`). There is no `submission review-context` dispatch in `src/taskledger/cli.py:156-204`. The rollout reports 55 verification calls but no bounded review fetch.
- **Impact:** Reviewers currently must assemble state from broad recovery output, worker claims, task context, and Git. A purpose-built projection should reduce lookup turns and irrelevant context while improving exact-state discipline. It would degrade quality if it replaced direct diff inspection/tests or omitted stale-definition and blocker information.
- **Recommendation:** Add an orchestrator-only, read-only command keyed by `submission_id`. Return assignment base OID, submitted head OID, canonical OID/ref, task revision and criterion **IDs**, current-vs-submitted revision/fingerprint status, diffstat and bounded changed-file list, immutable worker summary/evidence/risks/questions, linked requirement statements/revisions, relevant open blockers, prior verification outcomes for this submission, and deterministic Git inspection commands/paths as data—not executed worker commands. Enforce byte/item caps with explicit truncation metadata and continuation or targeted filters. Never include full diff bodies by default. Verification must still require independent inspection/testing of the exact OID and explicit per-criterion results.
- **Proposal disposition:** **Accept.** The tipping point is crossed if it removes even one broad recovery/context-gathering turn per submission without lowering independently executed checks; 55 verification calls make this likely. Measure review-context response size and additional-fetch rate.

### TEF-004 — Instrumentation is a prerequisite; current telemetry cannot rank causes

- **Severity:** High
- **Confidence:** High
- **Evidence:** Supplied model totals are cumulative context-token events, while Taskledger's schema records domain audit events only (`src/taskledger/db.py:56`) and its `audit` method stores no model, token, credit, response-byte, duration, or compaction fields (`src/taskledger/service.py:23-25`). The high cache ratios and repeated ~125k-token inputs in the example suggest context amplification, but command frequency alone does not identify which output stayed in context or its marginal billed cost. Auto-review pricing is explicitly unknown.
- **Impact:** Optimizing by call count risks spending implementation effort on low-volume payloads (such as 140k characters of assignment context) while leaving the dominant repeated-history or review loop untouched. A numeric “credit” dashboard without source/rate provenance would be misleading.
- **Recommendation:** Instrument two layers: Taskledger can directly record command, result/error, elapsed time, serialized input/output bytes, entity counts, and truncation/cache-hit flags; the orchestration host must supply provider/model token usage, cached-input tokens, output tokens, compactions, credits/rate version, thread/task ID, and command correlation ID. Store no prompts, raw command inputs, evidence text, repository content, or credentials in telemetry. Treat externally reported token/credit data as non-authoritative observability with provenance, not ledger authority. Establish a baseline and A/B compare routine sessions before making assurance reductions.
- **Proposal disposition:** **Accept and implement first.** The tipping point for an optimization is measured recurring savings sufficient to repay implementation/maintenance within 30 days or two comparable large projects, while quality/recovery metrics are non-inferior. Until rates are known, optimize tokens and turns separately from credits.

### TEF-005 — Assurance levels are valid only as declared profiles above an invariant floor

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** Taskledger assigns semantic planning and verification judgments to the orchestrator (`taskledger-product-spec.md:85,416-439,637-650`) and identifies the implementation as experimental for high-risk work (`skills/taskledger/SKILL.md:79-81`). Nothing in the current schema/spec defines assurance levels. Taskledger is prohibited from inventing product meaning (`taskledger-product-spec.md:152`).
- **Impact:** Adaptive assurance could control conversational ceremony, redundant review passes, model escalation, and context budgets. If Taskledger infers risk or lets “lean” skip exact-OID inspection, criterion decisions, relevant testing, requirement verification, blockers, or recovery gates, it directly weakens governing invariants and creates false confidence.
- **Recommendation:** Define `lean`, `standard`, and `critical` as orchestrator/user-declared policy profiles, with reason and provenance, not autonomous Taskledger judgments. All profiles retain authorization, isolation, plan validation, spec/recovery gates, independent exact-OID submission review, every criterion, evidence, integration, and requirement verification. Vary only presentation, number of optional review passes, context caps, and escalation/model policy. Default to `standard`; automatically move upward only on deterministic facts such as unresolved operations, auth/storage changes, destructive migration, or user declaration, and never automatically move downward. Critical work retains full gates and extra independent checks.
- **Proposal disposition:** **Modify/defer until instrumentation.** The benefit-versus-burn rule is: escalate when `probability that extra assurance prevents failure × impact of that failure` exceeds marginal credit/time cost. In the absence of defensible probabilities, deterministic critical triggers and user choice dominate; lean is justified only after measured non-inferior defect/rejection/recovery outcomes.

### TEF-006 — “Ready requirements” is safe; batch verification needs per-item atomic semantics

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** `requirement_rows` reports only `COMPLETE`, `BLOCKED`, or `REMAINING` (`src/taskledger/service.py:931-938`), while `progress` computes an aggregate `awaiting_final_verification` count (`src/taskledger/service.py:940-942`). `requirement verify` independently checks blockers, task coverage, completed state, exact integration records, plan/spec snapshots, and evidence (`src/taskledger/service.py:900-920`). The command reference supports only one requirement per call (`skills/taskledger/references/commands.md:24-26`). Supplied usage is 20 verification calls, lower than recover, context, validation, submission, and assignment operations.
- **Impact:** Exposing exact ready IDs prevents exploratory list/recover turns with no authority loss. A batch endpoint can reduce JSON/tool overhead, but a single shared evidence blob or all-or-nothing failure would weaken traceability or make retries ambiguous. Since only 20 calls are observed, its expected savings are secondary.
- **Recommendation:** First add a `ready` list filter and readiness reasons derived from the same checks used by verification. If batching is added, bound the batch size, require a complete evidence/notes object per requirement, evaluate each against one captured plan/spec/canonical snapshot, and return deterministic per-item results. Define whether the transaction is all-or-nothing; I prefer all-or-nothing after full prevalidation so a caller cannot mistake a partially verified set for success. Preserve one immutable verification record and audit event per requirement. Do not auto-generate evidence or infer that integration proves observable behavior.
- **Proposal disposition:** **Modify.** Accept ready-requirement exposure now; defer batch mutation until measured call overhead crosses the implementation cost or projects routinely have several simultaneously ready requirements. Twenty calls do not establish material global savings.

### TEF-007 — Two approvals can preserve seven planning concerns; deleting five concerns cannot

- **Severity:** Medium
- **Confidence:** Medium-High
- **Evidence:** Seven separate conversational confirmations are skill policy (`skills/taskledger/SKILL.md:22-36`), not a product/technical ledger invariant. The governing specification requires explicit canonical-branch confirmation and orchestrator-owned planning/verification, but not seven chat turns (`taskledger-product-spec.md:276-279,416-439`). Each extra turn in a long thread resends accumulated context, so the 88.8M-token/737-event example makes turn count a credible amplification factor, although no gate-specific telemetry is supplied.
- **Impact:** Combining approvals is likely materially useful, especially in high-context planning. Simply dropping work-chunk, granularity, parallelism, assumption, or working-base review can cause oversized tasks, conflicting workers, hidden scope, or unsafe repository mutation—costs that erase token savings through rework.
- **Recommendation:** For `standard`, combine the seven concerns into two concise approvals: **Plan approval** covers understanding, non-goals/questions, chunks, ledger granularity, dependency/parallel waves, and material assumptions; **Execution approval** covers branch/HEAD/cleanliness/ignore state, exact init/materialization actions, validated plan changes, and the first assignment wave. Retain exact branch confirmation as a CLI gate and never treat plan approval as approval to launch workers. Use the current seven separate turns for `critical` or when the user asks; lean may use the same two approvals with tighter presentation, not fewer substantive decisions. Re-open either approval when material facts change.
- **Proposal disposition:** **Modify.** Accept two approval points by default, reject removal of the seven concern areas. The tipping point is favorable when five avoided turns save more context than the combined approval adds and replan/rejection rates do not rise; instrument gate turns and subsequent plan revisions to verify this.

## Proposal decision matrix

| # | Proposal | Decision | Expected materiality | Invariant condition |
|---|---|---|---|---|
| 1 | Compact/delta `project resume` | Modify | High | Delta cursor is advisory; current tables and safety gates remain authoritative; full recover remains available/required for uncertainty |
| 2 | Assignment returns IDs/path/hash; worker fetches once | Accept with modifications | Low direct token benefit; high secret-hygiene benefit | Stored snapshot/hash, separate dynamic status, path-only credential delivery, versioned contract |
| 3 | Bounded `submission review-context` | Accept | High | Orchestrator-only; exact OIDs and criterion IDs; truncation explicit; never replaces diff/tests |
| 4 | Adaptive assurance | Modify/defer | Potentially high but unproven | User/orchestrator-declared profiles above a fixed verification/recovery/security floor |
| 5 | Batch verification and ready requirements | Modify | Ready query medium; batch low on current counts | Per-requirement evidence, checks, records, and deterministic transaction semantics |
| 6 | Seven gates to two by default | Modify | Likely high in long contexts | Combine approvals, do not delete concerns; retain separate execution authorization and critical full mode |
| 7 | Usage instrumentation and escalation tipping point | Accept first | Foundational | Provenance, no content/secrets, tokens and credits reported separately, observability not authority |

## Tipping-point framework

Use a measured decision rule rather than command counts:

`net benefit = recurring saved credits + valued saved latency - implementation/maintenance cost - expected quality-loss cost`

For an endpoint or payload optimization, measure per correlated workflow: serialized bytes emitted; tokens added to the next and later model inputs; cached and uncached tokens; tool/model turns; compactions; elapsed time; retries/rejections; missed blockers; post-acceptance defects; and recovery incidents. Ship broadly when payback is within 30 days or two comparable large projects and quality metrics are non-inferior. Roll back or raise assurance when saved credits are smaller than review/rework/recovery cost.

For assurance escalation, use:

`escalate when P(extra assurance prevents a material failure) × failure impact > marginal assurance credits + delay cost`.

Because the probability term is currently unknown, use deterministic floors: any uncertain Git operation, credential/auth change, storage/schema migration, destructive or hard-to-reverse action, high-value financial behavior, or user-designated critical work stays `critical`. Standard remains the default. Lean is appropriate only for bounded, reversible, low-blast-radius work after telemetry demonstrates comparable review outcomes.

Per proposal, prioritize: instrumentation immediately; review-context and resume once measured payload shapes are defined; two combined approvals once the concern checklist is preserved; assignment cleanup in the same contract release; ready-requirement query opportunistically; assurance-based relaxation and batch mutation only after outcome data exists.

## Risks/regressions

- Audit-sequence deltas can miss authoritative changes, cross project boundaries, or imply completeness when events were pruned; always recompute current safety state.
- Compact responses can hide blockers, pending spec review, stale plan, accepted-but-unintegrated work, or unresolved operations; these fields must be non-optional.
- Removing raw tokens/full context breaks the documented v0.2 command contract and current tests/callers; version and migrate deliberately.
- A hash over regenerated context can disagree with the durable assignment snapshot; hash canonical stored bytes and define invalidation/update rules.
- Review-context truncation can hide a dangerous file or risk; return totals, truncation flags, and continuation/filter options, and never authorize acceptance from a truncated projection alone.
- Batch verification can create ambiguous partial success or shared-evidence shortcuts; use full prevalidation and one record/evidence decision per requirement.
- Adaptive assurance can become a permission bypass or misleading quality label; profiles cannot relax ledger-enforced gates and must record who selected them.
- Combining approvals can bury material assumptions in a large prompt; cap and structure each concern, and split only when decision density exceeds the concise format.
- Telemetry can leak code, evidence, paths, tokens, or provider billing assumptions; collect counts/IDs/provenance, not contents, and distinguish estimated from invoiced credits.

## Tests needed

1. `project resume` parity tests proving every execution-blocking condition visible in `recover` is also visible in compact output; preflight reconciliation; same-project cursor validation; stale/unknown cursor behavior; empty delta; event-gap/truncation behavior; deterministic bounds; unborn and completed projects.
2. Recovery tests proving `resume` never offers ordinary execution while any operation is `STARTED`/`UNCERTAIN`, and that full `recover` still supplies proof fields and allowed recovery actions.
3. Assignment contract tests proving no raw token/context appears, credential file permissions and worker authentication still work, stored context hash is stable, `if_none_match` behavior is correct, dynamic blockers/questions are not hidden, and `CONTINUE` changes the hash atomically.
4. Review-context tests for orchestrator authorization, cross-project denial, exact base/head/canonical OIDs, rename/copy/binary diffstat, criterion IDs and revision staleness, relevant blockers, immutable worker claims, bounded/truncated lists, missing Git object, and no execution of worker evidence commands.
5. Ready/batch requirement tests for direct and task-backed requirements, shared tasks, blockers, invalid plans/spec reviews, external canonical rewrites, simultaneous snapshot capture, duplicate IDs, stale state, all-or-nothing rollback, idempotent retry, and one audit/verification record per item.
6. Assurance/gate tests that all profiles retain authorization, plan/spec/recovery gates, independent exact-OID review, every criterion, integration, and requirement verification; changed facts must reopen the appropriate approval.
7. Telemetry tests for token/path/content redaction, correlation IDs, unknown rates, cached-token accounting, failed calls, truncation, compactions, and no effect on authoritative state or command success.
8. Workflow benchmarks comparing recover versus resume, ad hoc versus review-context, seven versus two approval turns, and single versus batch verification using total downstream input tokens—not just CLI response bytes—with defect/rejection/recovery outcomes held non-inferior.

## Conclusion

Proceed, but in a different order and with narrower semantics than the proposals imply. Instrument first; then ship a compact authoritative resume and bounded review-context, with assignment path/hash cleanup in the same versioned contract change. Combine seven planning concerns into two approvals for standard work, while retaining separate execution consent and the full critical path. Add ready-requirement exposure, but defer batch mutation and assurance-based relaxation until correlated telemetry proves they save more than they risk. The ledger's role-scoped authority, exact-OID verification, per-criterion and per-requirement judgments, specification/recovery gates, and immutable evidence are the floor, not variables in the token budget.
