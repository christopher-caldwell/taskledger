# Task Ledger Supervisor — Exhaustive Test Plan

**Status:** design/verification plan
**Date:** 2026-09-10
**Scope:** deterministic Task Ledger supervisor + future Codex runtime adapter
**Inventory:** **190 no-Codex cases + 111 live-Codex cases = 301 total cases**

This plan is intended to be exhaustive against the current supervisor design and the failure surfaces already identified. It is a living verification document: cases may be split into more granular tests as implementation reveals new boundaries, but a case should not be removed unless its invariant is deliberately retired.

The test strategy assumes the architecture under discussion:

- Task Ledger remains the durable authority for requirements, tasks, assignments, submissions, verification, blockers, Git integration, recovery, and completion.
- A deterministic Python supervisor owns dispatch, waiting, retries, budgets, recovery coordination, and scheduling.
- Model turns may stop at any ordinary boundary. A turn ending is never treated as proof that work is complete.
- Worker threads are bounded to assignment attempts/correction cycles; unrelated tasks should not inherit their transcript by default.
- Independent reviewers operate on immutable submissions and return structured verdicts.
- The first development phases use a fake runtime so most correctness can be proven without model usage.

## Test priorities

- **P0 — release blocker:** violation can cause false completion, duplicate external work, unsafe Git/ledger mutation, lost work, privilege leakage, or unbounded token consumption.
- **P1 — required before routine use:** important correctness, operability, cost-control, recovery, or security behavior.
- **P2 — hardening/benchmark:** scale, portability, upgrades, optimization, and non-critical resilience.

Current inventory by priority:

| Category | P0 | P1 | P2 | Total |
|---|---:|---:|---:|---:|
| No Codex | 115 | 58 | 17 | 190 |
| With Codex | 39 | 57 | 15 | 111 |
| **Total** | **154** | **115** | **32** | **301** |

## Architectural invariants under test

- **INV-01** — A Codex/model turn ending is only a transport event; it never proves assignment completion.
- **INV-02** — Task Ledger durable state is authoritative over model prose, controller memory, and runtime events.
- **INV-03** — The supervisor does not directly mutate authoritative requirement/task/submission/verification/integration rows.
- **INV-04** — At most one active worker session owns a given assignment attempt.
- **INV-05** — A reviewer evaluates one immutable submission revision; stale verdicts cannot be applied to another revision.
- **INV-06** — Independent review does not begin while the worker can still mutate the submitted worktree.
- **INV-07** — Review and required checks are bound to the exact submitted commit/OID.
- **INV-08** — Every required deterministic check must have current independent reviewer evidence before acceptance.
- **INV-09** — A correctable rejection returns to the same assignment worker thread unless policy requires escalation/replacement.
- **INV-10** — Routine-worker rejection escalation policy is preserved exactly.
- **INV-11** — Semantic acceptance is distinct from successful Git integration.
- **INV-12** — Unknown/uncertain external outcomes pause; they are never converted into success, failure, or a duplicate dispatch by guesswork.
- **INV-13** — Turn, stall, failure, elapsed-time, and usage budgets survive supervisor restarts.
- **INV-14** — A crash at any dispatch/acknowledge/wait/consume boundary cannot silently create duplicate authoritative work.
- **INV-15** — Completed external results remain durable until the supervisor has consumed their semantic effect.
- **INV-16** — Only one controller process may actively supervise a project at a time unless the future design explicitly supports distributed ownership.
- **INV-17** — Orchestrator/controller credentials are never placed in model prompts or ordinary model-readable files.
- **INV-18** — Worker authority remains assignment-scoped and is revoked/rotated according to Task Ledger state.
- **INV-19** — Reviewer filesystem/tool authority is no broader than required for independent inspection and checks.
- **INV-20** — Task/specification/criteria/plan changes invalidate stale assignments, review packets, and verdicts rather than being silently accepted.
- **INV-21** — Progress detection reflects actual worktree content plus relevant durable ledger state; prose/activity alone is not progress.
- **INV-22** — Usage accounting is monotonic, deduplicated, and does not double-count cumulative runtime telemetry.
- **INV-23** — Scheduling never violates task dependencies, approved concurrency groups/waves, or known write-set conflicts.
- **INV-24** — Runtime concurrency never exceeds configured worker/reviewer capacity.
- **INV-25** — Project completion remains requirement-based and requires integrated, current evidence; an empty queue is insufficient.

## Test environments

### E0 — Pure fake

No real Git repository, no real Task Ledger database, no Codex. Uses `FakeLedger`, `FakeRuntime`, and the controller journal. Purpose: state-machine, journal, parsing, budget, and crash-boundary logic.

### E1 — Real Task Ledger / fake runtime

Disposable real Git repository + real Task Ledger SQLite + real Task Ledger `Service`/CLI behavior + fake model runtime. This is the most important pre-Codex environment. It should cover real worktrees, credentials, submissions, reviewer receipts, blockers, integration, recovery, requirement verification, and completion with zero model usage.

### E2 — Fault-injection Task Ledger

Same as E1, with targeted process kills, monkey-patched Git/SQLite failures, filesystem permission failures, corrupted runtime/journal responses, clock/sleep simulation, and concurrent controller processes.

### E3 — Minimal live Codex smoke

Disposable repository and tiny tasks specifically constructed to verify SDK/runtime contracts: authentication, thread persistence, resume, output schemas, sandboxing, usage telemetry, and crash/reconnect behavior. Keep prompts/tasks tiny to minimize paid usage.

### E4 — Live representative benchmark

Representative completed project such as the equipment-lending test. Uses production-like worker/reviewer profiles and records full cost/correctness telemetry. This environment is not entered until E0–E3 P0 gates pass.

## Required per-test evidence

Every automated test should retain enough evidence to answer *why* it passed:

- controller run/session/turn IDs;
- relevant Task Ledger IDs (task, assignment, submission, verification, integration, blocker);
- pre/post task and assignment state;
- pre/post Git OIDs and worktree fingerprint where applicable;
- runtime thread/turn IDs for live tests;
- exact model, effort, sandbox, cwd, and resolved runtime version for live tests;
- raw per-turn token fields and deduplicated totals for usage tests;
- pause reason/error classification;
- assertions proving no duplicate turn/review/integration occurred.

---

# Category A — Tests Without Codex

These tests should be run continuously in CI/local development. They should consume **zero model usage**. Use E0 for pure controller tests and E1/E2 once the real Task Ledger adapter exists.

