# Taskledger executable controller: discovery handoff

## Read this first

**This is discovery, not an implementation assignment. Do not change code or launch model work unless the user subsequently authorizes it.** The immediate request was to write this handoff in the Taskledger repository so another model can continue the investigation without reconstructing a long conversation.

The user explicitly corrected the previous assistant: “Don’t make code changes yet, this is discovery.” That instruction supersedes earlier wording that the assistant interpreted as permission to implement.

Use the **current working tree, including all uncommitted changes, as the newest Taskledger version**. Do not reset, stash, replace it with HEAD, or discard existing edits. The user specifically clarified this point. No commit is requested by this handoff.

The question we are exploring is:

> Can an executable process take over routine builder/reviewer dispatch, persistence, and waiting, while Taskledger remains the durable authority and stronger models are used for substantive review rather than repeatedly deciding what to do next?

We are trying to reduce both model overhead and human supervision. We are not trying to remove independent review or declare architectural correctness from a passing script.

## Why this work exists

The user has spent substantial time defining architectural decisions for a Rust application playbook. Those decisions are intentional. Skills are portable, model-facing instructions derived from the decisions; the two must remain separately maintained. Only the user may change the architectural decisions. Model adherence, rather than wholesale architectural redesign, has been the recurring problem in these trials.

Several iterations tested Terra implementations, Sol implementations, stronger-model reviews, mechanical architecture checks, and Taskledger orchestration. Direct implementations could produce significant working code but still leave architectural seams wrong. Compiler and PostgreSQL checks were useful, but they did not establish complete adherence to the documented decisions.

The user then tried a two-thread workflow: Terra implements, Astra/Sol reviews, corrections go back to the same worker, and the reviewer checks the corrections. That workflow is attractive on usage, but the user had to keep managing the process. In the current Financial Tracker trial, the worker repeatedly ended turns while explicitly acknowledging unfinished authorized work. The reviewer gradually became a manual dispatcher, sending continuation instructions and choosing smaller milestones.

The user considered returning to Taskledger because structured completion was worth something despite its cost. However, a historical Taskledger run took about four hours and consumed substantially more estimated usage. The user asked for alternatives that preserve reliable orchestration without keeping an expensive model active as the orchestration layer.

The user's wording was: “It would be really nice to have a live code binary be the orchestration layer here, rather than a consistent model of high strength.” Taskledger is already a Python executable. A compiled Rust rewrite is not necessary to test that idea: moving control flow out of model reasoning is the relevant change.

## Observed failure: turn completion is not delivery

The current Financial Tracker threads are historical evidence for this investigation:

- Worker: `01a08db3-a224-7dc3-a0b4-1e0ee0f67324`
- Reviewer: `01a08dd8-2a40-7301-88b9-1adf5ebad812`

**Do not resume, message, change models on, or otherwise take over those threads.** The user explicitly asked for inspection without restarting the worker. This discovery and handoff do not authorize a live experiment against them.

The earlier read-only trace analysis found several normal completion events with work remaining:

| Observed turn | What happened |
| --- | --- |
| About 21 minutes | Worker ended after database/client work with substantial migration scope still outstanding. The user's status question arrived after this stop. |
| About 10 minutes | After being asked why it stopped, worker implemented more features, then ended while listing remaining work. |
| About four minutes | A readiness instruction was followed by more work and an empty final message. |
| 51 seconds | A formal correction packet was followed by a check and a final saying it was continuing, with corrections still open. |
| 69 seconds | An explicit instruction not to end with progress-only output was followed by declarations, compilation, and another in-progress final. |

The analyzed trace had normal `task_complete` events at these boundaries, no observed `turn_aborted` event, and no demonstrated usage-limit interruption explaining them. This supports a persistence/turn-finalization diagnosis. It does **not** establish the model's internal reason for stopping, a universal Terra limitation, or a proven platform timeout.

A later milestone-sized turn was allowed to return at a milestone and should not be counted as the same unambiguous premature stop. The unfinished Financial Tracker migration is also not a completed-cost baseline.

