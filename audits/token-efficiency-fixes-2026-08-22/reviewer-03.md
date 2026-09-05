# Independent Audit — Token-Efficiency Proposals

## Scope and method

I audited the current dirty working tree at `/Users/christophercaldwell/Code/oss/taskledger` as-is. I inspected the CLI dispatch, service and database implementation, Taskledger skill, command reference, product specification, technical specification, and acceptance tests. I did not inspect other audit reports or the audit output directory. I treated the supplied usage measurements as empirical inputs, checked their internal implications against current code paths, and did not assume that call frequency proves token causation.

Read-only verification performed:

- `python3 -m unittest discover -s tests -v`: 21 tests passed in 10.822 seconds; one test emitted an unclosed-SQLite `ResourceWarning`.
- `git diff --check -- . ':(exclude)audits/token-efficiency-fixes-2026-08-22/**'`: passed.
- Current tracked modifications span the implementation, specs, skill, README, and tests; conclusions below preserve those changes conceptually rather than comparing against a clean release.

## Executive assessment

The measured burn is real and highly concentrated, but the evidence does not yet attribute most of it to the seven proposed mechanisms. The financial-tracker workload accounts for 1.410B of 1.575B cumulative tokens (89.5%). Terra and Sol together consumed about 10,716 known credits, while auto-review pricing is unknown. The 19-minute Terra run (88.8M tokens, 737 token events, roughly 125k average recent input, four compactions) is much stronger evidence of repeated large-context replay or a non-progress loop than of excessive Taskledger JSON alone.

The best near-term changes are a bounded submission review packet, removal of duplicated assignment context and raw-token return, an explicit ready-requirements view, two consolidated planning approvals for ordinary work, and low-overhead telemetry. A cursor-based resume API is promising but must fall back to the complete recovery snapshot on cold start, stale/unknown cursor, lost local cache, schema mismatch, or any uncertain operation. Adaptive assurance should not ship until risk classification and telemetry exist; no assurance level may relax Taskledger's independent-verification invariants.

The smallest coherent implementation set is:

1. Add privacy-safe workflow-span telemetry outside model-visible context and establish a baseline.
2. Add `submission review-context`, `requirement list --filter ready`, and the reduced `assignment create` response, with compatibility/versioning and exact hashes.
3. Change the skill to two consolidated approvals for ordinary work, retaining explicit branch confirmation and a deterministic critical-work expansion.
4. Add delta resume only after defining cursor/cache validity and fallback semantics.
5. Defer batch verification and adaptive assurance until measurements show that their marginal savings exceed their correctness and migration cost.

## Findings

### TLE-001 — Delta resume needs a trusted baseline and complete-recovery fallback

- **Severity:** High
- **Confidence:** High
- **Evidence:** `Service.recover()` runs preflight and materializes active specifications, reviews, all requirement rows, all tasks, all assignments, questions, proposals, blockers, operations, completion reasons, and next actions (`src/taskledger/service.py:973-977`). The technical contract requires a self-contained first input to a new orchestrator session and enough task/requirement text to operate without conversation history (`taskledger-technical-spec.md:1845-1904`). The product requires recovery without conversation history (`taskledger-product-spec.md:753-786`). Audit events have a monotonic sequence and entity reference, but many mutations record an empty or sparse payload (`src/taskledger/service.py:23-25`, `src/taskledger/db.py:56`), so events alone are not state-complete deltas. `project recover` was called 118 times, 17.7% of the 666 supplied custom tool-call inputs since August 18.
- **Impact:** A compact resume can eliminate repeated whole-graph serialization and prompt replay. However, treating it as sufficient for every non-uncertain operation would make a fresh or compacted orchestrator depend on missing conversation/cache state, violating the authority and recovery contract. A naïve `since-event` stream can also omit derived changes caused by Git reconciliation or specification observation.
- **Recommendation:** Add an orchestrator-only `project resume` that returns a schema version, project ID, canonical HEAD, plan fingerprint, high-watermark event sequence, current safety gates, ready/active action queues, and current snapshots of entities touched since the supplied cursor. Require a caller-held baseline identified by a snapshot hash. Return `full_recover_required: true` (or the full document) for no baseline, unknown/future cursor, schema mismatch, cache loss, cursor retention gap, reconciliation ambiguity, or any `STARTED`/`UNCERTAIN` operation. Capture the post-preflight high-watermark consistently so concurrent mutations cannot be silently skipped. Keep `project recover` for cold starts and authoritative rehydration as well as uncertain operations.
- **Proposal disposition:** **Modify** proposal 1. The phrase “reserve full recover for uncertain operations” is too narrow.