## A1. Supervisor lifecycle and terminal-state semantics

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-001** | P0 | Supervisor | Worker turn makes progress but no submission | Consume the turn, record progress, keep assignment ACTIVE, and dispatch another worker turn on the same assignment session. |
| **NC-002** | P0 | Supervisor | Worker turn ends with no progress and no terminal ledger state | Count one durable stall; continue only while stall budget permits. |
| **NC-003** | P0 | Supervisor | Worker reaches stall limit exactly | Pause STALLED before another turn is dispatched. |
| **NC-004** | P0 | Supervisor | Worker reaches max turn count exactly | Pause MAX_TURNS before exceeding the durable turn budget. |
| **NC-005** | P0 | Supervisor | Worker creates a pending submission during a completed turn | Consume the worker turn, stop implementation dispatch, and transition to review path. |
| **NC-006** | P0 | Supervisor | Worker creates a blocking question | Consume completed work, pause BLOCKED, and do not invoke reviewer. |
| **NC-007** | P0 | Supervisor | Worker creates an assignment/project blocker | Pause BLOCKED and preserve worker session for later resumption if still valid. |
| **NC-008** | P0 | Supervisor | Assignment is revoked between worker turns | Do not dispatch another worker turn; close/retire runtime session according to policy. |
| **NC-009** | P0 | Supervisor | Assignment is escalated after routine rejection limit | Do not continue routine session; surface ESCALATION_REQUIRED/new complex assignment requirement. |
| **NC-010** | P0 | Supervisor | Task becomes ACCEPTED but integration failed safely | Pause INTEGRATION_FAILED; never report INTEGRATED. |
| **NC-011** | P0 | Supervisor | Integration outcome becomes uncertain | Pause INTEGRATION_UNCERTAIN and require Task Ledger recovery proof. |
| **NC-012** | P0 | Supervisor | Task is already COMPLETED when supervisor starts | Return INTEGRATED/terminal result without launching a model session. |
| **NC-013** | P0 | Supervisor | Execution projection contains SUBMITTED without submission ID | Pause INVALID_STATE; do not guess the submission. |
| **NC-014** | P0 | Supervisor | Execution projection returns an unknown/impossible state | Fail closed with INVALID_STATE and no model dispatch. |
| **NC-015** | P1 | Supervisor | Correction packet appears after rejection | Next worker prompt includes current correction packet and uses same assignment thread. |
| **NC-016** | P1 | Supervisor | Correction packet is replaced by newer applicable correction | Only latest authoritative correction is used. |
| **NC-017** | P1 | Supervisor | Correction packet disappears after acceptance/revision | Do not replay stale correction. |
| **NC-018** | P1 | Supervisor | Worker profile changes while an existing active runtime session has old profile | Pause INVALID_STATE; never silently run wrong configured model profile. |
| **NC-019** | P1 | Supervisor | Supervisor starts with terminal unconsumed worker turn | Consume/reconcile it before any new worker dispatch. |
| **NC-020** | P1 | Supervisor | Supervisor starts with terminal unconsumed reviewer turn | Consume/apply/reconcile it before purchasing a new review. |
| **NC-021** | P1 | Supervisor | Known failed worker turn followed by valid progress | Retry within failure budget and continue normal lifecycle. |
| **NC-022** | P1 | Supervisor | Known failed worker turns reach retry limit | Pause RUNTIME_FAILED without another dispatch. |
| **NC-023** | P0 | Supervisor | Runtime outcome is uncertain | Pause RUNTIME_UNCERTAIN and never auto-dispatch replacement turn. |
| **NC-024** | P2 | Supervisor | Supervisor result counters after recovery | Counters distinguish newly-dispatched turns from recovered/consumed prior turns and remain internally consistent. |

## A2. Reviewer/verdict contract

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-025** | P0 | Reviewer | Reviewer ACCEPTED verdict covers every expected criterion exactly once | Verdict parses and may proceed only if all procedural acceptance gates are true. |
| **NC-026** | P0 | Reviewer | Reviewer omits one criterion | Reject verdict as malformed; do not mutate submission state. |
| **NC-027** | P0 | Reviewer | Reviewer adds unknown criterion ID | Reject verdict as malformed. |
| **NC-028** | P0 | Reviewer | Reviewer duplicates a criterion ID | Reject verdict as malformed. |
| **NC-029** | P0 | Reviewer | Criterion satisfied value is non-boolean | Reject structured verdict. |
| **NC-030** | P0 | Reviewer | Criterion evidence missing/empty where contract requires evidence | Reject structured verdict. |
| **NC-031** | P0 | Reviewer | ACCEPTED while one criterion is false | Reject as internally inconsistent. |
| **NC-032** | P0 | Reviewer | ACCEPTED while behavior_matches_intent=false | Reject as internally inconsistent. |
| **NC-033** | P0 | Reviewer | ACCEPTED while required_evidence_present=false | Reject as internally inconsistent. |
| **NC-034** | P0 | Reviewer | ACCEPTED while blocking_issues_remaining=true | Reject as internally inconsistent. |
| **NC-035** | P0 | Reviewer | ACCEPTED when required independent checks failed/missing | Acceptance is procedurally impossible; do not apply verdict. |
| **NC-036** | P0 | Reviewer | REJECTED with no unmet assertion | Reject malformed verdict. |
| **NC-037** | P0 | Reviewer | REJECTED without actionable corrections | Reject malformed verdict. |
| **NC-038** | P0 | Reviewer | BLOCKED without blocker semantics | Reject malformed verdict. |
| **NC-039** | P1 | Reviewer | Valid REJECTED verdict | Apply once, persist correction, and keep/reopen task according to Task Ledger policy. |
| **NC-040** | P1 | Reviewer | Valid BLOCKED verdict | Apply once, create/reference blocker, pause supervision. |
| **NC-041** | P0 | Reviewer | Reviewer returns prose but no structured output | Retry only within reviewer-turn budget; never infer verdict from prose. |
| **NC-042** | P0 | Reviewer | Reviewer returns invalid JSON/schema output repeatedly | Pause REVIEWER_FAILURE at budget. |
| **NC-043** | P1 | Reviewer | Reviewer known runtime failure then valid verdict | Retry within reviewer failure/turn limits without applying phantom result. |
| **NC-044** | P0 | Reviewer | Reviewer runtime outcome uncertain | Pause RUNTIME_UNCERTAIN without buying a duplicate review. |
| **NC-045** | P0 | Reviewer | Submission changes/supersedes before completed verdict is applied | Detect stale submission and do not apply verdict. |
| **NC-046** | P0 | Reviewer | Criteria/task revision changes before verdict apply | Invalidate verdict and require a current review. |
| **NC-047** | P0 | Reviewer | Plan/specification becomes invalid before verdict apply | Do not accept/integrate under stale plan. |
| **NC-048** | P1 | Reviewer | Same persisted verdict encountered twice | Apply at most once; second encounter reconciles against authoritative submission state. |
| **NC-049** | P1 | Reviewer | Fresh immutable resubmission after rejection | Use a fresh reviewer session/thread for the new submission. |
| **NC-050** | P2 | Reviewer | Reviewer notes/corrections contain large but valid payload | Respect configured size limits or reject deterministically; no DB corruption/truncation ambiguity. |

