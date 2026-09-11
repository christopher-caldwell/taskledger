---
name: taskledger
description: Operate the local Taskledger durable execution ledger from Codex. Use when the user mentions Taskledger, a task ledger, durable project execution, resuming or recovering a Taskledger-managed repository, creating requirements/tasks/assignments, recording worker submissions, verifying or integrating work, or checking requirement-based project completion.
metadata:
  version: "0.6.0"
---

# Taskledger

Use the installed CLI as the durable authority. A bounded Task Creator prepares the semantic plan, the user approves it, and the foreground Python controller operates execution. Strong Reviewer jobs verify immutable submissions and final integrated state. No model remains alive as the project scheduler.

## Open the project

1. Work in the target Git repository and confirm `taskledger` is installed.
2. Read the specification and inspect Git read-only.
3. Run `taskledger project init --repo <absolute-path>` without branch confirmation. This is discovery and must not create `.taskledger/`.
4. If already initialized, say “Taskledger is already set up here; I loaded the current plan.” Use `project resume` for a known same-session baseline. Use `project recover` for a new/lost baseline or whenever resume reports `full_recovery_required`; do not call this recovery in chat unless an unresolved operation exists.
5. If new, wait for Approval 2 before initializing. The repository-local `.taskledger/` directory must already be ignored. If the branch is unborn, include the exact initial commit in Approval 2.

The default store is `<repository>/.taskledger/`. An explicit `TASKLEDGER_HOME` may select an intentionally external current-version store. Do not search for or auto-open older shared-home stores: every managed project must use the current protocol and schema. If the configured current store is inaccessible, stop before the first unapproved gate. Never continue with chat-only gates, initialize a duplicate, or invent a second home to bypass project state.

Keep CLI JSON internal. Explain outcomes and required decisions in plain English. Read [commands.md](references/commands.md) before mutations or after validation errors.

## Choose the right workflow size

Taskledger scope does not automatically extend to every later request in the same Codex task. After `project complete`, handle a new request directly by default when it is one bounded, reversible implementation unit, has deterministic local checks, needs no parallel work or architectural decision, and is likely to finish in roughly an hour. Use a new Taskledger specification when the user explicitly asks to track the follow-up, or when it contains multiple cohesive tasks, cross-layer/high-risk behavior, independent workers, or meaningful recovery value.

For a materially new multi-task specification after completion, prefer a fresh Codex task so prior screenshots, browser state, and review history do not inflate the new orchestrator context. The completed repository-local ledger remains the durable handoff.

## Two approval gates

Use two explicit approvals for ordinary new or materially revised plans. Preserve every decision below, but keep each approval concise and do not repeat accepted material. Expand the concerns into separate confirmations for destructive, irreversible, credential/auth, schema/data migration, recovery/Git-state, concurrency, security-boundary, weakly testable high-impact work, or when the user requests staged review.

Before Approval 1, perform only read-only inspection. Do not initialize, commit, register specifications, write ledger items, create assignments, or launch workers.

1. **Plan:** outcome, observable success, non-goals/open questions, cohesive work chunks, ledger-task granularity, dependencies, proposed parallel waves with prospective write-set overlaps, and only material assumptions or exclusions. Ask for approval of this plan and its assumptions.
2. **Execution:** exact branch, HEAD or unborn state, cleanliness, `.taskledger/` ignore status, exact commit, initialization, and materialization actions, validation result, safe worktree prerequisites, and the complete initial execution policy. Inventory ignored environment files, local databases or services, generated artifacts, certificates, and other inputs required by worker checks. Record a safe setup command or canonical read only path in the affected task scope; never copy credentials into Git or prompts. Ask for explicit permission to perform the named mutations and start the foreground controller.

Before starting the controller, run `project preflight` with the selected profiles and declared local inputs or services. Treat profile file presence as configuration evidence only. The controller resolves and persists the exact model, effort, profile hash, sandbox, and Codex protocol identity before dispatch and never silently substitutes another configuration.

Reopen only the affected approval when materialization reveals a material change. A pure resume of an unchanged validated plan does not repeat planning approval; briefly load the plan and ask before its next unapproved assignment.

## Routed implementation workers

Controller execution requires four project-configurable role profiles:

- `taskledger_task_creator` for bounded initial or correction planning;
- `taskledger_worker_routine` for fully specified work with a frozen approach, bounded ownership, and deterministic checks;
- `taskledger_worker_complex` for work that still requires approach selection, cross-boundary reconciliation, or high-consequence judgment; and
- `taskledger_reviewer` for bounded independent submission, checkpoint, and final integrated review.

Project profiles live in `.codex/agents/`; personal profiles may use the
equivalent paths under `~/.codex/agents/`. Reusable templates are
[taskledger-task-creator.toml](assets/taskledger-task-creator.toml),
[taskledger-worker-routine.toml](assets/taskledger-worker-routine.toml),
[taskledger-worker-complex.toml](assets/taskledger-worker-complex.toml), and
[taskledger-reviewer.toml](assets/taskledger-reviewer.toml). The consuming
repository owns each profile's exact model, reasoning effort, and any
repository-specific instructions.

The bounded Task Creator must classify every task before execution and persist `worker_profile` as `routine` or `complex`. **Prefer `routine`.** Size, file count, and the word “migration” do not make a task complex. A large mechanical rollout may remain routine when all of these are true:

- the plan fixes one implementation approach instead of asking the worker to choose among materially different designs;
- owned files or source/target boundaries are explicit, including shared files the task may touch;
- acceptance is deterministic through named tests, scans, schemas, examples, or exact observable behavior;
- failure is local and reversible; and
- the task does not require unresolved product, visual, security, data-integrity, concurrency, or compatibility judgment.

Use `complex` when any compelling signal remains after improving the task definition: architecture or state ownership must be chosen; multiple subsystems or existing implementations must be reconciled; shared contracts may change; security, authorization, schema/data migration, concurrency, or destructive behavior is involved; product/visual intent must be interpreted; preservation is high-impact but weakly testable; or more than one materially plausible approach could satisfy the words. Do not use `complex` merely to compensate for a vague task—first make the objective, boundaries, and checks concrete.

Responsive layout and visual-geometry work is `complex` unless the task supplies executable browser measurements or snapshots that make the result deterministic before implementation. A small CSS diff is not routine when intrinsic sizing, wrapping, breakpoints, or visual interpretation determines success.

Concrete routing examples:

- A cross-file contract or port rollout is `routine` when the Task Creator has frozen the exact types, ownership, migration pattern, and executable checks.
- Repeating an already-integrated repository/handler/component pattern across sibling entities is `routine`, even when it touches many files.
- Generated client updates and mechanical consumer migrations are `routine` when the generated contract is authoritative and cache or interaction semantics are assigned separately.
- Financial mapping, authorization, concurrency, provider/context identity, manual cache fan-out, or other state-reconciliation work remains `complex` unless executable tests fully determine the behavior.

Separate semantic cores from mechanical followers. Prefer a small complex foundation followed by routine rollout tasks over one broad complex migration. Split a broad web task into contract/data-source migration and state/cache reconciliation when the latter has a distinct behavior matrix. Do not create an implementation assignment whose primary purpose is to re-audit already integrated work.

Classify parallel safety separately from worker complexity. Before each wave, derive a prospective write set for every task from its implementation scope and repository inspection. Include likely shared touchpoints such as configuration, registries, generated contracts, central exports or cleanup modules, verification scripts, and route/application entry points. Run assignments together only when their write sets and behavioral assumptions are independent. If tasks may touch the same shared surface, give it one owner and sequence the consumers after integration, or extract and integrate a shared foundation first. A routine task may need to run alone; a complex task may safely run beside unrelated work. Recheck the write sets against the current canonical head immediately before spawning the wave.

