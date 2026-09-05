# Independent audit: token-efficiency proposals

## Scope and method

I reviewed the current dirty working tree as-is, limited to Taskledger source, CLI dispatch, schema, skill guidance, tests, README, and the product and technical specifications. I did not inspect the audit output directory or any other reviewer material. This is an audit only; no implementation files were changed.

The review tested each proposal against four questions: whether it is technically sound, whether the supplied evidence supports a material token/credit benefit, whether it preserves Taskledger's authority/security/recovery invariants, and whether it can be implemented without weakening independent review. Evidence labeled “supplied metric” was provided in the audit brief and was not independently reconstructed from external logs. File references are to the dirty working tree.

## Executive assessment

The proposals identify real inefficiencies, but the causal case is uneven. The strongest immediately actionable issue is not token volume but secret handling: `assignment create` returns a raw worker token despite the skill saying never to place raw tokens in prompts or logs. Compact resume and bounded submission review context are good additions if they supplement—not replace—the complete recovery snapshot and independent repository inspection. Batching and fewer conversational gates are plausible secondary savings. Adaptive assurance is acceptable only when it changes review depth, never authorization, exact-OID binding, criterion coverage, integration, recovery, or requirement-verification rules.

The headline usage is severe (1.575B cumulative tokens, 1.410B or 89.5% attributed to the tracker), and the 88.8M-token/19-minute Terra run with four compactions is an obvious runaway pattern. However, command counts alone do not establish causation. The supplied 140,366 characters of assignment contexts are tiny relative to 1.410B tokens: even the deliberately conservative, unrealistic comparison of one character to one token is about 0.010% for one copy. Repetition early in a long thread can amplify cost, but that amplification must be measured. The smallest coherent set is therefore: (1) remove raw secret returns and make assignment context retrieval cache-coherent; (2) add orchestrator-side token/credit instrumentation and runaway detection; (3) add compact resume while retaining complete recovery; and (4) add exact, bounded review context. Gate reduction and batching can follow measured pilots; adaptive assurance should be last.

## Findings

### TEF-001 — The evidence establishes a cost crisis, but not the proposed causal allocation

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** Supplied metrics: 1.410B tracker tokens, 118 `project recover` calls, 115 `worker context` calls, 35 `assignment create` calls, 20 `requirement verify` calls, and 140,366 total assignment-context characters. The anomalous Terra run used 88.8M tokens across 737 token events with four compactions. The repository currently records domain transitions in `audit_events` but no model-usage fields (`src/taskledger/db.py:56`). Taskledger explicitly does not launch models (`README.md:65-67`).
- **Impact:** Implementing all seven proposals at once would make attribution impossible and may spend engineering effort on payloads that are immaterial compared with repeated whole-thread inputs and compaction behavior. Credit estimates also differ by model and cached-input pricing; auto-review cost is unknown.
- **Recommendation:** Instrument before broad workflow changes. Record provider token events at the orchestration layer and correlate them with Taskledger command category, task/assignment/submission IDs, compactions, and durable progress—without storing prompts or credentials. Roll out changes independently or behind flags and compare downstream cumulative input, not merely command response bytes.
- **Proposal disposition:** Proposal 7 — **Modify**. Measurement is required, but core Taskledger cannot observe provider usage by itself.

### TEF-002 — Compact resume is valid only as an additive state view, not a replacement for complete recovery

- **Severity:** High
- **Confidence:** High
- **Evidence:** The product specification requires a complete recovery snapshot and recovery without conversation history (`taskledger-product-spec.md:753-786`). The technical contract says `project recover` is a self-contained first input to a new orchestrator session (`taskledger-technical-spec.md:1845-1904`). The current skill invokes it for every existing project (`skills/taskledger/SKILL.md:12-16`), and current `recover()` returns the whole requirements/tasks/assignments graph (`src/taskledger/service.py:973-977`). Audit events have a monotonic sequence (`src/taskledger/db.py:56`), but their payloads are not a complete replay log.
- **Impact:** A delta-only resume can omit unchanged but decision-critical task/requirement text, invalidations, externally changed Git state, or specification changes. Reserving full recovery only for uncertain operations contradicts the stated new-session recovery contract. Conversely, routine full recovery repeatedly injects a growing graph and is a credible amplification source in long sessions.
- **Recommendation:** Add `project resume` as an orchestrator-only, read-only, preflighted view. It should return a current event cursor, current state hash, phase/progress, unresolved operations, pending reviews/blockers/questions, and actionable entity deltas since a supplied cursor. It must emit `full_recovery_required` for an unknown/stale cursor or unavailable baseline. Preflight must reconcile Git/specification facts before computing the cursor. Retain `project recover` for every context-loss/new-session case as well as uncertain operations. Do not attempt to reconstruct current state solely from audit payloads.
- **Proposal disposition:** Proposal 1 — **Modify**.