## A3. Journal durability and crash recovery

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-051** | P0 | Journal | Crash after local DISPATCHING row is committed but before external start_turn is called | Recovery proves no external turn ID; marks outcome uncertain or safely failed according to runtime contract; no blind duplicate. |
| **NC-052** | P0 | Journal | Crash after external start_turn succeeds but before external turn ID is journaled | Enter explicit unreconciled/uncertain state; never assume no turn exists. |
| **NC-053** | P0 | Journal | Crash after external turn ID journaled but before wait | Resume/inspect same external turn. |
| **NC-054** | P0 | Journal | Crash while external turn is RUNNING | Inspect same turn after restart; do not create another turn. |
| **NC-055** | P0 | Journal | Crash after runtime completed but before complete_turn journal write | Inspect runtime and persist same result once. |
| **NC-056** | P0 | Journal | Crash after complete_turn journal write but before semantic consumption | Consume persisted result; no repeat runtime call. |
| **NC-057** | P0 | Journal | Crash after reviewer verdict applied to Task Ledger but before consume_turn | Reconcile authoritative submission/task state and mark result consumed without reapplying/reviewing. |
| **NC-058** | P0 | Journal | Crash after worker turn progress changed but before progress_after persisted | Recompute actual progress from ledger/worktree and consume exactly once. |
| **NC-059** | P0 | Journal | Crash after acceptance before integration return | Use Task Ledger operation/integration recovery state; never manufacture completion. |
| **NC-060** | P0 | Journal | Crash during uncertain Git integration | Supervisor pauses until existing Task Ledger recovery resolves operation. |
| **NC-061** | P0 | Journal | Restart after PAUSED STALLED | Stall budget remains exhausted unless explicit reset/extension policy is invoked. |
| **NC-062** | P0 | Journal | Restart after MAX_TURNS | Turn budget remains exhausted. |
| **NC-063** | P1 | Journal | Restart after one known runtime failure | Failure streak remains counted. |
| **NC-064** | P1 | Journal | Successful progress after prior stall | Consecutive stall streak resets; historical stalls remain in audit. |
| **NC-065** | P1 | Journal | Successful runtime turn after prior known failure | Consecutive failure streak resets if policy specifies; total failures remain recorded. |
| **NC-066** | P0 | Journal | Attempt to begin second unresolved turn in same session | Reject locally before runtime call. |
| **NC-067** | P0 | Journal | Duplicate acknowledge_turn for same local turn | Second/stale acknowledgement is rejected and cannot change identity. |
| **NC-068** | P0 | Journal | Duplicate complete_turn for same external result | Idempotent/reconcilable without duplicate usage or result rows. |
| **NC-069** | P0 | Journal | complete_turn for wrong local turn/session | Reject; no cross-session result contamination. |
| **NC-070** | P0 | Journal | consume_turn before completion/failure | Reject. |
| **NC-071** | P1 | Journal | consume_turn called twice | Second call is idempotent/no-op; timestamps/usage do not duplicate. |
| **NC-072** | P0 | Journal | Runtime inspection says UNKNOWN for journaled RUNNING turn | Mark/pause uncertain rather than replacing it. |
| **NC-073** | P0 | Journal | Runtime inspection says FAILED for journaled RUNNING turn | Persist known failure and apply retry budget. |
| **NC-074** | P0 | Journal | Runtime inspection says COMPLETED for journaled RUNNING turn | Persist exact returned result/usage and continue consumption. |
| **NC-075** | P1 | Journal | Controller SQLite transaction interrupted mid-write | Atomic rollback; no half-created run/session/turn. |
| **NC-076** | P1 | Journal | Journal reopened after process death | WAL/recovery yields internally consistent schema and states. |
| **NC-077** | P1 | Journal | Result JSON cannot be decoded/corrupted | Fail closed with actionable journal corruption error; do not invoke model. |
| **NC-078** | P1 | Journal | External thread ID missing on ACTIVE session | Pause INVALID_STATE/repair path; never create hidden replacement. |
| **NC-079** | P1 | Journal | Multiple historical CLOSED sessions for same subject | Select no closed session as active; preserve history. |
| **NC-080** | P0 | Journal | Two ACTIVE sessions for same project/role/subject attempted | Unique constraint rejects second owner. |
| **NC-081** | P2 | Journal | Large session/turn history | Queries for active/unconsumed/open turns remain bounded/index-backed. |
| **NC-082** | P2 | Journal | Clock moves backward/forward substantially | Correctness does not depend on wall-clock ordering for identity/state; durations may be annotated but no false state transition. |

## A4. Project ownership and multi-controller safety

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-083** | P0 | Ownership | Second supervisor process starts for same project while first lock held | Second process fails immediately; first remains owner. |
| **NC-084** | P0 | Ownership | First supervisor dies ungracefully | OS releases advisory lock; next process can acquire and reconcile. |
| **NC-085** | P1 | Ownership | Machine sleeps longer than expected model turn | Lock remains held; no TTL-expiry duplicate controller. |
| **NC-086** | P1 | Ownership | Different projects share same journal directory | Distinct project locks allow concurrent supervision. |
| **NC-087** | P0 | Ownership | Same project reached through different relative paths/aliases | Canonical project key maps to same lock; no duplicate ownership. |
| **NC-088** | P1 | Ownership | Symlinked journal path attempts to bypass lock namespace | Canonical location/policy prevents two locks for same project. |
| **NC-089** | P1 | Ownership | Lock file exists but no process holds flock | New supervisor acquires normally. |
| **NC-090** | P1 | Ownership | Lock directory/file permission failure | Supervisor fails before model dispatch. |
| **NC-091** | P1 | Ownership | Platform lacks fcntl | Explicit unsupported/error path; no unsafe no-lock fallback. |
| **NC-092** | P2 | Ownership | SIGINT/SIGTERM graceful stop | Journal run state is finalized where possible and OS lock releases. |
| **NC-093** | P2 | Ownership | SIGKILL during turn | OS lock releases; restart enters reconciliation path. |
| **NC-094** | P1 | Ownership | Controller exception after acquiring lock | finally/context manager releases lock. |
| **NC-095** | P1 | Ownership | Nested lock request in same process | Behavior defined and tested: either supported reentrantly or rejected deterministically. |
| **NC-096** | P1 | Ownership | Controller A for project X and controller B for project Y use same runtime client | No state/turn cross-routing. |
| **NC-097** | P0 | Ownership | Old supervisor resumes after network/process stall while new supervisor owns project | Impossible under single-host OS lock; if future distributed mode exists, fencing token must reject old writes. |
| **NC-098** | P2 | Ownership | Lock acquisition latency under contention | Fails fast or obeys explicit timeout; does not hang indefinitely. |