### TLE-002 — Assignment output currently duplicates context and unnecessarily exposes a credential

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** Assignment activation stores canonical `context_json`, then returns the same context plus the raw worker token and token path (`src/taskledger/service.py:601-649`). `worker context` regenerates the full context and adds blockers/submissions (`src/taskledger/service.py:678-681`). The skill tells the orchestrator to pass returned context but never expose the raw token (`skills/taskledger/SKILL.md:42-47`, `:53-57`). The technical spec currently requires a once-returned plaintext token and full context (`taskledger-technical-spec.md:1046-1068`) even though the secret is already written to an owner-only path. Supplied data shows 39 assignment contexts totaling 140,366 characters (3,599 average; 6,745 maximum).
- **Impact:** Removing the duplicate context saves only roughly 35k tokens at four characters per token across the observed contexts before replay amplification, so it cannot explain a 1.410B-token workload by itself. It is still a clean compounding reduction because tool results are replayed in subsequent model inputs. Omitting the raw secret is primarily a security improvement: it keeps the credential out of model-visible command output and reduces accidental logging.
- **Recommendation:** Return IDs, worktree/branch/base OID, token path, and a SHA-256 hash of the exact stored assignment context; do not return plaintext `worker_token` by default. Make this an explicit versioned API change or provide a short deprecation window. `worker context` should return the stored immutable assignment snapshot on first fetch, not silently reconstruct a different document; dynamic questions, blockers, and submissions should have a separate state hash or be explicitly included in the hashed effective document. `if-none-match` should return a small unchanged response only when the caller supplies the exact matching hash. Preserve token rotation and restrictive file permissions.
- **Proposal disposition:** **Modify** proposal 2. Accept the direction, but define hash semantics and compatibility rather than simply deleting fields.

### TLE-003 — A bounded submission review packet is the strongest quality-preserving consolidation

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** Submission rows already store the exact submitted head, changed files, worker summary, evidence, risks, questions, follow-ups, and payload hash (`src/taskledger/db.py:48`). Assignment/task/criteria/requirement/blocker data needed for review is present in adjacent tables. Verification requires every current criterion exactly once and enforces independent assertions (`src/taskledger/service.py:765-819`; `taskledger-product-spec.md:635-677`). The skill requires independent inspection and tests of the exact submitted commit (`skills/taskledger/SKILL.md:46-47`). The CLI has `submission verify` but no read-only review-context command (`src/taskledger/cli.py:156-208`). Supplied counts show 55 verification calls and 43 auto-review threads with 112.4M input tokens.
- **Impact:** One authoritative, action-specific packet can replace repeated recovery/list/context discovery while improving, not weakening, review focus. The danger is that a summary packet could be mistaken for code inspection or could truncate away a risky file.
- **Recommendation:** Implement orchestrator-only `submission review-context` with submission/head OID, assignment base OID, current canonical HEAD, explicit diff endpoints, complete changed-file set or deterministic pagination, bounded diffstat, task revision and criterion IDs/text, linked requirement revision/text, worker claims/evidence/risks/questions/follow-ups, applicable open blockers, and prior verification outcomes. Include truncation flags and retrieval cursors; never silently truncate. State explicitly that the packet does not attest correctness and does not replace inspecting the exact OID, reading relevant diffs, or independently running tests. Do not execute worker-supplied command strings.
- **Proposal disposition:** **Accept** proposal 3 with the stated bounds and non-substitution rule.

### TLE-004 — Adaptive assurance is unsafe until levels are objective and invariant-preserving