### TEF-003 — Removing raw tokens is a security win; context deduplication needs a split immutable/dynamic cache contract

- **Severity:** High
- **Confidence:** High
- **Evidence:** `assignment_create()` returns both `worker_token` and full `context` (`src/taskledger/service.py:644-649`), while the skill says raw tokens must never enter prompts, chat, summaries, logs, or evidence (`skills/taskledger/SKILL.md:43-55`). The technical specification also requires token redaction (`taskledger-technical-spec.md:2017-2029`), although its current assignment response contract explicitly includes the raw secret (`taskledger-technical-spec.md:1046-1068`). Context is stored durably (`src/taskledger/db.py:44`), but `worker_context()` regenerates it from current tables instead of reading `context_json` (`src/taskledger/service.py:601-611,678-681`). The specification calls the assignment context an immutable snapshot except after explicit `CONTINUE` (`taskledger-technical-spec.md:1305-1338`), while current answers/blockers/submissions are dynamic.
- **Impact:** Returning the raw token creates an avoidable credential-exposure path. Returning and then retrieving the same full context is redundant, but the supplied context volume alone does not support a material share of 1.410B tokens. A single hash over regenerated context can also hide stale snapshot semantics or suppress changed answers/blockers.
- **Recommendation:** Immediately stop returning raw tokens from `assignment create` (and align `assignment rotate-token`, which has the same issue at `src/taskledger/service.py:664-671`); return only the restrictive token path. Amend the technical contract and provide a compatibility/version plan. Return `context_hash = SHA-256(canonical persisted context_json)`. Make worker retrieval use the persisted assignment snapshot, with dynamic questions/answers, open blockers, and submissions in a separately hashed envelope. `if-none-match` must cover the response component it suppresses and return explicit `not_modified`; it must not suppress a changed dynamic sidecar. Preserve worker-scoped authentication and never expose orchestrator state.
- **Proposal disposition:** Proposal 2 — **Modify** (accept secret removal; qualify the claimed token benefit and cache design).

### TEF-004 — Bounded review context improves focus only if it remains exact, lossless about claims, and non-authoritative

- **Severity:** High
- **Confidence:** High
- **Evidence:** Submissions already store immutable head OID, changed files, evidence, risks, unresolved questions, and follow-up work (`src/taskledger/db.py:48`; `src/taskledger/service.py:737-762`). Verification must consider every acceptance criterion and exact submitted state (`taskledger-product-spec.md:625-650`), and the orchestrator must independently inspect/test rather than execute worker-supplied evidence commands (`skills/taskledger/SKILL.md:46-57`). No `submission review-context` command is dispatched (`src/taskledger/cli.py:156-208`).
- **Impact:** A purpose-built view can prevent repeated broad recoveries and reduce search/setup work. But “bounded” implemented as silent truncation, summaries, or omitted negative evidence can bias review. Diffstat/file lists are not a substitute for reading the exact diff or independently running relevant tests.
- **Recommendation:** Add an orchestrator-only read command keyed by submission ID. Include submission/base/head OIDs; task revision and criterion IDs/text; immutable worker summary/evidence/risks/questions/follow-ups verbatim within existing field limits; Git-derived name-status and diffstat for exactly `base..head`; linked current requirement revisions/source references; applicable open blockers; and explicit stale/missing-object diagnostics. Use pagination or explicit `truncated_fields` plus hashes for oversized arrays—never silent truncation. State in the skill that the view frames review but does not satisfy it; exact diff inspection and proportionate independent checks remain required.
- **Proposal disposition:** Proposal 3 — **Modify**.