## A5. Real Task Ledger adapter, Git, checks, and integration

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-099** | P0 | TaskLedgerAdapter | Map ACTIVE assignment + ASSIGNED task to ExecutionStatus.ACTIVE | Projection is exact and includes current profile/correction metadata. |
| **NC-100** | P0 | TaskLedgerAdapter | Map pending submission + SUBMITTED task | Projection exposes current immutable submission ID. |
| **NC-101** | P0 | TaskLedgerAdapter | Map blocked submission/question | Projection pauses with correct detail without inventing state. |
| **NC-102** | P0 | TaskLedgerAdapter | Map revoked assignment | Projection returns REVOKED/escalation as applicable. |
| **NC-103** | P0 | TaskLedgerAdapter | Map accepted submission with failed integration | Projection returns ACCEPTED/failed detail, never COMPLETED. |
| **NC-104** | P0 | TaskLedgerAdapter | Map uncertain integration operation | Projection returns INTEGRATION_UNCERTAIN. |
| **NC-105** | P0 | TaskLedgerAdapter | Map completed integrated task | Projection returns COMPLETED only when current integration is reachable from canonical branch. |
| **NC-106** | P0 | TaskLedgerAdapter | Progress fingerprint changes on uncommitted tracked-file edit | Fingerprint changes. |
| **NC-107** | P0 | TaskLedgerAdapter | Progress fingerprint changes on staged edit | Fingerprint changes. |
| **NC-108** | P0 | TaskLedgerAdapter | Progress fingerprint changes on untracked file content | Fingerprint changes. |
| **NC-109** | P1 | TaskLedgerAdapter | Progress fingerprint unchanged for pure model prose/no filesystem/ledger change | Fingerprint remains identical. |
| **NC-110** | P1 | TaskLedgerAdapter | Progress fingerprint changes on submission/checkpoint/question state | Fingerprint changes. |
| **NC-111** | P0 | TaskLedgerAdapter | Worker prompt/context uses assignment-scoped persisted context | No primary-orchestrator chat reconstruction required. |
| **NC-112** | P0 | TaskLedgerAdapter | Worker cwd is exact assignment worktree | Runtime cannot accidentally edit canonical checkout. |
| **NC-113** | P0 | TaskLedgerAdapter | Reviewer cwd points at frozen submitted worktree/immutable view | Review targets exact submission. |
| **NC-114** | P0 | TaskLedgerAdapter | prepare_review executes/collects all exact required reviewer checks | Every required command gets current reviewer receipt at submitted OID. |
| **NC-115** | P0 | TaskLedgerAdapter | One required reviewer check fails | acceptance_allowed=false; failed command/evidence preserved. |
| **NC-116** | P0 | TaskLedgerAdapter | Worker receipt exists but reviewer receipt missing | Acceptance remains forbidden. |
| **NC-117** | P0 | TaskLedgerAdapter | Reviewer check mutates worktree | Receipt becomes stale/invalid or review is stopped; acceptance forbidden. |
| **NC-118** | P0 | TaskLedgerAdapter | Submitted worktree HEAD no longer equals submitted OID | Review/acceptance blocked. |
| **NC-119** | P0 | TaskLedgerAdapter | Submitted worktree dirty before reviewer checks | Review/acceptance blocked. |
| **NC-120** | P0 | TaskLedgerAdapter | Specification changed after assignment creation | Existing Task Ledger spec-review/plan gates prevent stale acceptance. |
| **NC-121** | P0 | TaskLedgerAdapter | Task revision changed after assignment | Old assignment/submission cannot be accepted for new revision. |
| **NC-122** | P0 | TaskLedgerAdapter | Acceptance uses Service.verify_submission rather than raw SQL | Existing validation and integration path executes. |
| **NC-123** | P0 | TaskLedgerAdapter | Integration merge conflict safely aborts | Task remains ACCEPTED and blocker created; supervisor reports integration failure. |
| **NC-124** | P0 | TaskLedgerAdapter | Integration mutation outcome cannot be proven | Existing operation marked UNCERTAIN/blocker; supervisor pauses. |
| **NC-125** | P0 | TaskLedgerAdapter | Recorded successful integration later becomes unreachable | Task Ledger reconciliation invalidates completion; supervisor no longer treats task complete. |
| **NC-126** | P1 | TaskLedgerAdapter | Idempotent duplicate worker submission payload/head | Existing submission returned; no duplicate review identity created. |

## A6. Blockers, escalation, requirements, and completion

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-127** | P0 | TaskLedgerPolicy | Blocking worker question pauses current assignment | No model turn or review starts until answer resolves blocker. |
| **NC-128** | P1 | TaskLedgerPolicy | Non-blocking worker question exists | Policy-defined behavior is deterministic; it does not accidentally stop forever or ignore required answer semantics. |
| **NC-129** | P0 | TaskLedgerPolicy | Answered blocking question | Supervisor can resume same valid worker session with refreshed context. |
| **NC-130** | P0 | TaskLedgerPolicy | Open project-level blocker | No ready assignment launches. |
| **NC-131** | P0 | TaskLedgerPolicy | Open submission blocker | Acceptance forbidden. |
| **NC-132** | P0 | TaskLedgerPolicy | Routine first rejection | Same assignment remains active and same worker session receives correction. |
| **NC-133** | P0 | TaskLedgerPolicy | Routine second rejection | Assignment revoked; next assignment must be complex; old session never resumed. |
| **NC-134** | P0 | TaskLedgerPolicy | Complex worker rejection | Follow current policy without accidental routine downgrade. |
| **NC-135** | P1 | TaskLedgerPolicy | Assignment token rotated on worker replacement | Old credential fails; new credential works. |
| **NC-136** | P0 | TaskLedgerPolicy | Dependency task not completed/integrated | Dependent task is not eligible for scheduling. |
| **NC-137** | P0 | TaskLedgerPolicy | All dependencies completed/integrated | Task becomes schedulable subject to blockers/wave capacity. |
| **NC-138** | P0 | TaskLedgerPolicy | Task cancelled while queued | Never dispatched. |
| **NC-139** | P0 | TaskLedgerPolicy | Task cancelled while worker turn running | After turn terminates/reconciles, no additional work/review/integration is accepted under cancelled task. |
| **NC-140** | P1 | TaskLedgerPolicy | Follow-up proposal from worker | Recorded for orchestrator/planner handling; does not silently expand current task scope. |
| **NC-141** | P0 | TaskLedgerPolicy | All tasks completed but a required requirement unverified | Project cannot complete. |
| **NC-142** | P0 | TaskLedgerPolicy | All requirements verified/current, no blockers/operations | Project completion succeeds. |
| **NC-143** | P0 | TaskLedgerPolicy | Requirement verification invalidated after new integration/spec review | Project completion becomes invalid/current state ACTIVE. |
| **NC-144** | P1 | TaskLedgerPolicy | New approved specification after completed project | Completion invalidates appropriately while historical completed work remains. |
| **NC-145** | P1 | TaskLedgerPolicy | Required evidence artifact missing/unreadable | Verification fails closed. |
| **NC-146** | P1 | TaskLedgerPolicy | Evidence path attempts traversal/symlink outside allowed root | Existing artifact safety rejects it. |
| **NC-147** | P2 | TaskLedgerPolicy | Cleanup after successful project completion | Only safety-checked clean finalized worktrees removed; branches/commits retained. |
| **NC-148** | P2 | TaskLedgerPolicy | Cleanup sees dirty/locked/mismatched worktree | Skips and reports it rather than force-deleting. |