- **Severity:** High
- **Confidence:** High
- **Evidence:** Product invariants prohibit worker self-verification and require explicit consideration of every acceptance criterion, intended behavior, evidence, and blockers (`taskledger-product-spec.md:207-227`, `:635-677`). Current verification enforces a result for every criterion and all acceptance assertions (`src/taskledger/service.py:765-805`). The current skill independently inspects and tests every exact submission (`skills/taskledger/SKILL.md:46-47`). No assurance-level field, risk taxonomy, or telemetry table exists in the schema (`src/taskledger/db.py:11-56`).
- **Impact:** A vague “lean” level creates pressure to skip inspection or tests precisely when a task is mistakenly classified as low-risk. Conversely, indiscriminate critical review can amplify the already costly 43 auto-review threads. The supplied metrics do not reveal defect escape rate, review depth, or savings by assurance level, so material benefit is unproven.
- **Recommendation:** Keep `standard` as default. Before any rollout, define deterministic escalation factors: auth/credential handling, destructive or irreversible operations, schema/migration/data integrity, recovery/Git state machines, security boundaries, public compatibility, weak tests, large or cross-cutting diffs, and user-designated criticality. All levels must retain distinct reviewer authority, exact-OID inspection, every criterion, evidence sufficiency, blocker check, and relevant independent tests. Let levels vary breadth (targeted versus broader suites, second reviewer, failure injection), record the selected level and reasons, and allow user override upward. Pilot only after telemetry can compare cost and defects.
- **Proposal disposition:** **Defer** proposal 4 in its current form.

### TLE-005 — Ready-requirements exposure is low risk; batch mutation needs per-item semantics

- **Severity:** Low
- **Confidence:** High
- **Evidence:** `requirement_rows()` reports `COMPLETE`, `BLOCKED`, or `REMAINING`, while `progress()` separately counts requirements whose current covering tasks are all completed (`src/taskledger/service.py:931-942`). `requirement list` has no `ready` filter (`src/taskledger/cli.py:53-57`; `taskledger-technical-spec.md:969-987`). Each verification currently rechecks plan, blockers, task integrations, snapshots, evidence, and notes, then writes one durable record/audit event (`src/taskledger/service.py:895-920`). Only 20 requirement-verification calls appear in the supplied rollout data.
- **Impact:** Exposing readiness removes guesswork and failed calls with little invariant risk. Batching can reduce envelope/tool-turn overhead but is unlikely to address the dominant burn. A shared evidence blob or partial implicit success would degrade requirement-specific judgment and make retries ambiguous.
- **Recommendation:** First add `requirement list --filter ready` with explicit readiness reasons and the exact requirement/task/integration snapshot needed for final judgment. If later adding `verify-batch`, require a complete evidence/notes decision per requirement, apply the same checks as the singleton command, generate distinct verification and audit records, reject duplicate IDs, and define atomic all-or-none behavior plus a batch idempotency key. Do not infer that one test proves every linked requirement.
- **Proposal disposition:** **Modify** proposal 5. Ship readiness first; batch only if telemetry shows meaningful turn savings.

### TLE-006 — Two ordinary planning approvals are defensible, but criticality and branch consent must remain explicit

- **Severity:** Medium
- **Confidence:** Medium-High
- **Evidence:** Seven separate confirmations are imposed by the skill (`skills/taskledger/SKILL.md:22-36`), not by the product or technical ledger state machine. The product does require explicit confirmation of the exact canonical branch (`taskledger-product-spec.md:265-307`), and the CLI enforces exact branch confirmation. The seven gates cover useful content, but their separation adds at least seven user/model round trips for new or materially revised plans. The supplied 88.8M-token run with 737 events and four compactions demonstrates that repeated turns with a roughly 125k-token context can dominate cost, although it does not isolate the seven gates as the cause.
- **Impact:** Consolidation can materially reduce repeated whole-context input and user friction without reducing the information reviewed. Over-consolidation can bury assumptions, parallelism conflicts, or a branch-changing mutation inside a single broad approval.
- **Recommendation:** Use two approvals for ordinary work: (1) one concise plan approval covering outcome/non-goals, task boundaries, dependencies/parallelism, and material assumptions; (2) one execution approval covering exact branch/HEAD/cleanliness, `.taskledger/` ignore/init or commit action, and the first assignment wave. Keep exact branch confirmation mechanically separate in the CLI payload even if presented in the second approval. Expand to the full gates when deterministic critical factors from TLE-004 apply, when the user asks for staged review, or when unresolved product choices prevent a coherent combined decision. Any material change must invalidate the relevant approval.
- **Proposal disposition:** **Modify** proposal 6. Accept two gates as the default only with explicit criticality and mutation-consent rules.