### TEF-005 — Assurance levels must not weaken any ledger or independent-review invariant

- **Severity:** High
- **Confidence:** High
- **Evidence:** Only the orchestrator may verify (`taskledger-product-spec.md:83-102`); every submission requires a distinct verification context and explicit consideration of every criterion, intended behavior, evidence, and blockers (`taskledger-product-spec.md:635-658`). The CLI enforces a result for every current criterion (`src/taskledger/service.py:765-790`). The current repository has no assurance-level model or policy. The supplied metrics do not break usage down by risk class or review depth.
- **Impact:** A vague “lean” tier could turn worker claims into accepted evidence, skip criteria, miss security/recovery regressions, or create a false assurance label. Conversely, requiring the same expensive review pattern for trivial documentation and security-sensitive recovery code wastes credits.
- **Recommendation:** Keep authorization, exact-OID binding, every-criterion decisions, blocker checks, integration, and final requirement verification identical at all levels. Let assurance affect only the breadth of independent tests/inspection and model strength. Default to `standard`; permit `lean` only with an explicit low-consequence rationale and no critical trigger. Force `critical` for credential/auth changes, schema/data migration, recovery/Git mutation, concurrency, destructive/irreversible actions, or unresolved/low-confidence behavior. Record the chosen level and rationale outside authoritative completion semantics until validation data exists.
- **Proposal disposition:** Proposal 4 — **Modify and pilot after instrumentation**.

### TEF-006 — Ready-requirement exposure is safe; batch verification must remain requirement-atomic

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** `requirement_rows()` exposes only `COMPLETE`, `BLOCKED`, or `REMAINING` (`src/taskledger/service.py:931-937`), while progress derives `awaiting_final_verification` from completed covering tasks (`src/taskledger/service.py:940-942`). `requirement_verify()` validates and snapshots one requirement at a time (`src/taskledger/service.py:895-920`). The product specification permits derived operational substates while keeping the three canonical statuses (`taskledger-technical-spec.md:1703-1717`). Only 20 verification calls appear in the supplied rollout counts.
- **Impact:** A `ready` filter prevents failed probe calls and helps the orchestrator group evidence gathering. Batch envelopes can reduce round trips, but semantic evidence and snapshots remain requirement-specific. An all-or-nothing batch can make one stale item block unrelated valid verifications; partial success can be ambiguous without per-item results.
- **Recommendation:** First expose a derived `ready_for_verification` boolean/reason list without creating a fourth persisted status. Then add a bounded batch command whose input contains complete evidence and notes per requirement, validates each independently, writes one audit event per successful requirement, and returns per-item results. Choose and document transaction semantics; per-item atomic transactions with explicit successes/failures are preferable for stale-state resilience. Never infer evidence or auto-verify a group merely because tasks are integrated. Use batching only when at least two requirements are ready and evidence collection is genuinely shared.
- **Proposal disposition:** Proposal 5 — **Modify**.

### TEF-007 — Seven conversational gates are not a product invariant, but replacing them needs an explicit two-gate contract

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** The seven separate confirmations are skill guidance added in the dirty tree (`skills/taskledger/SKILL.md:22-36`), not a Taskledger product-spec requirement. They separate outcome, work chunks, granularity, parallelism, assumptions, working base, and launch. The supplied command counts do not measure these chat turns, but repeated turns can carry a large accumulated context; the 88.8M-token run with four compactions shows why turn count can matter.
- **Impact:** Seven default round trips can multiply accumulated input and trigger compaction. Collapsing them without defining retained decisions can silently authorize initialization, commits, parallel work, or worker launch and increase expensive rework.
- **Recommendation:** Use two default gates for non-critical new plans: (1) one concise plan/base approval containing outcome, non-goals, task boundaries, dependencies/parallelism, material assumptions, exact branch/HEAD/cleanliness, and exact init/first-commit action; (2) after ledger materialization and successful validation, one launch approval naming the first assignment wave. A material change reopens the relevant gate. Retain the full sequence for critical work or when the user requests staged review. Pure resume retains the existing no-repeat rule.
- **Proposal disposition:** Proposal 6 — **Modify**.