The mechanical architecture checker can check code when called. It cannot force the model runtime to start another turn after a model ends. Stronger wording alone did not enforce continuation in this trace.

## Usage findings and their limits

These are historical estimates from the earlier analysis, not new billing measurements made for this handoff. They use a common public standard-rate proxy, including each model's cached-input mix. They are **credit-equivalents, not measured subscription allowance consumption or actual charges**.

| Historical reference | Active duration | Estimated credit-equivalent |
| --- | --- | ---: |
| Equipment lending, direct Terra run 4 implementation | 26.5 minutes | 77.19 |
| Same implementation plus independent Astra review | About 41.9 minutes combined | 242.43 |
| Equipment lending, Taskledger run 5 | 243.3 minutes | 3042.02 |
| Dive Log, direct Terra/xhigh implementation | 40m31s | 189.33 |
| Dive Log, Sol implementation, mostly medium | 44m31s | 360.90 |

The roughly 44-minute reference was Sol on Dive Log, not a 44-minute Terra equipment-lending run. The closest workload comparison is equipment lending run 4 against run 5. Run 4 plus review still needed corrections: **242.43 is not the cost of an accepted final result**. Dive Log is a different workload and both direct implementations had review findings.

Run 5's estimated split was 2433.45 for the Astra orchestrator and 608.57 across seven Terra workers: about 80% was attributed to the orchestrator. This does not mean all 80% is removable overhead; that model also performed substantive review and coordination. The proposed controller should replace routine dispatch and waiting, not erase the review work from the accounting.

The old run had a very high cache hit rate, but large repeated cached contexts still contribute usage. The earlier calculation attributed about 68.5% of its weighted proxy total to cached input, while estimating about 86% savings relative to charging all that input uncached. Those statements are compatible: caching helped considerably, but a large volume remained a large volume.

The approximately 12.55x run-5/direct-plus-review proxy ratio is not an intrinsic Taskledger multiplier. Completion status, review depth, seven-worker structure, and evolving Taskledger behavior differ. Run 5 was treated as the strongest generated version at the time, but later architectural review still found issues. Ledger completion is not blanket architectural correctness.

Do not promise this design will achieve 242 credits. The benchmark must include corrections, retries, review, final verification, and human interventions. Account-wide quota percentages cannot be attributed to this workload while other sessions are active.

## Source state and what was actually inspected

Target checkout: `/Users/christophercaldwell/Code/projects/task_ledger`.

HEAD at inspection: `6c93944f471d52b04e2a595497d87ae237991179`. The working tree identifies itself as version `0.6.0` and includes substantial newer uncommitted work. Read the working files, not only the committed version.

Taskledger is a Python 3.11+ CLI backed by SQLite and Git operations. The executable entry is `taskledger.cli:main`. The command interface uses resource/action pairs, JSON input, and structured JSON output. `taskledger --help` is not supported by the inspected CLI; use the command registry and source rather than repeatedly trying it.

Relevant source map; line numbers are approximate and can move:

| File / function | Existing responsibility | Implication |
| --- | --- | --- |
| `src/taskledger/service.py`, `assignment_create`, around 867 | Creates isolated worktree, assignment context, branch and worker credential | Reuse an approved assignment; no new worktree lifecycle is needed for a retained worker. |
| `correction_packet`, around 951; `worker_context`, around 1012 | Current worker context and assignment-scoped corrections | Feed the latest packet after rejection instead of inventing a parallel correction format. |
| `submission_review_context`, around 1025 | Submitted commit, revisions, criteria, requirements, claims, receipts and blockers | Suitable starting packet for independent review, but it does not include the actual patch. Reviewer must inspect the diff. |
| `worker_check`, around 149; `reviewer_check`, around 164 | Executes declared checks and records observed receipts | Worker receipts are supporting claims; they do not satisfy independent reviewer execution obligations. |
| `verify_submission`, around 1237 | Validates outcomes, assertions, per-criterion results and required reviewer receipts | Keep verification authoritative here. Do not directly update task/submission SQL from a controller. |
| `integrate_task`, around 1311 | Existing Git integration and recovery protections | Acceptance already invokes integration. Do not add a separate merge path. |
| `requirement_verify`, around 1380; `complete_project`, around 1461 | Requirement coverage and project completion gates | Integrated assignment, completed task, and completed project are distinct. |
| `wait_for_events`, around 1579 | Cursor-based durable audit-event waiting | Useful for ledger changes, but it cannot observe external model turn endings. |
| `src/taskledger/db.py` | Schema, constraints and transactions | Persist dispatch state without building a competing task ledger. |
| `src/taskledger/cli.py` | Authentication and command dispatch | Natural eventual CLI integration point; proposed controller commands do not exist. |
| `scripts/usage_accounting.py` | Session attribution and usage deduplication/reconciliation | Reuse its accounting lessons; cumulative telemetry must not be summed repeatedly. |
| `tests/test_acceptance.py` | Disposable Git/ledger integration fixtures | Useful basis for eventual real-ledger tests with a fake model runtime. |