## A7. Future scheduling, pools, waves, and parallel safety

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-149** | P0 | Scheduler | Configured max_workers=2 with three ready tasks | At most two worker turns execute concurrently. |
| **NC-150** | P0 | Scheduler | Configured max_reviewers=1 with multiple pending submissions | Exactly one reviewer executes at a time; queue preserves all submissions. |
| **NC-151** | P0 | Scheduler | Worker slot becomes free | Next eligible task may use slot regardless of routine/complex profile; slot is capacity, not permanent model identity. |
| **NC-152** | P0 | Scheduler | Two ready tasks share declared write surface | Scheduler does not run them concurrently. |
| **NC-153** | P0 | Scheduler | Two ready tasks have independent write sets and same approved wave | Scheduler may run both within capacity. |
| **NC-154** | P0 | Scheduler | Task in later approved wave becomes otherwise ready early | It does not bypass wave barrier. |
| **NC-155** | P1 | Scheduler | One task in wave fails/rejects while sibling completes | Later wave remains blocked until wave policy satisfied; sibling result preserved. |
| **NC-156** | P1 | Scheduler | One task blocked on human input | Other independent tasks in same approved concurrency set may continue if policy allows. |
| **NC-157** | P0 | Scheduler | Dependency edge crosses waves | Dependency dominates; dependent task never starts early. |
| **NC-158** | P0 | Scheduler | Cycle appears due to invalid plan change | Plan validation prevents scheduler execution. |
| **NC-159** | P1 | Scheduler | Routine and complex tasks compete for two generic slots | Both may run; each resolves its own configured model/effort correctly. |
| **NC-160** | P1 | Scheduler | Reviewer queue grows while worker slots remain active | Reviewer capacity remains independent; no starvation/deadlock. |
| **NC-161** | P1 | Scheduler | Worker queue grows while reviewer is busy | Workers obey capacity and wave/dependency constraints; no unbounded thread creation. |
| **NC-162** | P1 | Scheduler | Controller restart with two active worker turns | Each session is reconciled independently; no duplicate assignments. |
| **NC-163** | P1 | Scheduler | Controller restart with one reviewer and workers active | All external operations reconcile by IDs; capacity accounting remains correct. |
| **NC-164** | P1 | Scheduler | One runtime call hangs beyond configured execution timeout | Only affected slot is paused/interrupted per policy; scheduler does not lose other session state. |
| **NC-165** | P2 | Scheduler | Hundreds of queued tasks | Scheduler query remains bounded and deterministic; no O(N) prompt/state replay. |
| **NC-166** | P2 | Scheduler | Fairness under repeated rejections | A single noisy task does not permanently starve other eligible work unless dependency/wave policy requires it. |
| **NC-167** | P1 | Scheduler | Task creator declares concurrency > runtime max | Runtime max is enforced; semantic safety set is upper-bounded by capacity. |
| **NC-168** | P0 | Scheduler | Task creator declares task not parallel-safe | Scheduler never overlaps it even if slots are free. |
| **NC-169** | P0 | Scheduler | Reviewer for submission starts before worker turn has definitively ended | Forbidden; test must fail if review overlaps mutable worker execution. |

## A8. Budgets, accounting, security, configuration, and scale

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **NC-170** | P0 | Budgets | Input/output/reasoning/cached usage from completed fake turns | Stored exactly once per turn. |
| **NC-171** | P0 | Budgets | Runtime returns cumulative usage snapshot twice | Adapter converts/deduplicates correctly; journal does not sum cumulative totals twice. |
| **NC-172** | P0 | Budgets | Runtime emits duplicate usage notification | Deduplicated by turn/event identity. |
| **NC-173** | P1 | Budgets | Usage missing for a completed turn | Recorded as unknown/missing according to policy; never silently treated as free for an unlimited budget. |
| **NC-174** | P1 | Budgets | Token budget reached before new turn | No new turn admitted; pause with budget reason. |
| **NC-175** | P1 | Budgets | One in-flight turn overshoots budget | Turn result is recorded; next turn is refused. Overshoot is visible. |
| **NC-176** | P1 | Budgets | Budget extension after human approval | Persisted extension permits further turns without erasing prior usage. |
| **NC-177** | P1 | Budgets | Reviewer reserve configured | Worker cannot consume tokens reserved for mandatory review. |
| **NC-178** | P2 | Budgets | Credit-equivalent estimator rates change | Historical raw token counts remain immutable; estimates can be recomputed by rate version. |
| **NC-179** | P2 | Budgets | Cached input ratio calculation | 0 <= cached_input <= input and derived ratio handles input=0 safely. |
| **NC-180** | P0 | Security | Prompt construction accidentally includes orchestrator token/path content | Test rejects/leak scanner fails. |
| **NC-181** | P0 | Security | Reviewer prompt includes worker credential | Test rejects. |
| **NC-182** | P0 | Security | Worker prompt includes another assignment credential/context | Test rejects cross-assignment leakage. |
| **NC-183** | P1 | Security | Malicious worker submission text contains shell command | Supervisor never executes prose; only predeclared required checks run. |
| **NC-184** | P1 | Security | Malicious reviewer correction contains ledger command | Stored as correction text only; no controller code execution. |
| **NC-185** | P1 | Config | Unknown worker profile/model mapping | Fail preflight before assignment dispatch. |
| **NC-186** | P1 | Config | Missing reviewer configuration | Fail before implementation if independent review cannot be guaranteed. |
| **NC-187** | P2 | Performance | 10,000 journaled historical turns | Startup/reconcile active state remains within defined latency budget. |
| **NC-188** | P2 | Performance | Large Task Ledger project with many completed tasks | Supervisor state projection sends only bounded current assignment/review context, not full history. |
| **NC-189** | P2 | Property/Fuzz | Random valid event sequences across ACTIVE/SUBMITTED/ACCEPTED/COMPLETED | Invariant checker never observes illegal completion/duplicate active turn. |
| **NC-190** | P2 | Property/Fuzz | Random crash injection at every journal/runtime/ledger boundary | Recovery either proves a unique next action or pauses uncertain; never produces false success. |

## No-Codex destructive/fault-injection matrix

For the crash-sensitive P0 cases above, do not rely only on mocked exceptions. Run a process-level matrix that kills the controller after each durable boundary below, then starts a new process and asks it to reconcile:

1. before controller run row creation;
2. after run row creation;
3. before runtime session/thread request;
4. after runtime session/thread creation but before session ID persistence;
5. after session persistence;
6. after local turn `DISPATCHING` row;
7. after external turn start but before external turn ID persistence;
8. after external turn ID persistence / `RUNNING`;
9. while external turn is logically running;
10. after external completion but before local completion persistence;
11. after result/usage persistence but before turn consumption;
12. after worker side effects but before progress fingerprint consumption;
13. after submission creation but before worker turn consumption;
14. before reviewer checks;
15. after each reviewer check receipt;
16. after reviewer result persistence but before Task Ledger verification;
17. after Task Ledger verification commits but before controller consumption;
18. after semantic acceptance but before Git integration starts;
19. during Git integration operation;
20. after Git integration succeeds but before controller sees return;
21. after requirement verification but before project completion;
22. during cleanup.

Each restart must satisfy one of only two acceptable outcomes: **(a) prove the unique safe continuation**, or **(b) pause with explicit uncertainty/action required**. A restart must never manufacture success or dispatch duplicate potentially-live external work.

## No-Codex property/invariant testing

In addition to named cases, add a model/state-machine property suite that generates randomized sequences of:

- worker progress/no-progress/completion/failure/uncertainty;
- submissions, resubmissions, questions, blockers, revisions, cancellations;
- reviewer valid/invalid/rejected/accepted/blocked results;
- check success/failure/staleness;
- controller crashes/restarts;
- integration success/failure/uncertainty/invalidation.

After every generated step assert INV-01 through INV-25 where applicable. Especially assert: no task becomes completed without current accepted evidence + reachable integration; no session has two unresolved turns; no stale reviewer verdict changes authoritative state; and no restart resets safety budgets.

---

# Category B — Tests With Codex

These tests validate the runtime boundary that fakes cannot prove. They should be intentionally few in routine CI because they consume model usage, but the inventory is broad so every supported live behavior has an explicit acceptance case.

Current Codex Python SDK behavior assumed by this plan must itself be verified during E3: threads are persistent conversation state, turns are individual model executions, threads can be resumed by ID, `AsyncCodex` supports concurrent active turns, turns accept `output_schema`, sandbox can be selected per thread/turn, and turn results expose usage telemetry including cached input. Treat these as external contracts, not internal guarantees.

