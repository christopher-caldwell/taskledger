# Independent audit: token-efficiency proposals

## Scope and method

This audit evaluates the current dirty working tree at `/Users/christophercaldwell/Code/oss/taskledger` as-is. I reviewed the current CLI, service and database implementation; the Taskledger skill and command reference; the product and technical specifications; the acceptance tests; and the supplied 45-day usage metrics. I did not inspect the audit output directory or any other reviewer material.

The review criteria were technical validity, likely material token/credit savings, consistency with authority/security/recovery invariants, preservation of independent review quality, unsupported assumptions, and implementation proportionality. I treated the supplied empirical figures as observations, not as proof of causation. I also ran:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_acceptance
```

All 21 tests passed in 10.739 seconds. The run emitted one unclosed-SQLite `ResourceWarning`; it does not invalidate the results, but should be cleaned up in test code.

## Executive assessment

The proposals point in the right direction, but they are not equally supported by the evidence. The strongest immediate changes are: replace the skill's seven default conversational gates with two approval points; add a compact, current-state `project resume` that never displaces the complete recovery contract; add an orchestrator-only bounded submission review manifest; stop returning plaintext worker credentials and duplicate context from `assignment create`; and expose accurately computed ready-to-verify requirements. These changes reduce repeated context injection while preserving the ledger's authority boundaries.

Two qualifications are decisive:

1. An audit-event cursor is not currently a safe state cursor. Taskledger explicitly rejected event sourcing, and some reconciliation mutations do not create audit events. A `--since-event` implementation that emits only events after a cursor could omit authoritative changes caused by repository reconciliation.
2. The supplied totals demonstrate extreme context churn, but not which proposed change will save how many billed credits. Terra input was about 97.2% cached and Sol input about 97.3% cached, while the auto-review rate is unknown. Command counts and assignment-context character counts therefore cannot be translated directly into credit savings.

The smallest coherent implementation set is:

- simplify the skill to two default approval gates;
- add minimal privacy-safe host instrumentation and command response-byte measurements;
- add a compact `project resume` with a server-generated state cursor, current gates, current action queues, and an explicit `full_recovery_required` signal;
- add bounded `submission review-context` keyed to exact immutable OIDs;
- change assignment creation to return the credential path and context hash, not the plaintext token or full context, and make `worker context` conditionally cacheable;
- add `requirement list {"filter":"ready"}` using the same readiness checks as verification.

Defer adaptive assurance and batch verification until the first measurements show where cost remains. Batch verification is a convenience, not a substitute for per-requirement evidence. No token policy may weaken worker/orchestrator separation, exact-OID review, criterion-by-criterion verification, explicit blocker resolution, deterministic recovery, or requirement-based completion.

## Findings

### TL-TE-001 — An audit-event delta is not an authoritative resume protocol

- **Severity:** High
- **Confidence:** High (9/10)
- **Evidence:** `audit_events.sequence` is a global autoincrementing sequence (`src/taskledger/db.py:56`), but the design explicitly rejected event sourcing (`taskledger-technical-spec.md:52`). `reconcile_integrations` can invalidate an integration, move a task from `COMPLETED` to `ACCEPTED`, invalidate requirement/project completion, and create a blocker without recording an audit event (`src/taskledger/service.py:869-875`). `system_blocker` itself inserts no audit event (`src/taskledger/service.py:538-541`). Routine state loading also calls mutating reconciliation and specification observation through `preflight` (`src/taskledger/service.py:132-135`).
- **Impact:** A caller using `--since-event N` as proof that nothing changed could miss repository-derived invalidation or a system blocker. That could yield stale eligibility, verification, or completion decisions and violate the ledger-authority and safe-recovery invariants.
- **Recommendation:** Implement `project resume` as a compact current read model generated *after* preflight, not as event replay. Return a server-generated state cursor that covers ledger state plus repository-derived inputs (at minimum project identity, latest relevant audit sequence, canonical HEAD, active specification observations, plan fingerprint, unresolved-operation state, and schema/API version). A supplied cursor may suppress unchanged sections only when it matches the same project and a server-verifiable prior snapshot. Unknown, future, malformed, or incompatible cursors must return a full compact snapshot, not an empty delta. If any operation is `STARTED`/`UNCERTAIN`, set `full_recovery_required: true` and direct the caller to `project recover`.
- **Proposal disposition:** **Modify proposal 1.** Add compact resume, but do not define correctness in terms of audit events alone and do not reserve full recovery solely for uncertain operations. A fresh session without a trusted baseline still needs the complete recovery snapshot required by PS-120/PS-122.
- **Benefit versus token burn:** This is likely a material target because `project recover` was invoked 118 times, and repeated graph output is subsequently replayed in model context. It tips toward implementation as soon as resume output-byte measurements show even a modest recurring reduction. Correctness requires a current compact snapshot even if that costs more than a bare event list.

### TL-TE-002 — Full recovery is mandatory and the current implementation is already narrower than its contract

- **Severity:** High
- **Confidence:** High (9/10)
- **Evidence:** PS-120 requires recovery to include submissions awaiting action, accepted work awaiting integration, completed integrations and validity, and exact completion reasons (`taskledger-product-spec.md:753-786`). The technical contract also requires enough task/requirement text to act without conversation history and retained assignment workspace paths (`taskledger-technical-spec.md:1845-1904`). The current `recover` response returns raw task and assignment rows, but no submission collection, verification claims, or integration collection, and raw task rows lack task objective/scope/criteria (`src/taskledger/service.py:973-977`).
- **Impact:** Introducing a compact resume without first preserving a reliable full fallback could institutionalize incomplete recovery. A new or compacted orchestrator session may lack the worker claim or exact accepted/integrated state needed to proceed safely.
- **Recommendation:** Keep `project recover` self-contained and bring it into conformance independently of the compact endpoint. The skill should use resume for ordinary same-session polling, complete recovery for a new session or lost baseline, and complete recovery plus explicit operation actions for uncertainty. Add contract tests proving every PS-120 category is present and actionable.
- **Proposal disposition:** **Modify proposal 1.** “Reserve recover for uncertain operations” is too narrow; reserve it for uncertainty *and* baseline reconstruction after history loss.
- **Benefit versus token burn:** Do not optimize away mandatory recovery content. Savings should come from calling it less often, not silently making recovery incomplete.

### TL-TE-003 — Assignment creation duplicates context and exposes a plaintext credential to the orchestrator result

- **Severity:** Medium
- **Confidence:** High (9/10)
- **Evidence:** `assignment_create` returns both `worker_token` and `worker_token_path`, plus the complete context (`src/taskledger/service.py:644-649`). The skill says never put a raw worker token into prompts/chat/logs and prefers the token path (`skills/taskledger/SKILL.md:44,55`). The worker then obtains substantially the same assignment material again through `worker_context` (`src/taskledger/service.py:678-681`). The technical spec currently requires the plaintext token to be returned once (`taskledger-technical-spec.md:697-701,1046-1068`), so this is an intentional API change, not a code-only cleanup.
- **Impact:** Returning the raw secret increases accidental disclosure surface in LLM tool traces without adding value on the local-path workflow. Returning context twice adds avoidable tokens. However, the supplied 140,366 total context characters are tiny relative to 1.410B tracker input tokens; direct one-time duplication alone is not a credible explanation for the aggregate burn.
- **Recommendation:** Update the product/technical command contract and version the response. Return assignment/task IDs, worktree/branch/base OID, `worker_token_path`, and `context_hash`; omit `worker_token` and full `context` by default. Preserve an explicit non-LLM automation escape hatch only if there is a demonstrated remote workflow that cannot read the credential path. Serve the approved assignment snapshot from stored `context_json`, while exposing live questions/blockers/submission state as a separately hashed dynamic section. `if-none-match` must be scoped to assignment ID and context kind and must return a clear `not_modified` result only when the caller supplied the exact current hash.
- **Proposal disposition:** **Modify proposal 2.** Accept the path/hash design and raw-token removal, but distinguish the stored approved assignment snapshot from live coordination state and handle API compatibility explicitly.
- **Benefit versus token burn:** Implement now primarily for secret minimization and protocol clarity. Claim material token savings only if instrumentation shows repeated worker-context bodies are being reinjected; the raw character total alone does not establish material credit savings.

### TL-TE-004 — A bounded exact-OID review manifest is a high-value targeting aid, not a replacement for review

- **Severity:** Medium
- **Confidence:** High (9/10)
- **Evidence:** Submission records already persist the exact head OID, changed-file set, summary, evidence, risks, unresolved questions, and follow-up work (`src/taskledger/db.py:48`; `src/taskledger/service.py:737-762`). Verification must consider every current criterion and the exact submitted state (`taskledger-product-spec.md:625-650`), but the CLI exposes no submission show/review-context command (`src/taskledger/cli.py:156-208`). Verification currently expects the caller to already know criterion IDs (`src/taskledger/service.py:765-780`).
- **Impact:** Reviewers are pushed toward full recovery, ad hoc database inspection, or repeated repository discovery. A focused manifest can remove retrieval overhead and reduce the chance of inspecting a mutable branch tip instead of the immutable submitted object. If treated as the review itself, however, it would degrade independent verification.
- **Recommendation:** Add an orchestrator-only `submission review-context` that returns submission ID/state, assignment ID, task ID/revision, base OID, submitted head OID, canonical HEAD, existence/reachability facts, deterministic file statuses and bounded numstat/diffstat, exact criterion IDs/text, linked requirement statements, worker claims/evidence/risks/questions/follow-ups, and applicable open blockers. Do not include an unbounded patch. Return explicit omitted counts/byte totals and commands or structured selectors for inspecting the exact `base..head` range. Treat all worker-authored fields as untrusted claims. Do not let manifest generation mutate verification state or satisfy any criterion.
- **Proposal disposition:** **Accept proposal 3**, subject to the stated bounds and exact-OID safeguards.
- **Benefit versus token burn:** This tips strongly toward implementation because it replaces broad state retrieval in a safety-critical, recurring path (55 submission verifications and 43 auto-review threads) while improving, rather than reducing, review targeting.

### TL-TE-005 — Adaptive assurance is under-specified and can conflict with mandatory verification invariants

- **Severity:** High
- **Confidence:** High (8/10)
- **Evidence:** The product requires independent verification of every submission and explicit consideration of every acceptance criterion, intended behavior, required evidence, and blockers (`taskledger-product-spec.md:635-675`). The service enforces criterion completeness and acceptance assertions (`src/taskledger/service.py:765-815`). The proposal does not define who assigns assurance, what risk signals are authoritative, how the choice is recorded, or what “lean” may omit.
- **Impact:** A cost-driven lean tier could become a route around exact-OID inspection, criterion checks, relevant tests, security review, or recovery proof. Conversely, storing assurance as new durable workflow state adds schema, migration, policy, and testing cost before its benefit is known.
- **Recommendation:** Defer a durable assurance-level feature. First instrument cost and review outcomes. If piloted later, keep invariant checks identical at every level. Let tiers vary only the breadth of additional independent inspection/testing: lean for narrow reversible work with deterministic tests and no auth/security/data/schema/migration/recovery impact; standard as default; critical for high-consequence or weakly testable changes, with stronger evidence or a second independent review. Make the orchestrator state the rationale, allow automatic escalation but never automatic de-escalation, and record the chosen level with the verification evidence if it affects review procedure.
- **Proposal disposition:** **Defer proposal 4** as currently specified; reconsider only as a constrained workflow pilot after telemetry and a risk taxonomy exist.
- **Benefit versus token burn:** Lean review is justified only when the marginal inspection has low expected information value and all mandatory proofs remain. If uncertainty concerns an invariant or irreversible consequence, expected-loss avoidance dominates token cost and assurance must escalate.

### TL-TE-006 — Ready-requirement exposure is useful; batching offers limited semantic savings and needs per-item atomic semantics

- **Severity:** Medium
- **Confidence:** High (8/10)
- **Evidence:** `requirement list` only supports complete/remaining/blocked/all (`src/taskledger/cli.py:53-57`; `skills/taskledger/references/commands.md:24`). `progress.awaiting_final_verification` uses `all()` over covering tasks without excluding blockers or other verification gates (`src/taskledger/service.py:940-942`), while the real verification path separately checks plan validity, blockers, task completion, and integration (`src/taskledger/service.py:895-920`). Only 20 `requirement verify` tool-call inputs were observed, substantially fewer than recover/context/validation calls.
- **Impact:** Without a trustworthy ready filter, the orchestrator may query broad state or attempt verification only to receive a gate error. A batch command can reduce envelope/call overhead, but it does not remove the need for requirement-specific evidence and can obscure partial failure or cross-item invalidation behavior.
- **Recommendation:** First add a side-effect-free `requirement_ready` predicate shared by list/progress/verify and expose `filter=ready` with readiness reasons for non-ready items when requested. If measurements later justify batching, accept an array of complete existing verification payloads, validate all items against one preflight/plan/repository snapshot, reject duplicates, cap batch size and total JSON, and use one transaction for all-or-nothing semantics. Emit one immutable verification and one audit event per requirement. Do not permit shared generic evidence to stand in for each requirement's observable rationale.
- **Proposal disposition:** **Modify proposal 5.** Accept ready-requirement exposure now; defer the batch mutation until call-overhead measurements show a meaningful residual cost.
- **Benefit versus token burn:** Ready listing likely pays for itself through fewer failed/broad queries. At only 20 observed verify calls, batch implementation does not yet cross the complexity threshold unless projects with much larger ready waves are common.

### TL-TE-007 — Seven separate default planning gates cause avoidable context replay without being a product invariant

- **Severity:** Medium
- **Confidence:** High (9/10)
- **Evidence:** The dirty skill requires seven separate confirmations for new or materially revised plans (`skills/taskledger/SKILL.md:22-36`). The product specification assigns semantic planning to the orchestrator and requires structural plan validation, but does not mandate seven conversational approvals (`taskledger-product-spec.md:414-430`). A single observed Terra task averaged roughly 125,000 input tokens per token-count event and compacted four times; additional confirmation turns can replay a large accumulated context even when each gate's new text is short.
- **Impact:** Seven sequential turns increase latency and repeated context processing and may prompt needless restatement. They also create more opportunities for compaction to discard useful review context. Collapsing them carelessly could blur user approval of the plan versus authorization to mutate the repository and launch workers.
- **Recommendation:** Use two default gates: (1) approve the synthesized outcome, non-goals, material assumptions, requirements/task chunks, dependencies and parallel waves; (2) approve the exact working base and the immediate mutation/assignment/worker-launch wave after the materialized plan validates. Reopen a gate only for material change. Retain an expanded critical workflow when the user requests it or when work is destructive, irreversible, security/auth sensitive, data/schema migrating, recovery-related, or otherwise high consequence.
- **Proposal disposition:** **Accept proposal 6.** The two-gate default preserves the two distinct decisions that matter while removing five mandatory round trips.
- **Benefit versus token burn:** This is the clearest no-schema saving. Even if most repeated input is cached, five avoided high-context turns per plan can be material. The tipping point favors two gates whenever splitting a decision does not change the user's authority or the evidence needed to decide it.

### TL-TE-008 — Token/credit instrumentation belongs at the host boundary and must not become a cost-based safety bypass

- **Severity:** Medium
- **Confidence:** High (8/10)
- **Evidence:** Taskledger sees JSON requests and responses but no model token counters or model-specific credit rates (`src/taskledger/cli.py:18-32,735-741` in the implementation/spec). The supplied auto-review rate is unknown. Terra used 1,090,070,288 input tokens of which 1,059,004,928 were cached; Sol used 203,321,840 input of which 197,913,600 were cached. Thus raw cumulative input is not equivalent to billed credits. Security controls also prohibit credential leakage in logs and audit payloads (`taskledger-technical-spec.md:2017-2031`).
- **Impact:** Ledger-only “token” accounting would be fabricated or incomplete. Storing prompts or full tool results to improve attribution would increase privacy and credential risk. Conversely, no instrumentation leaves adaptive decisions and projected savings unsupported.
- **Recommendation:** Instrument at the Codex/model host or wrapper, with Taskledger emitting only correlatable command metadata: command name, project/task/assignment/submission IDs as locally protected pseudonymous dimensions, request/response byte counts, duration, status, and state cursor movement. The host should add model, input/cached/output tokens, compactions, and actual rate-table version. Store no prompt text, raw tool output, token, credential path content, evidence command output, or repository diff. Aggregate offline and inject only threshold crossings or compact summaries into model context.
- **Proposal disposition:** **Modify proposal 7.** Accept instrumentation and a tipping framework, but implement actual token/credit capture outside the ledger and prohibit automatic lowering of required assurance.
- **Benefit versus token burn:** Instrumentation is justified when its own retained/injected summary is below 1% of measured task token use and it can attribute at least 80% of spend to task/phase/command. If rate information is missing, report tokens and bytes separately rather than inventing credits.

### TL-TE-009 — The supplied metrics establish scale, not proposal-level causality

- **Severity:** Medium
- **Confidence:** High (9/10)
- **Evidence:** Financial tracker accounts for 1.410B of 1.575B observed tokens (89.5%), but the provided command observations are counts, not output bytes or downstream-context attribution. Assignment contexts total only 140,366 characters. The 19-minute Terra task accumulated 88.8M tokens with four compactions and roughly 125k average recent input, indicating repeated whole-context processing rather than any single small payload. Cached input dominates both measured model groups, and the auto-review price is unknown.
- **Impact:** It would be easy to overstate savings from removing a few thousand context characters or batching 20 calls, then add significant protocol complexity without moving the credit total. The largest savings may instead come from fewer turns, fewer repeated broad snapshots, better context isolation, and avoiding review loops with no new information.
- **Recommendation:** Establish per-command response bytes, per-turn tool-output retention, compaction count, phase, assurance procedure, retries, rejections, escaped defects, and actual credits before and after each change. Roll out in independently measurable increments rather than landing all proposals together. Preserve the supplied metrics as baseline but label projections as hypotheses.
- **Proposal disposition:** This finding supports **modify/defer** decisions for proposals 2, 4, 5, and 7; it does not negate the security benefit of proposal 2 or the workflow benefit of proposal 6.
- **Benefit versus token burn:** A change crosses the materiality threshold only when measured savings over a reasonable horizon exceed implementation, migration, added decision, and expected rework cost. Raw call counts alone are insufficient.

## Proposal decision matrix

| # | Proposal | Decision | Technical validity | Expected materiality | Required constraint |
|---:|---|---|---|---|---|
| 1 | Compact/delta `project resume`; recover for uncertainty | **Modify** | Valid as an additional current read model; unsafe as audit-event replay | High potential because recover is frequent and broad | Preserve full recovery for fresh/lost baselines; cursor must cover repository-derived state; uncertainty forces recover |
| 2 | Assignment returns IDs/path/hash; worker fetches once | **Modify** | Valid and improves secret handling | Low direct measured savings; potentially larger downstream replay savings | Version API/spec; hash stored approved context separately from live coordination state; exact scoped ETag semantics |
| 3 | Bounded `submission review-context` | **Accept** | Directly supported by durable submission/task data | Medium-to-high and review-quality positive | Orchestrator-only; exact base/head OIDs; explicit truncation metadata; never a substitute for diff inspection/tests |
| 4 | Lean/standard/critical assurance | **Defer** | Conceptually possible but underspecified | Unknown and potentially large | Instrument first; all tiers retain invariant checks; no automatic de-escalation |
| 5 | Batch requirement verification and ready requirements | **Modify** | Ready filter is straightforward; batch needs careful semantics | Ready filter medium; batch currently low | Shared readiness predicate first; later bounded atomic batch with per-requirement evidence and records |
| 6 | Collapse seven planning gates to two by default | **Accept** | Consistent with product spec and preserves distinct approvals | High potential due avoided full-context turns | One plan decision and one mutation/launch decision; expanded gates for critical work |
| 7 | Token/credit instrumentation and escalation tipping point | **Modify** | Necessary, but actual token data is outside Taskledger | Enabling rather than directly saving | Host-level counters plus ledger response bytes; privacy-safe aggregation; cost never waives safety proof |

## Tipping-point framework

Use a two-axis decision: expected information value and expected consequence of error. Token cost is a resource constraint, not an authority rule.

### Measurements

For each task and phase, record:

- actual input, cached input, output, and billed credits using a versioned rate table;
- tool command, request/response bytes, status, retry count, and state-cursor advance;
- turn count and compactions;
- review passes, tests, rejection/correction cycles, blockers, and escaped defects;
- assurance procedure used, without storing prompt or repository content.

Use medians and p90 by task risk/size; cumulative token counts alone are too skewed for operational thresholds.

### Implementation threshold

- **Implement:** a flow consumes at least 5% of median task credits or occurs at least 100 times per 30 days, the change preserves invariants, and expected 90-day savings are at least 3x implementation/migration cost.
- **Pilot:** estimated share is 1-5%, frequency is 20-99 per 30 days, or attribution confidence is below 80%.
- **Defer:** below 1% and fewer than 20 occurrences per 30 days, unless the change independently reduces security or correctness risk.
- **Reject:** savings require weaker identity scoping, stale-state tolerance, skipped criteria, implicit blocker resolution, non-exact Git review, or incomplete recovery.

These are starting thresholds and should be recalibrated after at least 20 completed tasks per major risk band.

### Per-task escalation/stop rule

Continue an additional planning or verification pass only when it is expected to produce evidence that could change a disposition or close a named proof gap. Switch from broad recovery/context calls to compact targeted reads after the first repeated call with no cursor advance. After two consecutive review cycles produce no new material evidence, either decide on the existing evidence or explicitly escalate because a named high-consequence uncertainty remains; do not continue an unbounded “one more pass” loop.

Before a stable baseline exists, trigger an efficiency intervention when any of these occurs: one compaction before the first submission; two identical broad state loads without state change; rolling input above 2x the median of comparable completed tasks; or projected task credits above 2x the comparable median. The intervention is to compact state, use `resume`/`review-context`, narrow the question, or split the task. It is not permission to lower review requirements.

Escalate to critical assurance regardless of token cost for credential/auth changes, recovery logic, destructive or irreversible operations, data/schema migrations, canonical-branch/integration semantics, security boundaries, weakly testable high-impact behavior, or conflicting evidence about an invariant.

## Risks/regressions

- **Missed delta:** sparse audit events and external Git/specification changes can make event-only deltas stale.
- **Cursor confusion:** a cursor reused across projects, schema versions, or lost baselines could incorrectly suppress state unless strongly scoped and verified.
- **Read-side mutation:** `preflight` reconciles operations/integrations and observes specifications, so cursor capture must occur after reconciliation and tests must cover changes created by the read.
- **Recovery erosion:** a compact endpoint may become an excuse not to fix or test the complete recovery contract.
- **Context staleness:** one ETag cannot safely conflate approved assignment definition with changing questions, blockers, and submissions.
- **Credential compatibility:** removing `worker_token` breaks clients described by the current technical spec; migration/version behavior must be explicit. Retaining an unguarded compatibility field defeats the security improvement.
- **Review-manifest overtrust:** diffstat and worker evidence can focus review but cannot establish correctness. Exact-object existence and independent inspection remain required.
- **Silent truncation:** bounded outputs must report omitted items/bytes and stable retrieval selectors; silent omission can hide risky files or blockers.
- **Batch ambiguity:** partial success, duplicate IDs, shared evidence, or state changes between items could corrupt traceability unless the batch is bounded and atomic.
- **Assurance misclassification:** cost pressure may bias work toward lean; critical categories and automatic escalation must be conservative.
- **Approval collapse:** two gates must still separate semantic plan approval from repository mutation/worker launch authorization.
- **Telemetry leakage:** prompt/tool-body capture could persist secrets, proprietary code, or worker claims. Store numeric metadata only.
- **Metric gaming:** optimizing raw tokens can worsen rework or escaped defects; outcome quality must be measured alongside spend.

## Tests needed

1. `project resume` after every state mutation returns a current compact view and a monotonically usable, project-scoped cursor.
2. External canonical rewrites and specification byte changes are visible even when no prior audit event would have represented the resulting reconciliation.
3. Unknown/future/cross-project/stale cursors fail safe; a caller with no baseline receives a self-contained compact snapshot.
4. `STARTED`/`UNCERTAIN` operations force `full_recovery_required` and never disappear behind an unchanged cursor.
5. Full `project recover` contract tests cover every PS-120 category, task definitions/criteria, submission claims, integration validity, workspace paths, and exact completion blockers.
6. Assignment creation never emits plaintext credentials or context in the default response; credential file permissions and worker authentication still work.
7. `context_hash` is deterministic, assignment-scoped, changes on explicit `CONTINUE`, and cannot suppress live blockers/questions/submissions when only the static context is unchanged.
8. Worker context reads stored approved context rather than silently absorbing an unapproved requirement/task revision.
9. `submission review-context` rejects worker credentials, binds exact base/head OIDs, verifies object existence, reports canonical HEAD separately, and cannot be confused by later branch commits.
10. Review-context bounds test very large file lists/claims, rename/copy statuses, binary files, unusual filenames, and explicit omitted counts without secret leakage.
11. Ready-requirement results exactly match the verify preconditions for direct and task-backed requirements, including plan invalidity, blockers, pending spec review, missing/reverted integrations, and zero-task direct requirements.
12. Any later batch verify rejects duplicates, is all-or-nothing, writes one immutable record/audit event per requirement, and preserves requirement-specific evidence.
13. Skill evaluation scenarios confirm two default gates do not initialize, commit, assign, or launch before the applicable approval and expand for critical work.
14. Telemetry redaction tests ensure raw tokens, credential contents, prompts, evidence output, and diffs are never recorded; unknown rates remain “unknown,” not estimated credits.
15. Benchmark fixtures report response bytes and host tokens for recover versus resume, assignment create/context, review-context workflows, seven versus two gates, and individual versus batch requirement verification.

## Conclusion

Proceed with a constrained efficiency release centered on two approval gates, compact current-state resume, exact-OID review context, safer assignment handoff, and ready-requirement discovery, with minimal measurement shipped alongside. Preserve and repair the full recovery snapshot rather than shrinking it. Do not implement audit-event replay as authoritative state, do not claim material savings from the small assignment-context total, and do not introduce adaptive assurance until actual cost attribution and review-outcome data exist. The defensible optimization target is repeated broad context and low-information turns; the non-negotiable floor is independent, exact-state, criterion-complete verification and deterministic recovery.