Other important existing behavior:

- Isolated assignments already survive rejection/correction. Retaining a conversation does not require `lightweight` mode.
- Lightweight assignments require planned intermediate checkpoints. Silently selecting that mode would add obligations to the workflow.
- A routine worker's second rejection requires escalation and revokes the assignment. A controller must respect this, not keep resuming a revoked worker or silently choose another model/profile.
- Check receipts identify source revisions and detect worktree changes during execution. Acceptance requires independent successful evidence for each exact declared command.
- Registered specification changes, invalid plans, blockers and unresolved Git operations have existing gates.
- Current principal roles are `ORCHESTRATOR` and `WORKER`; a semantic reviewer should not receive a blanket orchestrator token merely to express a verdict.

## Proposed smallest useful design — not yet approved

Start with **one approved, already-materialized assignment, one retained worker thread, one separate retained reviewer thread, and a foreground executable controller**. Specify model and effort explicitly. Prefer existing isolated assignment behavior for the first experiment unless intermediate checkpoints are a deliberate requirement.

The division of responsibility would be:

1. The human/initial planning workflow supplies approved scope, requirements, decisions, checks and acceptance criteria.
2. The controller owns dispatch, waiting, budgets, durable run state and existing ledger transitions.
3. The worker implements and uses assignment-scoped worker commands for checks, questions, blockers and submission.
4. The reviewer independently inspects exact submitted code and returns structured semantic findings.
5. The controller validates the verdict's identity and evidence, records it through existing verification, and handles the returned integration state.

The controller makes procedural decisions. Models retain the semantic work: architecture, intent, behavior, and correction content. A green check must not be converted into invented per-criterion semantic evidence.

Exclude autonomous decomposition, general DAG scheduling, parallel worker pools, dashboards, daemon installation, remote hosting, multihost management, generic provider frameworks, and automatic model replacement from the first version. Do not recreate full Taskledger orchestration under another name.

## Candidate flow and unresolved choices

| Event | Proposed behavior |
| --- | --- |
| Worker active | Wait in ordinary code, without a model status call. |
| Normal worker turn ends, no submission or blocker | Resume the same worker with current context and corrections, within explicit limits. |
| Submission appears while worker still active | Wait for the turn to finish before independent inspection; prevent concurrent edits. |
| Valid pending submission | Check exact source/revision, execute independent required checks, then obtain substantive review. |
| Valid rejection | Record existing verification and send the latest correction packet to the retained worker. |
| Valid acceptance | Use existing verify/integrate behavior and inspect the actual integration result. |
| Reviewer ends without a valid structured verdict | Decide a small explicit retry policy; never infer acceptance from prose or silence. |
| Blocking question, approval request, changed decision/specification, revoked assignment, failed/interrupted turn, cancellation or exhausted budget | Pause with a durable actionable reason. Do not turn these into generic automatic continuation. |
| Uncertain dispatch outcome | Reconcile if possible; otherwise pause rather than create a duplicate turn. |