## B1. SDK, authentication, version, and model preflight

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-001** | P0 | SDK/Auth | Instantiate supported Codex SDK/runtime with existing ChatGPT/Codex login | Client initializes and account is usable without interactive surprise during an unattended turn. |
| **CX-002** | P1 | SDK/Auth | No existing authentication | Supervisor fails preflight or enters explicit authentication-needed state before dispatch. |
| **CX-003** | P1 | SDK/Auth | Expired/revoked auth between turns | Current turn/session error is classified; no duplicate dispatch or false task failure. |
| **CX-004** | P1 | SDK/Auth | SDK runtime version incompatible with required feature set | Preflight rejects before model usage. |
| **CX-005** | P1 | SDK/Auth | Configured model no longer available | Preflight/model-list validation fails with clear profile mapping error. |
| **CX-006** | P1 | SDK/Auth | Configured reasoning effort unsupported by selected model | Fail preflight or surface deterministic runtime error; no silent substitution. |
| **CX-007** | P2 | SDK/Auth | Codex runtime executable missing/corrupted | Supervisor fails before ledger assignment mutation or model turn. |
| **CX-008** | P2 | SDK/Auth | SDK/client closes gracefully | No orphan controller ownership; active external turn state remains recoverable. |

## B2. Thread/session lifecycle and bounded context

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-009** | P0 | Threads | Start worker thread with expected cwd/model/effort/sandbox | Returned thread ID is persisted and usable. |
| **CX-010** | P0 | Threads | Run second turn on same worker thread after ordinary early stop | Same thread retains assignment-local conversation and accepts continuation. |
| **CX-011** | P0 | Threads | Resume persisted worker thread from a brand-new supervisor process | thread_resume succeeds and a subsequent turn continues the same conversation. |
| **CX-012** | P0 | Threads | Read/resume thread after Codex client process restarts | Persisted ID remains addressable; no replacement thread required. |
| **CX-013** | P1 | Threads | Resume nonexistent/archived/deleted thread ID | Classify as unrecoverable/repairable according to API; never silently create a new implementation thread. |
| **CX-014** | P1 | Threads | Thread exists but current cwd differs from stored assignment worktree | Supervisor explicitly supplies/validates assignment cwd before continuing. |
| **CX-015** | P1 | Threads | Thread model/profile differs from persisted session metadata | Pause/config error; do not silently change semantic strength. |
| **CX-016** | P1 | Threads | Worker thread has many prior turns within same assignment | No unrelated task context appears; continuation remains bounded to assignment. |
| **CX-017** | P1 | Threads | New unrelated assignment starts | New worker thread is created; prior assignment transcript is not inherited. |
| **CX-018** | P1 | Threads | Rejected submission correction | Same assignment worker thread is reused unless escalation replaced assignment. |
| **CX-019** | P0 | Threads | New immutable resubmission review | Fresh reviewer thread is created for that submission. |
| **CX-020** | P2 | Threads | thread.read(include_turns=False) | Verify response-history omission does not imply model-context reset; controller does not misuse it as compaction. |
| **CX-021** | P2 | Threads | thread.compact if ever enabled | Benchmark correctness and cache/context effects; disabled unless explicit policy proves it safe. |
| **CX-022** | P2 | Threads | thread_fork experiment | Verify whether it preserves desired context and measure cost; do not adopt based only on functional success. |

## B3. Real worker early-stop/continuation behavior

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-023** | P0 | Worker Behavior | Terra performs useful partial work then returns normally without submission | Supervisor automatically issues continuation; human intervention not required. |
| **CX-024** | P0 | Worker Behavior | Terra stops twice with progress before submitting | Both early stops are tolerated within budgets; same thread continues. |
| **CX-025** | P0 | Worker Behavior | Terra stops with no filesystem/ledger progress | Stall guard increments; repeated no-progress turns pause. |
| **CX-026** | P1 | Worker Behavior | Terra final response claims complete but no Task Ledger submission exists | Supervisor ignores prose and continues/pauses based on durable state. |
| **CX-027** | P1 | Worker Behavior | Terra says it is continuing but turn actually ends | Supervisor sees completed turn and continues automatically. |
| **CX-028** | P0 | Worker Behavior | Worker submits through assignment-scoped Task Ledger command | Submission is recorded and review starts only after turn ends. |
| **CX-029** | P0 | Worker Behavior | Worker asks a blocking question through Task Ledger | Supervisor pauses; no repetitive model prompting. |
| **CX-030** | P1 | Worker Behavior | Worker attempts to broaden task scope | Task Ledger context/instructions constrain scope; reviewer catches unauthorized changes if they occur. |
| **CX-031** | P1 | Worker Behavior | Worker receives correction packet after rejection | Correction is understood/applied in same thread; old correction is not replayed after new submission. |
| **CX-032** | P1 | Worker Behavior | Routine worker reaches second rejection | Real runtime thread is not resumed after Task Ledger revocation; complex assignment uses configured stronger profile. |
| **CX-033** | P1 | Worker Behavior | Worker returns empty final_response but made progress | Progress is detected from filesystem/ledger, not response text; continuation proceeds. |
| **CX-034** | P1 | Worker Behavior | Worker returns empty final_response and no progress | Counts toward stall. |

## B4. Independent reviewer and structured verdict behavior

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-035** | P0 | Reviewer | Reviewer turn uses output_schema and returns valid exact verdict | SDK returns structured result accepted by parser; Task Ledger applies once. |
| **CX-036** | P0 | Reviewer | Reviewer omits criterion despite output_schema | Parser catches semantic coverage gap even if schema accepts structure. |
| **CX-037** | P0 | Reviewer | Reviewer tries to ACCEPT when provided failed required-check evidence | Structured result is rejected/procedurally barred. |
| **CX-038** | P1 | Reviewer | Reviewer returns no final response/structured output | Bounded continuation/retry occurs; no inferred acceptance. |
| **CX-039** | P1 | Reviewer | Reviewer gives prose plus malformed structured result | Only structured contract is authoritative. |
| **CX-040** | P0 | Reviewer | Reviewer inspects exact submitted diff/OID | Findings correspond to immutable submission, not latest canonical files. |
| **CX-041** | P0 | Reviewer | Reviewer sandbox is read-only | Attempted repository edit is prevented; review still succeeds. |
| **CX-042** | P1 | Reviewer | Reviewer needs deterministic check output | Controller-provided receipts/context are available without giving broad mutation authority. |
| **CX-043** | P1 | Reviewer | Reviewer identifies a real semantic defect | REJECTED result routes actionable correction back to same worker. |
| **CX-044** | P1 | Reviewer | Reviewer identifies genuine blocker | BLOCKED result pauses with durable blocker rather than looping worker. |
| **CX-045** | P1 | Reviewer | Reviewer thread crashes/known fails once | Retry within configured budget. |
| **CX-046** | P0 | Reviewer | Reviewer turn outcome cannot be reconciled | Pause uncertain; do not launch a second reviewer blindly. |
| **CX-047** | P1 | Reviewer | Large diff near context limits | Review either succeeds with bounded context strategy or fails explicitly; no silent partial acceptance. |
| **CX-048** | P1 | Reviewer | Reviewer model/effort routing | High-strength configured reviewer is actually used and recorded. |