### TEF-008 — Token instrumentation belongs at the orchestrator/provider boundary, not in authoritative domain state

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** Taskledger is a deterministic ledger and must not become generic workflow automation (`taskledger-product-spec.md:57-75,133-152`). Its observability contract requires local diagnostics/audit/recovery, not provider telemetry (`taskledger-technical-spec.md:2111-2120`). Cached-input proportions are very high in the supplied data (Terra: 1,059,004,928 of 1,090,070,288 input; Sol: 197,913,600 of 203,321,840 input), so raw token count and credits are not interchangeable.
- **Impact:** Core-ledger accounting would be incomplete and misleading because the CLI cannot observe model events, cache pricing, auto-review rates, or compaction. Storing prompt content or credentials to improve attribution would create a security/privacy regression.
- **Recommendation:** Instrument the Codex orchestration integration or an external append-only sidecar. Store counts and IDs only: timestamp, model/tier, input/cached/output tokens, provider rate-card version, estimated credits, thread/task/assignment/submission correlation, command category, compaction count, and durable-progress marker. Do not store prompt bodies, CLI JSON, evidence commands, or credentials. Sample or aggregate if instrumentation itself exceeds 1% of measured token/credit cost.
- **Proposal disposition:** Proposal 7 — **Modify**.

## Proposal decision matrix

| # | Proposal | Decision | Expected benefit | Benefit-vs-token-burn tipping point |
|---|---|---|---|---|
| 1 | Compact/delta resume | **Modify** | Potentially high in long-lived projects; unquantified | Use compact resume when a trusted cursor/state hash exists and measured downstream input is lower; require full recovery after context loss, stale cursor, or uncertain state. |
| 2 | Deduplicate assignment context | **Modify** | High security benefit; likely low direct token benefit | Remove raw tokens regardless of savings. Cache context when retrieval plus downstream repetition costs more than a small hash/status exchange; invalidate dynamic components independently. |
| 3 | Submission review context | **Modify** | Moderate focus and quality benefit | Use when it replaces broad recovery/search setup; escalate to direct repository inspection whenever the bounded view cannot answer a criterion—which should be routine for code changes. |
| 4 | Adaptive assurance | **Modify / pilot later** | Potentially high, highest quality risk | Use lean only when no critical trigger exists and expected marginal review cost exceeds risk-adjusted failure cost; critical triggers override token budget. |
| 5 | Batch requirement verification / ready view | **Modify** | Low-to-moderate at observed volume | Batch only when two or more requirements are concurrently ready and each has independent evidence; otherwise single verification is clearer and cheaper. |
| 6 | Two planning gates by default | **Modify** | Potentially high due fewer accumulated-context turns; unmeasured | Use two gates for ordinary work when the combined presentation remains understandable; split/full gates when risk, ambiguity, or decision density would impair informed approval. |
| 7 | Usage instrumentation / escalation point | **Modify** | Foundational; indirect savings | Instrument while overhead stays below 1% of measured cost; use provider-side data, not estimates from CLI bytes, for credit decisions. |

## Tipping-point framework

Use marginal, not cumulative, economics. For a proposed extra review step, compare:

`expected value of extra assurance = probability reduction of a missed defect × consequence/rework credits`

against:

`marginal assurance cost = incremental uncached input credits + cached input credits + output credits + expected compaction/restart cost`.

Choose the higher assurance level whenever expected assurance value exceeds marginal cost, and always when a critical trigger affects credentials, authority boundaries, durable state, recovery, integration correctness, destructive actions, or irreversible external effects. Token budgets do not override those invariants.

Operational runaway rules should be calibrated from telemetry, but the supplied outlier supports immediate conservative guards:

1. Mark a durable-progress event after an accepted decision, ledger mutation, successful test milestone, or resolved blocker—not after mere tool activity.
2. Warn/re-scope when input tokens since the last durable-progress event exceed three times the trailing median for comparable tasks.
3. Force a compact checkpoint and fresh-session/full-recovery decision after two compactions without durable progress. The cited run reached four.
4. Escalate from lean to standard on any failed independent check, ambiguity, broad unexpected diff, stale context/hash, or new blocker.
5. Escalate directly to critical for the invariant-sensitive triggers above, irrespective of estimated savings.
6. Compare variants on total downstream credits through completion and defect/rework rate, not output byte reduction per command.