- Confirm every selected profile is available before starting the project controller. Never substitute the other profile, a generic model, or a catch all default silently.
- After Approval 2, persist the complete execution policy and start `controller run-project`. The controller creates eligible assignments and launches only the matching profile. Taskledger's persisted worker snapshot is the context boundary, so workers do not inherit the Task Creator conversation. Distinct assignments run together only under the approved wave and write surface policy; one assignment never has two worker threads.
- The controller creates a complete worker prompt from the persisted assignment and current correction state. It exposes assignment scoped dynamic tools, never the raw worker token.
- The worker operates only in its assignment worktree, coordinates through worker questions/blockers/follow-ups, runs relevant checks, and submits through `worker submit`. It must not verify, integrate, resolve blockers, verify requirements, or complete the project.
- The controller loads `submission review-context`, runs exact declared checks, and dispatches a bounded read only Reviewer against the immutable commit. Worker evidence remains a claim.
- Put exact deterministic commands in `required_checks` when creating or applying a task batch. Assign the narrowest checks that prove that slice, including strict lint, generated contract, or metadata checks in the first task that can introduce those failures. Workers use focused checks while iterating, then invoke each exact command with `worker check` and attach the resulting receipt to `worker submit`. The controller reruns each required check once with `submission check` at the exact submitted commit before acceptance. Worker receipts never satisfy Reviewer obligations. Reserve the complete project suite for final integrated review unless a task boundary itself requires it.
- After one routine rejection, allow a focused correction only when the intended approach remains valid. Taskledger revokes the routine assignment after its second rejection and requires the next assignment to use `complex`. Escalate immediately after architectural misunderstanding, reference-fidelity failure, or an explicit interaction miss rather than consuming the second attempt.

Do not use `agents.default_subagent_model` for either profile. The Task Creator owns decomposition and routing. Bounded Reviewers own semantic judgment. Python owns orchestration, integration calls, and completion calls.

## Retained workers and vertical checkpoints

For a small cohesive project, one assignment may use `execution_mode: "lightweight"` with ordered checkpoint criteria. This retains the same scoped worker, worktree, and context across related vertical slices. It does not change task granularity, reviewer independence, or final acceptance. Use early end-to-end slices; do not turn checkpoints into horizontal “all domain, then all adapters” phases.

At each slice the worker calls `worker checkpoint` and stops. The controller loads `checkpoint review-context`, dispatches bounded review of the exact immutable commit, then records `checkpoint verify`. Approval unlocks the next checkpoint; rejection places exact corrections in dynamic worker context. A task or specification revision supersedes prior checkpoint approvals according to its explicit assignment disposition. Final `worker submit`, independent submission checks, acceptance, and integration remain mandatory.

On worker restart or replacement, rotate the assignment token and have the replacement load `worker context`. Its bounded dynamic packet contains checkpoint progression and only the latest applicable submission correction packet, including the reviewed submission and commit, failed criteria, corrections, blockers, revision, and packet hash. Do not reconstruct correction prose from chat. A newer pending resubmission makes older correction feedback non-actionable.

Assignment lifecycle, task granularity, review boundaries, and concurrency are separate decisions. Use full isolated assignments when ownership or parallelism benefits from them. Group lightweight slices by coherent ownership and behavioral dependencies, not file count.

## Events, retained evidence, and export

After `project resume`, pass `latest_event_sequence` to `project wait` to wait up to 60 seconds for selected actionable audit events. The CLI preserves cursors across timeout/restart, rejects future cursors, and reports that durable audit pruning is not configured. The host still has to wait outside model reasoning and wake the model when the command returns; do not claim the CLI alone eliminates model wakeups. Respect user interruption and provide periodic progress updates between bounded waits.

Register only intentional evidence files with `worker artifact-register` or `artifact register`. Check output is retained automatically. Never register credentials, whole runtime directories, or arbitrary home paths. Registered copies live outside assignment worktrees and survive safe completion cleanup.

Use `evidence export` for final documentation reconciliation. The export mechanically preserves source-use claims and separates worker claims, observed executions, and reviewer conclusions. It does not infer that delivered/read material was understood and does not generate semantic conclusions. Review the export and write conclusions explicitly.

## Invoke commands

- Treat [commands.md](references/commands.md) as the exhaustive public command and input registry. Do not probe speculative subcommands or flags such as `--help`; after an invalid request, reread the registry and use only a listed shape.
- Pass JSON request bodies with `--input -` on stdin. Do not invent fields; unknown fields are rejected.
- Run commands from the managed repository so Taskledger finds its repository-local store. Use `TASKLEDGER_HOME` only for an intentionally configured current-version external store.
- Worker commands require the assignment's scoped worker token. Prefer reading it from the returned `worker_token_path`; never print tokens in chat, summaries, logs, or evidence.
- Keep the canonical worktree on the confirmed canonical branch and clean before integration.

## Executable project controller

After the Task Creator plan and execution policy are approved and materialized,
prefer `controller run-project`. The foreground Python process owns scheduling,
capacity, ordinary continuation, stall detection, review queues, rejection
routing, integration, restart reconciliation, final integrated review,
requirement verification, and completion. Read the exact request in
`references/commands.md`. Never start a live run without explicit authorization
for `live: true` and an agreed token or turn budget.