## B5. Sandbox, credentials, approvals, and authority boundaries

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-049** | P0 | Sandbox/Security | Worker with workspace_write can edit only intended worktree/writable roots | Expected writes succeed; canonical checkout/ledger secrets remain protected by configured boundaries. |
| **CX-050** | P0 | Sandbox/Security | Reviewer with read_only attempts to edit source | Write denied. |
| **CX-051** | P0 | Sandbox/Security | Worker tries to read orchestrator/controller credential file | Permission/sandbox boundary prevents access; prompt omission is not sole protection. |
| **CX-052** | P0 | Sandbox/Security | Reviewer tries to call worker-only Task Ledger command/token | Authorization fails. |
| **CX-053** | P1 | Sandbox/Security | Worker requires network but task does not authorize it | Request fails/approval surfaces; supervisor does not auto-grant unexpected authority. |
| **CX-054** | P1 | Sandbox/Security | Authorized task genuinely requires network/service | Explicit configuration allows only intended capability and run remains auditable. |
| **CX-055** | P1 | Sandbox/Security | Codex raises tool approval request unexpectedly | Supervisor pauses/fails according to policy rather than hanging or auto-approving everything. |
| **CX-056** | P1 | Sandbox/Security | Model requests user input unexpectedly | Request becomes a durable pause/human action, not an invisible deadlock. |
| **CX-057** | P1 | Sandbox/Security | Prompt injection inside repository/spec/submission text | Model may reason about it, but controller authority boundaries prevent direct controller/ledger privilege escalation. |
| **CX-058** | P2 | Sandbox/Security | Worker attempts destructive command outside workspace | Sandbox/approval policy blocks it. |
| **CX-059** | P2 | Sandbox/Security | Reviewer attempts network exfiltration | Read-only/network policy blocks or records according to configuration. |

## B6. Live crash/reconnect/reconciliation behavior

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-060** | P0 | Live Recovery | Kill supervisor after thread creation and persisted thread ID, before first turn | Restart reuses session and safely dispatches first turn. |
| **CX-061** | P0 | Live Recovery | Kill supervisor immediately after turn start acknowledgement | Restart inspects/resumes exact active turn rather than starting another. |
| **CX-062** | P0 | Live Recovery | Kill supervisor while worker turn is running | After restart, exactly one external turn is associated with assignment; result reconciles. |
| **CX-063** | P0 | Live Recovery | Kill supervisor after turn completed but before local result consumption | Restart retrieves/persists/consumes result without another paid turn. |
| **CX-064** | P0 | Live Recovery | Kill supervisor after reviewer result arrives but before Task Ledger apply | Persisted/recovered verdict is applied once without new review. |
| **CX-065** | P0 | Live Recovery | Kill supervisor after Task Ledger applies rejection but before local consume | Restart detects authoritative rejected state; does not apply again or re-review. |
| **CX-066** | P0 | Live Recovery | Kill supervisor during accepted integration | Existing Task Ledger operation recovery governs outcome. |
| **CX-067** | P1 | Live Recovery | Codex client connection drops while turn continues server-side | Reconnection/inspection behavior classified and no duplicate turn is launched. |
| **CX-068** | P1 | Live Recovery | Network/process error definitely aborts turn | Known-failed retry path works and is bounded. |
| **CX-069** | P1 | Live Recovery | Runtime cannot determine whether turn exists | Supervisor pauses uncertain and exposes identifiers for manual recovery. |
| **CX-070** | P1 | Live Recovery | Machine sleep during long turn | Controller ownership remains exclusive; turn resumes/reconciles after wake. |
| **CX-071** | P2 | Live Recovery | Codex runtime itself restarts during active thread | Thread persistence/recovery behavior is empirically documented; unsupported behavior becomes explicit limitation. |

## B7. Async concurrency and event routing

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-072** | P0 | Concurrency | Two worker turns run concurrently through one AsyncCodex client | Events/results route to correct thread/turn IDs; no cross-contamination. |
| **CX-073** | P0 | Concurrency | One reviewer runs while two independent workers run | Three active operations remain correctly routed within configured capacity. |
| **CX-074** | P1 | Concurrency | Worker A finishes while Worker B remains running | Only A slot frees; B remains attached to its turn. |
| **CX-075** | P1 | Concurrency | Reviewer finishes while workers run | Verdict applies to correct submission; worker state unaffected. |
| **CX-076** | P1 | Concurrency | One concurrent turn fails | Other active turns continue/reconcile normally. |
| **CX-077** | P1 | Concurrency | One concurrent turn becomes uncertain | Only affected assignment/submission pauses unless global safety invariant requires full stop. |
| **CX-078** | P1 | Concurrency | Backpressure with >2 ready workers | No more than configured max active worker turns. |
| **CX-079** | P1 | Concurrency | Backpressure with >1 pending review | No more than configured reviewer max. |
| **CX-080** | P2 | Concurrency | Rapid completion events arrive out of order | ID-based routing preserves correct ownership. |
| **CX-081** | P2 | Concurrency | Controller shutdown with multiple active turns | All journal identities remain recoverable; shutdown does not cross-wire outcomes. |

## B8. Usage accounting, caching, rollover, and admission budgets

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-082** | P0 | Usage/Cache | TurnResult usage includes input/output/reasoning/cached fields | Adapter records values with documented semantics exactly once. |
| **CX-083** | P0 | Usage/Cache | Usage notification total/last values across multiple turns | Accounting chooses correct delta/source and never sums cumulative total repeatedly. |
| **CX-084** | P1 | Usage/Cache | Same assignment continuation on same thread | Measure cache hit ratio and weighted usage relative to first turn. |
| **CX-085** | P1 | Usage/Cache | Fresh thread for unrelated task with same static role instructions | Measure prompt-prefix cache hit ratio; confirm thread reuse is not assumed necessary for caching. |
| **CX-086** | P1 | Usage/Cache | Persistent thread reused across unrelated tasks (experimental) | Measure cost/context contamination against fresh-task strategy; reject if cumulative context dominates savings. |
| **CX-087** | P1 | Usage/Cache | Forked seed thread strategy (experimental) | Measure cached input, total weighted usage, correctness, and context isolation. |
| **CX-088** | P1 | Usage/Cache | Periodic thread rollover strategy | Find crossover point where fresh/rolled thread beats long-lived thread. |
| **CX-089** | P1 | Usage/Cache | Cache expires/does not hit | System correctness unchanged; only cost telemetry changes. |
| **CX-090** | P1 | Usage/Cache | Token admission budget before live turn | Supervisor refuses turn when remaining budget insufficient under configured rule. |
| **CX-091** | P1 | Usage/Cache | Actual turn exceeds estimate/budget | Overshoot recorded; no subsequent turn admitted without extension. |
| **CX-092** | P2 | Usage/Cache | Repeated identical continuation prompt | Verify no pathological token growth from unnecessary context replay. |
| **CX-093** | P2 | Usage/Cache | Different models/efforts by profile | Usage attributed to actual resolved model/effort, not configured label only. |

## B9. End-to-end acceptance, benchmarks, and upgrade compatibility