## Risks/regressions

- A delta cursor can miss external Git/specification changes unless preflight observations occur before cursor/hash calculation and are represented in current state.
- Removing raw tokens is a response-contract break; older callers/tests currently read `worker_token` directly (`tests/test_acceptance.py:262,307,324,373`). A versioned migration is required.
- A hash over mutable regenerated context defeats snapshot semantics. Immutable assignment context and dynamic worker state need distinct validators.
- Bounded review output may omit the fact most relevant to rejection. Truncation must be explicit and recoverable, and summaries must not replace immutable worker claims.
- Assurance labels can become permission shortcuts. They must never weaken role checks, criterion completeness, exact-OID verification, blocker propagation, integration, or final requirement verification.
- Batch verification can obscure partial failure, ordering, and stale-state behavior; per-item results and audit records are mandatory.
- Fewer gates can overload a single approval prompt and reduce informed consent. The two-gate content contract and critical-work fallback are necessary.
- Provider rate cards and caching rules change. Store rate-card versions and retain raw token dimensions; do not persist estimated credits as unquestioned truth.
- Instrumentation must not capture prompts, raw CLI envelopes containing secrets, evidence command strings for execution, or credential paths beyond the minimum correlation need.
- The dirty implementation remains experimental and lacks the full failure-injection/66-requirement suite (`README.md:140-144`); optimization should not be represented as increased recovery assurance.

## Tests needed

1. **Resume correctness:** golden full-recover versus resume+baseline equivalence; stale/unknown/future cursor; no-event changes; spec file mutation; canonical rewrite; pending/uncertain operation; completed-project invalidation; deterministic ordering and state hash.
2. **Resume security:** worker tokens cannot invoke resume/recover; no credentials or raw context leaks; cursor from another project is rejected.
3. **Context handoff:** assignment responses omit raw tokens and full context; credential file permissions; old token invalidation on rotation; persisted context hash stability; explicit `CONTINUE` changes the hash; `if-none-match` hit/miss; dynamic questions/answers/blockers/submissions invalidate only their envelope; revoked worker denial.
4. **Review context:** exact base/head OIDs and object-existence check; rename/copy/binary diffstat; criterion revision correctness; linked requirements and applicable blockers; immutable claim preservation; explicit pagination/truncation; worker authorization denial; no execution of evidence commands.
5. **Assurance policy:** classification table tests for every critical trigger; lean still requires every criterion and independent evidence; failed check automatically escalates; level cannot change ledger authorization or completion results.
6. **Requirement readiness/batch:** direct and task-backed readiness; blockers/review/recovery/invalid-plan cases; multiple requirements sharing tasks; per-item stale state; mixed success; idempotent retry; one audit event and correct snapshot per success; no inferred evidence.
7. **Gate workflow:** two-gate ordinary path preserves exact init/commit and launch approvals; material change reopens approval; critical classification selects full gates; resume does not replay accepted gates.
8. **Telemetry:** provider fixture reconciliation for input/cached/output and credits; unknown rate handling; compaction/runaway alerts; correlation without prompt/secret capture; overhead measurement and sampling fallback.
9. **Regression:** full acceptance, authorization matrix, recovery/failure-injection, plan fingerprint, integration reachability, and requirement-currentness suites must remain unchanged across assurance levels.

## Conclusion

Proceed, but in a narrower order than proposed. First remove raw secret returns and establish cache-coherent assignment context; simultaneously add provider-side measurement and runaway guards. Next add compact resume as an additive view and exact bounded submission review context. Pilot two-gate planning and ready/batch verification using measured completion credits and rework. Defer adaptive assurance until those measurements exist, and constrain it to review depth only. The complete recovery snapshot, exact submitted OID, independent criterion-by-criterion verification, explicit blocker handling, scoped authority, integration proof, and requirement-based completion are non-negotiable at every optimization level.