### TLE-007 — Telemetry is necessary, but Taskledger cannot authoritatively measure model credits itself

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** The service schema records domain/audit state but no model, prompt, token, cache, compaction, or credit fields (`src/taskledger/db.py:11-56`). The CLI sees JSON requests and results, not provider billing. Supplied Terra input averages about 113k tokens per token-count event with 97.2% reported cached input; Sol averages about 118k with 97.3% cached input. The known Terra/Sol credit cost is about 10,716, while auto-review cost is unknown. The 666 custom tool-input counts show frequency, not per-command serialized bytes or causal credit contribution.
- **Impact:** Without span-level attribution, optimizations can target visible small payloads while leaving runaway context replay untouched. Embedding verbose telemetry in prompts would itself increase burn, and accepting self-reported credit rates into the authoritative project ledger would mix provider accounting with domain truth.
- **Recommendation:** Instrument the Codex/orchestration boundary or an external append-only metrics sink, not Taskledger's authority state. Record model, reasoning level, input/cached/output tokens, credits when a known rate exists, tool name, response byte size, compactions, elapsed time, and opaque project/task/assignment/submission IDs. Never record prompt contents or credentials. Mark unknown rates as unknown rather than estimating them into totals. Add compact offline reports and workflow-span budgets; feed only threshold alerts, not raw telemetry, into model context.
- **Proposal disposition:** **Modify** proposal 7.

### TLE-008 — The savings claims need causal measurement before broad workflow weakening

- **Severity:** Medium
- **Confidence:** High
- **Evidence:** Observed assignment contexts total only 140,366 characters, whereas the tracker consumed 1.410B tokens. The top three command counts (`project recover` 118, `worker context` 115, `plan validate` 84) are 47.6% of supplied custom tool inputs, but output sizes and downstream replay counts were not supplied. The extreme run's 737 token events and four compactions point to repeated large inputs, but do not identify which command or gate caused them.
- **Impact:** It is plausible that recovery packets and extra turns amplify context, but call-count correlation is insufficient to justify reducing independent review. The system could implement all seven proposals and still retain the dominant loop if it is caused by agent retry behavior, tool schema overhead, or repeated repository inspection.
- **Recommendation:** Measure per-span response bytes, context size before each model call, state-changing versus no-progress iterations, compactions, and credits. Compare matched workflows before and after each change. Treat a reduction as material only if it saves at least 10% of median credits for the targeted workflow or prevents a defined runaway threshold, without increasing correction/rejection/escaped-defect rates.
- **Proposal disposition:** Cross-cutting constraint on all proposals; do not use aggregate totals alone as acceptance evidence.

## Proposal decision matrix

| # | Proposal | Decision | Expected value | Main condition |
|---|---|---|---|---|
| 1 | Compact/delta `project resume` | **Modify** | High for warm sessions | Full recovery also required for cold/cache-invalid state, not only uncertain operations |
| 2 | Remove duplicate assignment context/raw token | **Modify** | Low-to-moderate tokens; high credential-hygiene value | Version response; hash exact stored/effective context; first worker fetch remains complete |
| 3 | Bounded `submission review-context` | **Accept** | High review-focus and turn reduction | Never substitutes for exact diff inspection and independent tests; explicit pagination/truncation |
| 4 | Adaptive lean/standard/critical assurance | **Defer** | Potentially moderate, currently unmeasured | Objective risk taxonomy, invariant floor, telemetry, and pilot required |
| 5 | Batch requirement verification and expose ready | **Modify** | Low-to-moderate | Ready view first; batch keeps per-requirement evidence and atomic/idempotent semantics |
| 6 | Collapse seven planning gates to two | **Modify** | Potentially high turn/context savings | Preserve exact branch/mutation consent; deterministic critical expansion |
| 7 | Instrument usage and escalation point | **Modify** | Enabling value, not direct savings | Measure at runtime boundary; never contaminate authority state or prompts with verbose data |

## Tipping-point framework

Use workflow spans such as plan, assignment, implementation, submission review, integration, and final requirement verification. For each span track credits, wall time, token events, average/peak input, cached fraction, compactions, tool-result bytes, state-changing events, retries, rejection/correction rate, and escaped defects.

The economic decision is:

`escalate when (expected reduction in failure probability × consequence of failure) > incremental escalation cost`

“Escalation” should first mean a fresh bounded context, a targeted diagnostic, or a human decision—not automatically a stronger model with the same bloated transcript. Apply these operational thresholds initially and recalibrate from measured medians:

- **No-progress stop:** after three consecutive model/tool cycles with no new ledger event, repository fact, test result, or user decision, stop replaying context and switch to a bounded diagnostic/resume packet.
- **Context reset:** when mean input over the last five calls exceeds four times the measured bounded working-set packet, when a second compaction occurs in one workflow span, or when the context is near the model limit, start a fresh context from authoritative state.
- **Budget anomaly:** alert when a span exceeds three times the rolling median credits for the same command/task risk class; at five times, require explicit continuation or a changed strategy.
- **Review ceiling:** lean/standard review may not repeat an unchanged inspection more than twice without new evidence. A further pass requires a named critical risk or a concrete unresolved criterion.
- **Critical escalation:** add broader tests, failure injection, or a second independent reviewer only when the expected avoided loss (credential exposure, data corruption, unrecoverable Git state, compatibility break, or similarly high impact) exceeds the measured incremental credits.

On these rules, the observed 88.8M-token, 19-minute span is far beyond the tipping point: four compactions and hundreds of roughly 125k-input events should have triggered a fresh bounded restart long before completion unless each interval produced genuinely new high-risk evidence.

## Risks/regressions

- Delta cursors can miss derived or concurrent state unless tied to a snapshot hash, schema version, and post-preflight high-watermark.
- Removing `worker_token` and `context` is an API/spec compatibility break; older orchestrators may fail unless versioned or migrated together.
- Token-path-only handoff depends on worker filesystem access; failure must be explicit and recoverable through token rotation, not worked around by printing the secret.
- A context ETag over only `context_json` will not detect changed blockers, submissions, or answered questions if those are returned in the same response.
- Review-context truncation can hide the highest-risk file; pagination and explicit completeness indicators are mandatory.
- Assurance labels can become permission to rubber-stamp work unless the invariant floor is enforced in code/skill tests.
- Batch verification can create partial durable state or duplicate evidence on retry without atomicity and idempotency.
- Two combined approvals may obscure exact branch/init/commit consent if the mutation is not separately and plainly stated.
- Telemetry can leak repository or credential data, distort prompt size, or misstate costs when pricing is unknown; collect metadata only and keep unknown rates unknown.
- Current acceptance coverage is happy-path oriented, and the suite emitted a SQLite resource warning; efficiency work touching concurrency/cursors needs stronger failure-injection coverage.

## Tests needed

1. Resume equivalence tests: applying a full snapshot plus every valid delta yields the same actionable state as a fresh `project recover` after each mutation type.
2. Cursor boundary tests: missing, future, stale, pruned, wrong-project, wrong-schema, concurrent, and post-preflight-event cursors force safe fallback.
3. Recovery tests: any started/uncertain operation and external integration rewrite cannot be hidden by compact resume.
4. Assignment response tests: no plaintext token or full context in normal output; token path permissions and rotation remain valid; compatibility behavior is explicit.
5. ETag tests: unchanged exact context returns not-modified; question answer, requirement/task CONTINUE, blocker, or submission changes update the appropriate hash.
6. Review-context tests: exact OIDs and criterion revisions are stable; changed-file completeness, binary/rename/large-list pagination, blockers, prior outcomes, and worker claim fields are correct; worker credentials are denied.
7. Verification non-regression tests: every assurance level still requires every criterion, exact submitted OID inspection workflow, evidence assertions, blocker checks, and independent authority.
8. Ready-requirement tests: direct and task-backed readiness, missing coverage, invalid integration, spec review, plan invalidity, and blocker propagation all produce explicit reasons.
9. Batch tests: duplicate IDs, stale snapshots, one invalid member, crash/retry, idempotency, and transaction behavior preserve one independent record per requirement.
10. Gate UX tests or scripted evaluations: ordinary two-gate and critical expanded flows convey the same material decisions and always isolate exact branch/mutation consent.
11. Telemetry tests: known/unknown rates, cached-token accounting, nested spans, compactions, retries, redaction, and no model-visible verbose payload.
12. Efficiency regression tests: cap serialized byte size for compact endpoints and compare median workflow credits plus review correction/defect rates against baseline.

## Conclusion

The proposals should be pursued as targeted context shaping, not as a relaxation of Taskledger's review or recovery guarantees. Proposal 3 is ready in principle. Proposals 1, 2, 5, 6, and 7 need the modifications above. Proposal 4 should be deferred until instrumentation and an objective risk taxonomy exist. The decisive optimization is to stop repeated replay of large, unchanged context; small JSON savings help, but the 1.410B-token tracker total and the 88.8M-token outlier require loop-level attribution and hard no-progress/context-reset controls.