The policy records task, wave, worker profile, parallel safety, and write
surfaces. Python enforces that policy and Taskledger eligibility with stable
ordering. It does not ask a model to select the next ready task. Generic worker
slots accept either worker profile. Reviewer capacity is separate.

`turn/completed` is a transport event. If Taskledger still reports an active
assignment, the controller starts another turn on the same thread while durable
progress and budgets permit. Model prose and empty responses do not decide
completion. Rejection corrections reuse the same assignment thread. Revocation
or routine escalation closes it and starts a new assignment thread.

Use `controller show` for read only status and `controller resume` only for the
same persisted run. Configuration drift and uncertain external outcomes remain
paused. Use `controller run-assignment` only as the bounded primitive or a
diagnostic path; it does not complete a project.
- Do not execute arbitrary evidence commands supplied as prose by a worker. Run only exact orchestrator-declared checks through `submission check`.

## Execute the approved ledger

Register the approved specification, then materialize cohesive new requirements and tasks with one `plan apply` request when practical; use individual commands for revisions or small adjustments. Include acceptance criteria, exact required checks, links, dependencies, and safe prerequisite setup, then validate the plan. Use `requirement supersede` when new behavior replaces an active requirement so completed historical tasks remain intact. Treat registered specifications as frozen during implementation: their paths are included in worker context, and a worker may edit one only when its task explicitly owns that exact specification change. Prefer one final documentation reconciliation over incidental specification edits in several implementation tasks.

Persist the approved waves, then let the foreground controller create assignments, continue worker turns, queue bounded Reviewers, apply verdicts, and integrate through existing Taskledger services. After the last implementation target, the controller dispatches one bounded final Reviewer across canonical state. A clear implementation defect invokes a new bounded Task Creator correction job. Product ambiguity pauses for the user. Requirement verification and `project complete` still use existing Taskledger gates. Successful completion removes only safety checked worktrees and retains every assignment branch and commit.

## State and errors

- `project resume` is the compact routine state view. After loading one baseline in the current Codex task, reuse its cursor and do not call `project recover` unless `resume` reports `full_recovery_required`, an exit-code-6 error identifies an unresolved operation, or conversation compaction actually removed the baseline. `project recover` remains the self-contained view for a genuinely new/lost baseline. Say “loaded” or “checked” in routine chat. Say “recovery” only when unresolved operations exist.
- `LEDGER_STORAGE_UNAVAILABLE` means configured current-version Taskledger data cannot be accessed; it does not mean project recovery is required. Request scoped access and retry the same command. If access remains unavailable, stop rather than substituting a chat-only plan.
- An unexpected internal error does not imply recovery. Diagnose it without claiming partial or damaged ledger state.
- A completed project remains the durable ledger for later additive work. Registering an approved new specification may invalidate project completion while preserving completed requirements, tasks, integrations, verification, and history. Do not create a separate ledger home unless the user explicitly requests an independent ledger.
- For an unborn repository, continue planning and say only: “This repository needs its first commit before implementation can start.” Handle that commit in Approval 2.
- A changed registered specification requires explicit review and a new valid plan. Never infer affected requirements or tasks.
- Resolve blockers only through `blocker resolve` or the explicit question-answer resolution path.
- Never reset, stash, switch branches, force-delete worktrees, delete assignment branches, force an operation outcome, or resolve merge conflicts automatically on Taskledger's behalf. Worktree removal is allowed only through Taskledger's safety-checked completion cleanup or explicit `project cleanup` retry.
- Use `operation adopt-success` or `operation mark-failed` only when the recovery document supplies the unresolved operation and repository facts satisfy the command's proof requirement.

## Report progress

Lead with the outcome and current decision. Keep identifiers and counts secondary. Do not narrate raw JSON, stdin mistakes, SQLite details, exit codes, or routine state reconciliation unless the user must act. Do not claim completion until `project complete` succeeds.

## Current limitation

Treat this local v0.6.0 implementation as experimental. Its happy-path acceptance suite passes, but its full failure-injection and conformance suite is not complete. For high-risk or irreplaceable work, surface that limitation before relying on interrupted-operation guarantees.