**Failed independent checks need a design decision.** The previous source plan proposed skipping expensive semantic review on a mechanical failure. However, `verify_submission` expects complete per-criterion assertions. We must not fabricate those to obtain a rejection. Options include a narrowly scoped mechanical-failure transition or still invoking the reviewer with failed-check evidence. The latter costs more but reuses the existing truthful verification contract. No option has been approved or implemented.

**Completion scope also needs a decision.** The first controller can stop at confirmed assignment integration and explicitly report that limited outcome. Automatically completing the entire project requires remaining requirement-level semantic verification and existing project completion gates. Do not let a convenient final status obscure this distinction.

## Runtime transport discovery

The installed `codex app-server` exposes local stdio transport. Its CLI help and generated protocol schemas were inspected without launching model turns. A schema bundle was generated at `/tmp/taskledger-controller-protocol`; that is disposable inspection output, not a repository dependency.

The inspected protocol includes:

- `initialize` followed by `initialized`;
- `thread/start`, `thread/resume`, and `thread/read`;
- `turn/start`, `turn/completed`, and `turn/interrupt`;
- per-turn `outputSchema` for structured reviewer output;
- token-usage notifications and turn identifiers;
- server requests for approvals/input that a client must handle rather than hang on.

Official reference consulted during discovery: <https://learn.chatgpt.com/docs/app-server>. Recheck the installed protocol before eventual implementation; this is an experimental interface. Help/schema availability does not establish successful authentication, permissions, event ordering, reconnect behavior, or end-to-end model execution.

Start any later authorized experiment with controller-owned threads in a disposable project. Reusing existing app-visible threads is a separate capability to verify. Do not edit session JSONL files or automate the desktop UI as a substitute for a supported transport.

Choose a single owner for continuation. Native goals/automatic continuation and the controller must not independently dispatch overlapping turns.

## Durability, permissions and budgets to settle before coding

Persist intent before thread creation or turn dispatch. A JSON-RPC request ID is not an exactly-once guarantee. After a lost response or controller crash, reconcile known thread/turn state before retrying. If creation succeeded but its identifier was never recorded, automatic retry may create duplicate workers; an explicit uncertain state is safer.

Record assignment/task revision, submitted commit, thread and turn IDs, action state, consumed limits, event cursors, and verdict provenance. Prevent two controllers from owning the same assignment. Duplicate events must not cause duplicate verification or integration. Decide whether this belongs in additive SQLite tables or a durable separate journal; neither design is adopted yet.

Keep the worker frozen while checking/reviewing a submission. Validate commit, clean worktree, current criteria, assignment state and plan again before applying the verdict. Recheck semantics that are not fully protected by existing service methods; do not assume a review packet remains current indefinitely.

The controller should keep the orchestrator credential out of model prompts. The reviewer should have read-only source access and no broad ledger mutation capability. **Prompt omission is not a security boundary.** A model process running as the same user may be able to read credential files; granting a worker write access to the entire ledger directory can also expose broader authority. Runtime filesystem permissions, ledger access, command availability and credential storage need a concrete design before claiming isolation.

Independent checks may write build/test artifacts or need PostgreSQL/network access. A read-only reviewer is compatible with controller-executed checks, but blanket permissions and automatic approvals should not be inferred. Resolve approvals and genuine product questions through a durable pause, not an unattended “yes.”

Bound total worker/reviewer turns, consecutive incomplete turns, review rounds, elapsed time, and optionally measured tokens. Changed files are activity, not proof of progress; superficial edits must not reset every limit forever. Reserve room for review. Decide how budgets persist across restarts and how a user explicitly extends them.

Token limits based on telemetry are admission limits unless in-flight work is interrupted. They may overshoot by a turn. Specify handling of missing telemetry. Deduplicate per-response/per-turn data and do not add reasoning tokens again when they are already included in output. Record unsuccessful attempts, continuation prompts and reviews as well as successful work. Weighted usage estimates require explicit rates and remain estimates.

## Recommended continuation of discovery

The next model should first read this file, the relevant current source, and the original source plan listed below. Do not reopen the entire architectural decision process. Focus on the small number of controller decisions that affect feasibility:

1. Confirm the first supported unit: one existing isolated assignment, with assignment integration as the explicit stopping boundary, or a broader completion contract.
2. Resolve mechanical-check failure handling without fabricated semantic judgments.
3. Specify a concrete state/transition table, including early normal returns, cancellation, blocked work, reviewer retries, escalation, and unknown dispatch outcomes.
4. Establish the actual Codex permission and credential model, required capabilities, and version checks. Separate a read-only protocol probe from any paid/live model experiment.
5. Specify durable journal/lease behavior and recovery proofs. “Resume” must not mean blindly replay the last request.
6. Define measurable acceptance and a small completed-work comparison, then present the bounded implementation proposal for authorization.

Ask the user about material ambiguities rather than silently deciding them, but do not ask them to repeat all their existing architectural decisions. Keep project-specific Rust observations in fixtures/examples; the execution mechanism itself should be project-agnostic.

## Proposed verification after implementation is authorized

These are future tests, not tests already run:

- A fake runtime ends a worker turn with progress only; controller resumes the same thread without waking the reviewer.
- A genuine submission wakes the reviewer only after the worker has stopped; independent checks run through real ledger APIs.
- Rejection produces the existing correction packet, resubmission is reviewed at its new commit, and acceptance uses existing integration.
- Failed checks cannot produce fabricated semantic acceptance or bypass independent evidence.
- Changed source, criteria, specification or assignment state invalidates a stale verdict.
- Routine second-rejection escalation pauses rather than dispatching the revoked worker.
- Blocking questions, permission requests, interruption, timeout, cancellation and exhausted budgets terminate or pause predictably.
- Duplicate events, competing controllers, crash before/after dispatch acknowledgement, and crash around verification do not create duplicate work or false completion.
- Missing/doubled token notifications do not silently permit unlimited dispatch or inflate accounting.
- A tiny separately authorized live smoke test confirms actual app-server behavior after offline tests pass.

Use disposable real Git repositories and ledger state for lifecycle tests, with a fake runtime for deterministic failure injection. Include final reviewed correctness, total usage, reviewer invocations, automatic resumptions, elapsed time and human interventions in the eventual benchmark.

## Artifacts and provenance

Existing detailed reports live in the Rust playbook checkout, not this repository:

- `/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/reviews/taskledger-controller/SOURCE-PLAN.md` — original source-backed proposal; its “implementation plan” title is not current authorization to implement.
- `/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/reviews/taskledger-usage-comparison/STOP-ANALYSIS.md` — premature-stop trace and corrected historical comparison.
- `/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/reviews/taskledger-usage-comparison/REPORT.md` — rate-proxy method, component split, exclusions and quota caveats.
- `/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/reviews/taskledger-usage-comparison/usage.json` — earlier usage analysis artifact.
- `/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/reviews/equipment-lending-run-5/` — earlier run reports and usage records.

Historical run-5 Taskledger root thread: `01a08973-fe87-7a91-90b5-e5684cc0a921`. This is an evidence reference, not an instruction to resume it.

At the start of this handoff, existing modified tracked files included the plugin manifest, command registry, README, traceability, token-efficiency README, run guide, pyproject, checkpoint scripts, Taskledger skill/profiles/references, `__init__.py`, CLI/core/database/service source, product/technical specifications, and acceptance tests. Existing untracked files included `docs/AI_ORCHESTRATED_TOOL_INSTALLATION.md`, `scripts/usage_accounting.py`, and `tests/test_usage_accounting.py`. Preserve them.

The previous assistant briefly added `src/taskledger/controller.py` and `src/taskledger/codex_transport.py` after misreading the scope. **Both were removed immediately after the user's correction.** They were never wired into the CLI, committed, tested, or exercised against live models. Their discarded design is not an adopted implementation or a validated prototype.

The current deliverable is this documentation only. No controller has been implemented, no test suite has been run for a controller, no managed project has been initialized for this investigation, and the Financial Tracker worker/reviewer have not been restarted as part of it.