| ID | Pri | Layer | Test case | Required result |
|---|---|---|---|---|
| **CX-094** | P0 | End-to-End | One routine task: early stop -> continue -> submit -> accept -> integrate | Completes without human continuation and with exact audit trail. |
| **CX-095** | P0 | End-to-End | One routine task: submit -> reject -> same-thread correction -> accept -> integrate | Completes with exactly two immutable reviews and no duplicate turns. |
| **CX-096** | P0 | End-to-End | Routine task rejected twice -> complex replacement -> accept | Escalation is deterministic; old routine thread never resumes. |
| **CX-097** | P0 | End-to-End | Worker blocks on product decision | Run pauses quickly with actionable state and zero repeated prompts while blocked. |
| **CX-098** | P0 | End-to-End | Integration conflict after accepted review | Run pauses with Task Ledger blocker; never reports project/task completion. |
| **CX-099** | P1 | End-to-End | Two independent tasks in same approved wave + one reviewer slot | Workers overlap; reviews serialize; both integrate correctly. |
| **CX-100** | P1 | End-to-End | Dependent task after prerequisite | Second task starts only after prerequisite integration. |
| **CX-101** | P1 | End-to-End | Controller crash/restart mid-project | No duplicate task work; project continues from durable state. |
| **CX-102** | P1 | End-to-End | Specification changes mid-run | Execution pauses for existing spec review/replan gates. |
| **CX-103** | P0 | End-to-End | Small complete project including requirement verification and project complete | Completion only occurs after all requirement-level gates succeed. |
| **CX-104** | P0 | Benchmark | Equipment-lending benchmark using supervisor architecture | Record final reviewed correctness, wall time, raw token mix, credit-equivalent estimate, turns, reviews, corrections, human interventions; compare with historical runs. |
| **CX-105** | P1 | Benchmark | Repeat same benchmark at least 3 times | Report distribution/variance; do not claim savings from one lucky run. |
| **CX-106** | P1 | Benchmark | Compare fresh-per-assignment vs long-lived-pool thread strategies | Use same tasks/model/effort where possible; select strategy by completed-work cost and correctness, not cache ratio alone. |
| **CX-107** | P1 | Benchmark | Compare max_workers=1 vs max_workers=2 | Measure wall-clock improvement, usage change, conflicts/rework, and reviewer bottleneck. |
| **CX-108** | P1 | Benchmark | Quality regression comparison against current Task Ledger primary-orchestrator run | Supervisor version must preserve or improve acceptance/architecture correctness at materially lower cost. |
| **CX-109** | P2 | Upgrade | Upgrade Codex SDK/runtime one compatible version | Run compatibility smoke suite before enabling production; state/journal remains readable. |
| **CX-110** | P2 | Upgrade | SDK changes TurnResult/usage/output-schema behavior | Contract tests fail visibly; no silent accounting/review degradation. |
| **CX-111** | P2 | Upgrade | Configured model aliases/version change | Resolved model is recorded and benchmark baselines remain attributable. |

## Live-Codex cost-control protocol

Live tests exist to verify external contracts, not to rediscover deterministic bugs expensively. Apply these rules:

1. **No E3/E4 test until its corresponding E0/E1/E2 logic is green.**
2. Use tiny disposable tasks for SDK/thread/sandbox/recovery tests.
3. Before every paid test, record the expected maximum number of worker and reviewer turns.
4. Refuse admission when the test would exceed its explicit live-turn budget.
5. Persist raw usage fields per turn before computing any credit-equivalent estimate.
6. Never use account-wide quota change as the authoritative per-test usage measurement while other sessions are active.
7. Repeat cost benchmarks enough times to measure variance; do not optimize around one run.
8. Evaluate **completed reviewed work**, not implementation-only cost.

## Cache strategy experiment

The persistent-thread question should be decided by data. Use one fixed workload and compare at least these strategies:

- **S1 — fresh thread per assignment** (same static role prefix);
- **S2 — same thread for the entire assignment + corrections** (current default candidate);
- **S3 — persistent role worker across multiple unrelated assignments**;
- **S4 — periodic rollover after N assignments/tokens**;
- **S5 — forked seed thread**, only if the SDK behavior remains supported and useful.

For each strategy collect:

- completed/accepted result quality;
- input tokens;
- cached input tokens;
- output tokens;
- reasoning tokens;
- weighted/credit-equivalent estimate using a versioned rate table;
- wall-clock duration;
- number of model turns;
- reviewer rounds;
- corrections/rework;
- human interventions;
- evidence of cross-task context contamination.

Do **not** select the strategy with the highest cache-hit ratio. Select the lowest-cost strategy that preserves correctness and bounded context. A large cached prefix can still be more expensive than a small fresh prompt.

## Benchmark acceptance criteria

The supervisor redesign should not be considered successful merely because it runs. For the representative benchmark:

- all intended requirements must reach current verified completion;
- independent review must remain intact;
- no human should need to repeatedly type “continue” for ordinary early model stops;
- no false completion or duplicate work may occur;
- crashes/restarts must recover or stop safely;
- usage must be attributable by planner/worker/reviewer/task/turn;
- wall time and usage must be compared to historical direct and Task Ledger runs;
- quality findings/corrections must be included in the comparison.

A reasonable initial economic target is **materially below the historical long-running-orchestrator Task Ledger run while retaining Task Ledger assurance**. Do not encode a hard multiplier until completed-work benchmarks are comparable.

---

# Stage gates

## Gate 0 — Pure supervisor

Required: all no-Codex P0 cases that use E0 pass. No real Task Ledger adapter and no Codex yet.

## Gate 1 — Real Task Ledger, fake runtime

Required: all P0 E1/E2 cases pass against disposable real repositories. This is the main proof that the controller does not bypass current Task Ledger semantics.

## Gate 2 — Minimal Codex contract smoke

Required live P0s: auth/preflight, thread start/resume, same-thread continuation, output-schema reviewer result, read-only reviewer, assignment-scoped worker behavior, usage capture, and at least one controlled crash/restart reconciliation.

## Gate 3 — Single-assignment live lifecycle

Required: real early stop -> automatic continuation -> submission -> review -> correction if needed -> acceptance -> integration. No human “continue” messages.

## Gate 4 — Parallel scheduler

Only after single-assignment crash behavior is proven. Enable max_workers=2 / max_reviewers=1 and pass all scheduler/concurrency P0 cases.

## Gate 5 — Representative project benchmark

Run complete representative projects, including requirement verification and final completion. Compare cost, time, correctness, retries, and interventions to historical runs.

## Gate 6 — Routine use

All P0 and P1 cases applicable to shipped features pass. Remaining P2 failures are documented limitations with no path to false completion, duplicate uncertain work, credential leakage, or unbounded usage.

# Regression policy

Any production incident, unexpected model stop behavior, duplicate dispatch, stale verdict, bad integration, token-accounting anomaly, or manual intervention that was not represented by an existing test must produce a new deterministic reproduction if technically possible. The new test should fail before the fix and remain permanently in the suite.

# Current prototype coverage

The v2 prototype's existing 19 tests cover only a small subset of this plan—primarily NC supervisor/reviewer/journal cases using E0. Passing them proves the controller idea is testable; it does **not** prove the real Task Ledger adapter or Codex boundary.

The next implementation target remains: **real Task Ledger adapter + fake runtime**, with the no-Codex P0 cases above acting as the acceptance backlog.
