# Taskledger Product Specification

**Document status:** Current functional specification for technical design and implementation
**Product:** Taskledger  
**Document authority:** This document defines required product behavior. The technical specification may select implementation details, but it must not weaken, omit, or expand the behaviors defined here.

---

## 1. Document Purpose

Taskledger is the durable authority for a software implementation effort operated by a local Python controller. Model sessions perform bounded planning, implementation, and review jobs. No model remains responsible for running the workflow.

This document defines what Taskledger, the controller, the Task Creator, reviewers, and workers must do. The controller implementation is intentionally local and small: Python, SQLite, Git, and the Codex app server.

Taskledger does not design or implement software. It records authoritative project state, validates structurally checkable rules, controls state transitions, preserves evidence, and prevents a project from being declared complete before its requirements have actually been verified and integrated.

---

## 2. Product Definition

Taskledger allows an approved product specification to move through the following durable lifecycle:

1. Register the product specification documents.
2. Decompose those documents into atomic product requirements.
3. Decompose implementation work into bounded executable tasks.
4. Validate requirement coverage, task dependencies, routing, and approved concurrency policy.
5. Let the Python controller assign mechanically eligible tasks to implementation workers.
6. Receive implementation submissions and supporting evidence.
7. Use bounded independent Reviewer jobs to verify each submission.
8. Integrate accepted work into the project’s confirmed canonical working branch.
9. Use a bounded final Reviewer job to verify the integrated result against each requirement.
10. Recover the complete current state in a later session.
11. Finish only after all required product behavior is verified and no blocking work remains.

Bounded models supply semantic judgment. The Python controller owns scheduling, waiting, continuation, recovery, budgets, and routing. Taskledger supplies durable authority and deterministic transition rules.

---

## 3. Product Goals

Taskledger must:

- preserve project state across unrelated model sessions;
- make the ledger, rather than conversation history, the authoritative source of current project state;
- preserve traceability from specifications to requirements, from requirements to tasks, and from completed work back to its requirements;
- prevent workers from approving or completing their own work;
- prevent tasks from being assigned before their dependencies are integrated;
- detect specification changes that could invalidate an existing plan;
- require explicit orchestrator review when the specification changes after planning begins;
- withhold task completion until accepted work exists on the confirmed canonical branch;
- measure project progress primarily through requirement coverage and verification;
- recover safely after process interruption or an incomplete operation; and
- stop rather than guess whenever the next state cannot be determined safely.

---

## 4. Non-Goals

Taskledger is not responsible for:

- interpreting a product specification;
- deciding what the software architecture should be;
- writing implementation code;
- choosing task priorities;
- deciding which tasks are safe to run in parallel;
- semantically judging whether a requirement is atomic or implementation-independent;
- semantically verifying implementation behavior without a bounded Reviewer decision;
- deploying software;
- operating CI/CD pipelines;
- replacing a source-code repository;
- replacing a general-purpose issue tracker;
- cloud synchronization;
- team chat or collaboration;
- generic workflow automation; or
- securely sandboxing a malicious worker at the operating-system level.

The controller is not a hosted service, distributed worker system, generic workflow engine, or general model framework. Cache policy may affect cost, but it never affects correctness.

The product may coordinate Git operations needed for assignment and integration, but Git hosting, pull requests, deployment, and repository administration remain outside its scope.

---

## 5. Actors and Responsibilities

### 5.1 User and approvals

The user owns product decisions and approvals. Human input is required for product ambiguity, unresolved specification changes, blocking questions, uncertain external outcomes, unsupported configuration drift, and budget extensions. An ordinary model turn ending never requires human intervention.

### 5.2 Task Creator

The Task Creator is a bounded strong model job. It reads the specification and produces or revises requirements, tasks, acceptance criteria, deterministic checks, dependencies, worker routing, execution waves, and declared write surfaces. It exits after the approved plan is durably materialized. It may be invoked again when final review finds an implementation defect that needs semantic decomposition. It does not schedule work or monitor execution.

### 5.3 Python controller

The controller is the orchestration engine. It derives mechanical readiness from current Taskledger state and the approved execution policy. It manages worker and reviewer capacity, dispatches or resumes exact assignment threads, continues active assignments after normal turn completion, enforces durable budgets, detects stalls, freezes submitted work, queues reviewers, routes rejections, honors escalation, invokes existing verification and integration operations, performs restart reconciliation, and requests completion only after final requirement verification.

The controller does not invent product meaning, decide semantic parallel safety, fabricate reviewer conclusions, or resolve uncertain external outcomes by guesswork.

### 5.4 Worker

A worker performs bounded implementation work for one assignment.

A worker may:

- inspect and modify the repository in the workspace provided for the assignment;
- run development commands permitted by the orchestrator’s instructions;
- produce code, tests, documentation, or other implementation artifacts;
- ask questions;
- report blockers;
- report risks;
- propose follow-up work; and
- submit a claim that the assigned task is ready for verification.

A worker may not:

- modify the project plan through Taskledger;
- create, revise, cancel, or complete requirements or tasks;
- change dependencies;
- assign work;
- approve or reject any submission;
- resolve blockers;
- change the canonical branch;
- verify a requirement; or
- declare project completion.

A worker submission is evidence and a claim. It is never a completion decision.

Routine workers use the lowest practical configured model for constrained work. Complex workers use a medium strength configured model when architecture, integration, debugging, or shared state judgment remains. Each thread belongs to one assignment attempt. Correction turns reuse that thread; a replacement assignment receives a new thread.

### 5.5 Reviewer

A Reviewer is a bounded strong model job with read only authority. It inspects the exact immutable submission or integrated canonical state, evaluates the supplied criteria, considers controller run check receipts, and returns structured findings. Reviewer prose has no authority. The controller rejects malformed, incomplete, duplicate, stale, or inconsistent verdicts and never infers acceptance.

### 5.6 Taskledger

Taskledger is a deterministic state-management system.

Taskledger is responsible for:

- persisting project, specification, requirement, task, assignment, submission, verification, blocker, integration, and completion state;
- enforcing role-scoped operations;
- validating structurally checkable plan rules;
- enforcing dependency and state-transition rules;
- detecting registered specification changes;
- preventing execution while mandatory specification review is pending;
- preserving evidence and traceability;
- coordinating isolated assignment workspaces;
- coordinating integration into the confirmed canonical branch;
- calculating requirement-based progress;
- determining whether completion preconditions are met; and
- exposing a complete recovery snapshot.

Taskledger must not invent product meaning, infer a semantic answer, prioritize work, or silently resolve ambiguity.

---

## 6. Core Domain Terms

### 6.1 Project

A project is one Taskledger-managed implementation effort associated with one existing Git repository and one confirmed canonical working branch.

### 6.2 Canonical Working Branch

The canonical working branch is the branch into which accepted task work must be integrated. It is selected explicitly during project initialization and is never inferred from a conventional branch name.

### 6.3 Specification

A specification is a registered document that defines desired product behavior. A project may have one or more specifications.

### 6.4 Requirement

A requirement is an atomic, independently understandable statement of observable product behavior traced to at least one registered specification.

All active requirements are required for project completion. Taskledger does not introduce an additional optional-requirement feature.

A requirement can be:

- **task-backed**, meaning one or more executable tasks are required to satisfy it; or
- **directly verifiable**, meaning no implementation task is needed, as may be true for a documentation-only or already-satisfied requirement.

### 6.5 Task

A task is bounded implementation work created to contribute to one or more requirements. A task can contribute to multiple requirements, and a requirement can require multiple tasks.

### 6.6 Assignment

An assignment is one authorized worker attempt against one current revision of one task.

### 6.7 Submission

A submission is a worker’s immutable claim that a specific repository state satisfies the assigned task. It includes implementation evidence, changed files, risks, unresolved questions, and proposed follow-up work.

### 6.8 Verification

Submission verification is the orchestrator’s independent judgment of a worker submission. Requirement verification is the orchestrator’s final judgment that integrated work satisfies an active requirement.

### 6.9 Integration

Integration is the operation that places the exact accepted submission into the project’s canonical working branch.

### 6.10 Blocker

A blocker is a durable, explicitly resolved condition that prevents safe progress for a project, requirement, task, assignment, submission, or integration attempt.

---

## 7. Product Invariants

The following invariants must always hold:

1. The ledger is the authoritative source of current project state.
2. Conversation history is not authoritative project state.
3. The project has exactly one confirmed canonical working branch.
4. Taskledger never assumes that the canonical branch is `main` or any other conventional name.
5. Every active requirement remains traceable to at least one registered specification.
6. Every non-cancelled executable task contributes to at least one active requirement.
7. Workers never mark tasks complete.
8. Workers never accept or reject their own work.
9. A submitted task is not complete.
10. An accepted task is not complete until its accepted repository state is integrated into the canonical branch.
11. A dependency is not satisfied until the dependency task is complete and its integration remains present on the canonical branch.
12. Project completion is determined by verified requirement coverage, not by task count.
13. A completed task remains traceable to the requirements it was intended to satisfy.
14. A specification change after planning begins prevents continued execution until the orchestrator completes a review.
15. Open blockers remain open until an orchestrator explicitly resolves them.
16. A state-changing operation must either finish in a known valid state or leave a durable recovery condition that prevents unsafe continuation.
17. Ambiguity results in a blocker or rejected operation rather than an implicit decision.

---

## 8. Project Lifecycle

A project moves through the following effective phases:

### 8.1 Planning

The project is in planning when its current plan has not yet passed validation or has become invalid because requirements, tasks, dependencies, coverage, or active specifications changed.

### 8.2 Execution

The project is in execution when its current plan is valid and no mandatory specification review or repository-recovery gate prevents work.

Execution includes task assignment, worker implementation, submission, verification, integration, and requirement verification.

### 8.3 Specification Review Required

This state overrides planning or execution whenever a registered specification is added, changed, removed, missing, or unreadable after planning has begun.

Only state-inspection, recovery, specification-review, blocker, and plan-maintenance actions may continue while review is pending. New task assignment, submission acceptance, integration, requirement verification, and project completion must remain blocked.

Worker submissions may still be recorded so that implementation work is not lost, but they cannot be accepted or integrated until review is complete and the resulting plan is valid.

### 8.4 Completed

The project is completed only after all completion conditions in Section 23 pass and the orchestrator explicitly requests completion.

A later specification change, task reopening, requirement revision, invalidated integration, or other change that invalidates completion must return the project to an incomplete state.

### 8.5 Blocked Overlay

A project, requirement, or task may be shown as blocked without changing its underlying lifecycle phase. A blocker describes why safe progress cannot continue for that scope.

---

## 9. Project Initialization and Canonical Branch

### PS-001 — Initialize from an existing repository

Taskledger must initialize a project only from an existing, non-bare Git working repository.

During initialization Taskledger must:

1. resolve the repository root;
2. detect the branch currently checked out in that working repository;
3. present the exact detected branch to the orchestrator;
4. require an explicit confirmation of that exact branch; and
5. record the confirmed branch as the project’s canonical working branch.

Project creation must not complete until the branch is confirmed.

If the repository root is already registered, initialization must be idempotent:
it returns the existing project identity and directs the orchestrator to continue
that project. It must not require a second confirmation, create a duplicate
project, or describe the existing project as stale state.

An unborn symbolic branch is a valid initialization base. Taskledger must report
that the first commit is still required, allow planning and recovery, and reject
assignment or completion with a specific initial-commit precondition instead of
surfacing Git's missing-revision error.

The default durable store must be `.taskledger/` under the canonical repository
root. Discovery must not create it. New-project confirmation must require that
path to be covered by Git ignore rules before writing the database, credentials,
or assignment state. An explicit `TASKLEDGER_HOME` remains available for an
intentionally external current-version store. Automatic older-version store
discovery is not supported.

If the repository is in detached-HEAD state or a checked-out branch cannot be determined, initialization must stop and explain that the orchestrator must first select a branch. Taskledger must not substitute `main`, the remote default branch, or any other branch.

### PS-002 — Preserve one canonical branch

A project must always have exactly one confirmed canonical branch.

The branch may have any valid Git branch name, including a feature, release, bugfix, development, or mainline branch.

### PS-003 — Change the canonical branch explicitly

Only the orchestrator may change the canonical branch. The action must name and explicitly confirm the new branch.

Taskledger must reject or block a branch change that would immediately violate known integration invariants. It must identify the unsafe condition and leave the existing canonical branch unchanged.

Taskledger must never silently update the canonical branch because the repository’s checked-out branch changed.

---

## 10. Specification Registration and Change Review

### PS-010 — Register specifications

The orchestrator may register one or more specification documents associated with the repository.

For each specification, Taskledger must preserve:

- a stable specification identity;
- its repository-relative location;
- an approved content revision or equivalent content fingerprint;
- its registration state; and
- enough history to determine that a later observed version differs from the approved version.

### PS-011 — Detect changes

Taskledger must compare every registered active specification with its last approved state before allowing execution-sensitive actions.

A difference includes content modification, addition of a specification after planning begins, removal, an unreadable file, or a missing file.

The product does not require a continuously running file watcher. Detection may occur at an explicit check or at the next Taskledger command, provided execution cannot continue past that command without detection.

### PS-012 — Require orchestrator review after planning begins

Planning begins when the orchestrator first creates a requirement or executable task.

If a specification difference is detected after that point:

- Taskledger must preserve the newly observed specification state;
- the project must enter Specification Review Required;
- the orchestrator must identify the requirements and tasks it considers affected, including an explicit assertion when no existing item is affected;
- Taskledger must not infer affected items;
- affected requirement verification must no longer count as current;
- the current plan must be treated as unvalidated against the new specification state; and
- execution-sensitive actions may resume only after all pending specification reviews are completed and the plan is validated again.

The orchestrator remains responsible for revising, retiring, reopening, cancelling, or adding requirements and tasks as necessary.

### PS-013 — Changes before planning

If a registered specification changes before planning begins, Taskledger may update the approved specification baseline because no requirements or tasks yet depend on the prior version. The change must still be recorded.

### PS-014 — No automatic semantic interpretation

Taskledger must not attempt to determine what a specification change means, whether a requirement is still correct, or what implementation changes are needed.

---

## 11. Requirement Management

### PS-020 — Requirement structure

Each active requirement must include:

- a stable identity;
- an atomic statement of observable behavior;
- enough detail to be independently understandable;
- at least one source reference to a registered specification; and
- a declaration of whether executable implementation tasks are required.

Taskledger may validate the presence and referential integrity of this data. The orchestrator is responsible for the semantic quality, atomicity, and implementation independence of the requirement.

### PS-021 — Requirement traceability

Taskledger must always be able to identify:

- the specification or specifications supporting a requirement;
- the tasks currently intended to satisfy it;
- the completed integrated tasks used in its latest verification; and
- whether that verification is still current.

Revising or retiring a requirement must preserve its prior identity and history rather than silently erasing traceability.

### PS-022 — Requirement coverage modes

A task-backed requirement must be linked to at least one non-cancelled executable task before the plan can be valid.

A directly verifiable requirement may have no executable task. The orchestrator must still provide evidence and verify it before the requirement can be complete.

### PS-023 — Requirement state

Taskledger must report each active requirement as one of:

- **complete** — a current orchestrator verification exists, and every task used for that verification remains completed and integrated on the canonical branch;
- **blocked** — the requirement is incomplete and an unresolved project-, requirement-, or relevant task-level blocker prevents safe progress; or
- **remaining** — the requirement is incomplete but not currently blocked.

Specification Review Required may cause incomplete requirements to be reported as blocked by the review gate.

### PS-024 — Requirement revision and retirement

Only the orchestrator may revise or retire a requirement.

A revision must invalidate any prior verification of that requirement. A retirement must preserve the historical requirement and its links but remove it from the active completion set after the plan is revalidated.

If a requirement revision affects an active assignment, Taskledger must require the orchestrator to decide whether the assignment remains valid or must be revoked.

When new behavior replaces rather than revises an active requirement, the
orchestrator may supersede it atomically. Taskledger must create the replacement,
retire and link the prior requirement, preserve completed tasks as historical
evidence, and require new current-plan tasks to link to active requirements.

---

## 12. Planning and Plan Validation

### PS-030 — Approved semantic plan

The bounded Task Creator creates or revises the requirements, tasks, requirement links, dependencies, routing, and concurrency policy that make up the plan. The user approves the plan. Taskledger does not generate or prioritize it, and the Python controller does not add semantic scheduling decisions during execution.

Taskledger may accept a batch of new requirements and tasks with request-local
references. The entire batch must commit or roll back as one ledger transaction;
the controller must still invoke explicit Taskledger validation before execution.

### PS-031 — Structural validation

Taskledger must validate all structurally checkable plan rules:

1. every active task-backed requirement has at least one non-cancelled task;
2. every directly verifiable requirement is explicitly marked as not requiring an executable task;
3. every non-cancelled executable task has a non-empty objective;
4. every non-cancelled executable task has non-empty implementation scope;
5. every non-cancelled executable task has at least one acceptance criterion;
6. every non-cancelled executable task contributes to at least one active requirement;
7. every dependency refers to a valid non-cancelled task in the same project;
8. a task does not depend on itself;
9. the dependency graph contains no cycle; and
10. every active requirement remains traceable to a registered active specification.

Taskledger must return all detected validation errors in one result when practical rather than stopping after the first error.

### PS-032 — Semantic responsibility

Taskledger must not claim that a requirement is truly atomic, that a task is optimally bounded, that acceptance criteria are sufficient, or that the plan semantically covers the specification. Those judgments belong to the bounded Task Creator and the user approval process.

### PS-033 — Plan validity

Planning is complete only when validation succeeds against the current active specification state and current requirement/task definitions.

Any mutation that changes specification baselines, active requirements, task definitions, acceptance criteria, requirement links, task lifecycle, or dependencies must invalidate the prior validation until the orchestrator validates the new plan.

Completion or assignment state changes alone do not alter the plan definition.

---

## 13. Task Management

### PS-040 — Task structure

Each non-cancelled executable task must include:

- a stable identity;
- an objective;
- implementation scope;
- one or more acceptance criteria;
- zero or more dependencies; and
- one or more associated active requirements.

A task may also declare exact deterministic required-check commands. Each
declared command is part of the immutable task revision and must be represented
by successful matching evidence before Taskledger records a worker submission.

### PS-041 — Task lifecycle

A task must have a durable lifecycle that distinguishes at least:

- planned and unassigned;
- assigned and under implementation;
- submitted and awaiting verification;
- accepted and awaiting integration;
- completed after successful integration; and
- cancelled.

A blocker may overlay any non-final task state.

### PS-042 — Task revision

Only an authorized planning action may revise a task. During automated correction planning, that action is a bounded Task Creator result applied by the controller.

Revising a task must preserve its identity, increment or otherwise distinguish its definition revision, and invalidate the current plan validation.

When a task changes after assignment, Taskledger must require an explicit authorized decision whether:

- the existing assignment may continue under the revised definition; or
- the assignment must be revoked and the task returned to an unassigned state.

Taskledger must not decide this automatically.

### PS-043 — Split a task

The Task Creator may propose a split. The approved plan creates replacement tasks, updates affected dependency and requirement links, and cancels the original task with a recorded split reason.

Taskledger must preserve the original task and its history. The resulting plan must be validated before new assignments proceed.

### PS-044 — Cancel a task

Only an authorized controller or user action may cancel a task.

Cancellation must preserve task history and traceability. If the task has an active assignment, the orchestrator must explicitly revoke or otherwise dispose of that assignment. Cancellation that removes required coverage must make the plan invalid.

### PS-045 — Reopen a task

Only an authorized controller or user action may reopen a completed or cancelled task.

Reopening must create a new active task revision or equivalent new implementation attempt, invalidate requirement verifications that depended on the prior completion where appropriate, invalidate project completion, and require plan validation before further assignment.

Previously integrated repository history is not erased by reopening; new work begins from the current canonical branch.

---

## 14. Dependencies and Eligibility

### PS-050 — Dependency satisfaction

A task dependency is satisfied only when the dependency task:

- has an accepted submission;
- has been successfully integrated;
- is marked complete; and
- still has valid integration present on the canonical branch.

Submission or acceptance without integration does not satisfy a dependency.

### PS-051 — Assignment eligibility

A task is eligible for assignment only when:

- the current plan is valid;
- no mandatory specification review is pending;
- no repository-recovery gate prevents safe assignment;
- the task is planned and has no active assignment;
- all dependencies are satisfied; and
- no unresolved blocker prevents work on the project, task, or an associated requirement.

Taskledger may list eligible tasks. The Python controller selects the next target by stable ordering within the approved wave and routing policy. It does not ask a model to choose among mechanically equivalent ready tasks.

### PS-052 — Parallel work

Taskledger may maintain multiple active assignments for different eligible tasks. It must never assume that tasks are safe to run in parallel based on their descriptions or file scope. The approved Task Creator policy supplies that decision, and the Python controller enforces it.

Before approval of a parallel wave, the Task Creator must inspect the repository
and derive a prospective write set for each task. The write set includes explicit
ownership plus likely shared configuration, registries, generated contracts,
central exports or cleanup modules, verification scripts, and application entry
points. Tasks may run together only when their writes and behavioral assumptions
are independent. A shared surface must have one owner, with consumers sequenced
after its integration or dependent on a separately integrated foundation.
Worker complexity and parallel safety are independent decisions.

A task may have at most one active assignment at a time.

---

## 15. Assignment and Worker Context

### PS-060 — Scoped assignment

An assignment must be bound to:

- one task;
- the exact task revision assigned;
- one worker identity or scoped worker credential;
- one explicit worker profile, either `routine` or `complex`;
- one repository workspace;
- one starting canonical commit; and
- one attempt number.

### PS-061 — Limited worker context

Taskledger must provide the worker only the ledger information needed for the assignment, including:

- the task objective;
- implementation scope;
- acceptance criteria;
- associated requirement statements and source references;
- active registered specification identities and repository-relative paths,
  without embedding their complete contents;
- the assignment workspace;
- the assignment identity; and
- current answers to assignment questions.

Worker-scoped access must not expose general plan mutation, verification, integration, blocker resolution, canonical-branch management, or project-completion operations.

Repository access needed to implement the task is permitted. The authority restriction applies to Taskledger and project-state operations; Taskledger is not required to be an operating-system security sandbox.

### PS-062 — Assignment revocation

The orchestrator may revoke an assignment. Revocation must invalidate the worker’s authority to submit further ledger actions for that assignment while preserving any repository work for inspection or recovery.

### PS-063 — Approved worker routing

Taskledger must require the approved plan to select `routine` or `complex` when
creating an assignment and must preserve that selection in assignment context,
recovery state, review context, and audit history. Taskledger records the stable
capability role; the consuming repository maps each role to its chosen model,
reasoning effort, and worker instructions. Taskledger must not silently select,
substitute, or downgrade a worker profile.

The Task Creator must prefer `routine` when the task fixes one implementation
approach, explicitly bounds ownership, provides deterministic acceptance, keeps
failure local and reversible, and requires no unresolved high-consequence
judgment. Size, file count, and a mechanical migration do not make a task
complex. It must use `complex` when the worker still needs to choose architecture
or state ownership, reconcile shared contracts or implementations, interpret
product or visual intent, or decide security, authorization, schema/data,
concurrency, destructive, compatibility, or weakly testable high-impact
behavior. The Task Creator must first clarify a vague task rather than using the
complex profile as a substitute for adequate planning.

A mechanically broad rollout remains routine when it repeats a frozen,
integrated pattern. The Task Creator should separate unresolved semantic or
state-reconciliation work into a focused complex task and route deterministic
followers independently. Final integrated verification is a bounded Reviewer
job applied by the controller, not an implementation assignment whose purpose
is to review other workers.

---

## 16. Worker Questions, Blockers, Risks, and Follow-Up Work

### PS-070 — Questions

A worker may record a question during implementation and indicate whether it blocks progress.

The orchestrator may answer the question. A blocking question must prevent the affected assignment from being accepted until the blocking condition is explicitly resolved.

### PS-071 — Worker-reported blockers

A worker may create a blocker against its own assignment or task. The worker may not resolve it.

### PS-072 — Risks

A worker may record risks in its submission. Risks do not automatically become blockers unless the worker or orchestrator explicitly records a blocker.

### PS-073 — Follow-up proposals

A worker may propose follow-up work during implementation or submission.

A proposal does not automatically modify the plan or create a task. The orchestrator decides whether to acknowledge it and whether to create or revise planning items.

---

## 17. Submission

### PS-080 — Submission contents

When a worker claims implementation is ready, Taskledger must record an immutable submission containing:

- a summary of the work;
- the exact repository commit or equivalent immutable repository state being submitted;
- the changed-file set for that state;
- evidence supporting the claim;
- known risks;
- unresolved questions and whether each is blocking; and
- proposed follow-up work.

Taskledger may calculate repository-derived fields, such as the exact changed-file list, rather than trusting worker-entered text.

### PS-081 — Submission is not completion

A submission changes the task to awaiting verification. It must not mark the task complete, satisfy downstream dependencies, verify any requirement, or count as integrated work.

### PS-082 — Immutable reviewed state

Verification must apply to the exact repository state identified by the submission. Later repository changes require a new submission and may not be silently included in an earlier verification.

### PS-083 — Corrected submissions

After a first rejection, the worker may continue under the active assignment and
create a corrected submission. Prior submissions and verification outcomes must
remain available as history. After a task's second routine-worker rejection,
Taskledger must revoke it, return the task to planned, and require a complex
worker profile for the next assignment.

---

## 18. Submission Verification

### PS-090 — Independent verification

Every submission must be verified by the orchestrator or an orchestrator-authorized verification context that is distinct from the worker assignment that produced the submission.

Taskledger must prevent a worker-scoped identity from verifying its own or any other submission.

### PS-091 — Verification decision

Verification must explicitly consider:

- each task acceptance criterion;
- whether the implementation matches intended behavior;
- whether required evidence exists; and
- whether blocking issues remain.

### PS-092 — Verification outcomes

Verification produces exactly one of the following outcomes for the reviewed submission state:

- **accepted** — all acceptance criteria are satisfied, intended behavior matches, required evidence exists, and no blocking issue remains;
- **rejected with corrections** — the submission is not acceptable and concrete corrections are required; or
- **blocked pending additional action** — a condition prevents a safe accept/reject decision or prevents acceptance despite otherwise useful work.

### PS-093 — Rejection behavior

Rejection must:

- preserve the rejected submission and verification record;
- include corrective guidance;
- return the task to active implementation after a first routine rejection or a
  complex-worker rejection unless the orchestrator revokes it;
- automatically revoke a routine assignment after its second rejection and
  require a complex replacement assignment; and
- allow a later corrected submission.

### PS-094 — Blocked verification behavior

A blocked verification must preserve the submission, create or reference an open blocker, and withhold acceptance. Once the blocker is explicitly resolved, the orchestrator may re-verify the same unchanged submission or require a new one.

### PS-095 — Acceptance behavior

Acceptance must identify the exact accepted submission state, revoke or close further worker mutation authority for that accepted attempt, and trigger or enable integration.

Acceptance alone must not mark the task complete.

---

## 19. Integration

### PS-100 — Integrate accepted state

The exact accepted submission state must be integrated into the confirmed canonical working branch.

Taskledger must not substitute a different branch tip, unsubmitted changes, or later worker changes.

### PS-101 — Completion after integration

A task becomes complete only after integration succeeds and Taskledger records the resulting canonical branch state.

Only then may dependent tasks treat the task as satisfied.

### PS-102 — Integration failure

If integration fails:

- Taskledger must withhold task completion;
- preserve the accepted submission;
- restore the repository to a known safe state when deterministic rollback is possible;
- record the failure and relevant evidence;
- create or reference a repository/integration blocker; and
- require orchestrator-directed corrective action.

Taskledger must not silently choose a conflict resolution.

### PS-103 — Integration validity

Taskledger must be able to verify that each completed task’s recorded integration remains reachable from the current canonical branch.

If external Git operations remove a recorded integration from the canonical branch, the affected task and requirement completion must no longer count as current. Taskledger must block unsafe continuation until the orchestrator restores or reintegrates the accepted work.

---

## 20. Blockers

### PS-110 — Blocker structure

A blocker must record:

- a stable identity;
- a category;
- a description of the blocking condition;
- the affected scope;
- who or what created it;
- creation time;
- current open/resolved state; and
- an explicit resolution and resolution time when closed.

Supported categories must include at least:

- missing product decision;
- ambiguous requirement;
- external dependency;
- repository state; and
- verification failure.

Additional internal categories may exist only to represent required failure or recovery behavior, not to add product workflow.

### PS-111 — Explicit resolution

A blocker remains open until the orchestrator explicitly resolves it. A changed external condition may make resolution possible, but it must not silently delete the blocker.

### PS-112 — Blocking scope

A blocker may apply to the whole project or a specific requirement, task, assignment, submission, or integration attempt.

Taskledger must propagate the practical effect of a lower-level blocker into requirement and progress reporting without changing the blocker’s recorded scope.

---

## 21. Recovery and Authoritative Snapshot

### PS-120 — Durable recovery

Taskledger must recover all authoritative state after interruption without relying on previous conversation history.

At minimum, recovery must include:

- project identity and canonical branch;
- active specification revisions and pending reviews;
- current plan validity and validation errors;
- all active requirements and their state;
- eligible tasks;
- unfinished tasks;
- active assignments and workspaces;
- submitted work awaiting verification;
- rejected or blocked submissions still needing action;
- accepted work awaiting integration;
- completed integrations and their current validity;
- open worker questions;
- unresolved blockers;
- requirement-verification status;
- progress totals; and
- exact reasons the project cannot yet complete.

### PS-121 — Interrupted operations

If Taskledger detects an operation that started but did not reach a durably known outcome, it must reconcile only states that can be proven deterministically.

When multiple outcomes are plausible, Taskledger must record a recovery blocker and require the orchestrator to choose an explicit recovery action.

### PS-122 — Machine-readable state

The complete recovery snapshot and all state-changing command results must be available in a deterministic machine-readable form suitable for an LLM orchestrator.

A human-readable view may also exist but is not required.

### PS-123 — Compact routine resume

Taskledger must provide a compact, deterministic current-state projection for
routine polling when the orchestrator retains a trusted project baseline. It
must be computed from current authoritative tables after repository and
specification preflight, not reconstructed from audit events. Missing or stale
baselines and unresolved operations must fail safe to a fresh compact snapshot
or the complete recovery contract.

### PS-124 — Secret-minimized assignment handoff

Model-visible assignment and credential-rotation results must not contain a
plaintext worker credential. The credential is delivered through its
owner-only local path. Assignment creation returns a stable hash of the exact
persisted assignment snapshot rather than duplicating that snapshot.

### PS-125 — Focused review context

Taskledger must provide an orchestrator-only submission review projection bound
to the exact base and submitted commit OIDs. It must identify omissions or
bounds explicitly and must not claim semantic correctness, execute worker
evidence, or replace independent inspection and testing.

---

## 22. Progress Reporting

### PS-130 — Requirement-based progress

Primary project progress must be calculated from active requirements, not task counts.

Taskledger must report at least:

- total active requirements;
- complete requirements;
- remaining requirements;
- blocked requirements;
- requirements awaiting final verification;
- active implementation tasks;
- submissions awaiting verification;
- accepted tasks awaiting integration; and
- unresolved blockers.

### PS-131 — Task counts are secondary

Task counts may be reported as operational detail, but they must not be presented as the authoritative completion percentage.

### PS-132 — Stable derivation

Progress must be derived from durable ledger and repository state. It must not depend on an orchestrator’s conversational summary.

---

## 23. Requirement Verification and Project Completion

### PS-140 — Final requirement verification

A requirement becomes complete only when the controller records a current verification backed by a bounded final Reviewer decision that the observable behavior is satisfied.

For a task-backed requirement, Taskledger must require every current non-cancelled task linked as its implementation coverage to be complete and validly integrated before accepting requirement verification.

For a directly verifiable requirement, the final Reviewer must provide direct evidence.

The requirement verification must record enough information to identify:

- the requirement revision verified;
- the active specification state considered;
- the integrated task results considered, if any; and
- the Reviewer evidence and controller application record.

### PS-141 — Verification invalidation

A requirement verification must stop counting as current when any of the following occurs:

- the requirement is revised;
- a supporting task is revised, reopened, cancelled, or loses valid integration;
- an affected specification change is approved;
- the canonical branch changes in a way that no longer contains required integrations; or
- an authorized controller or user action explicitly invalidates it.

### PS-142 — Completion checks

A project is eligible for completion only when all of the following are true:

1. every active requirement is complete;
2. every task required by an active requirement has been verified, accepted, and integrated;
3. every completed integration remains present on the canonical branch;
4. no unresolved blocker remains;
5. no task is actively assigned, submitted for verification, accepted but unintegrated, or otherwise under active implementation;
6. no specification review is pending;
7. the current plan is valid against the current specifications; and
8. no unresolved interrupted operation or repository-recovery condition exists.

The absence of remaining task records is not sufficient.

### PS-143 — Explicit completion

Only the Python controller or an explicit user action may request project completion.

Taskledger must evaluate every completion condition at that moment. If any condition fails, it must reject completion and return all known reasons.

On success, Taskledger must record the canonical branch and commit at which completion was established.

### PS-144 — Completion invalidation

If later observed state invalidates a completion condition, Taskledger must remove the project’s effective completed state and identify why it is no longer complete.

### PS-145 — Safe post-completion worktree cleanup

After recording successful project completion, Taskledger must attempt to remove
finalized assignment worktrees that it can prove are its own, are registered to
the managed repository, are on the recorded assignment branch, contain no local
changes, and have no Git operation in progress. Cleanup must never force removal
or delete assignment branches, commits, integration records, or ledger history.

An unsafe worktree must be retained and reported with a reason. The orchestrator
may retry cleanup explicitly while the project remains completed. Repeated
cleanup must be safe when a worktree has already been removed.

---

## 24. Failure Behavior

### PS-150 — Correctness over progress

Taskledger must prefer a safe stop over speculative continuation.

### PS-151 — Atomic ledger updates

A ledger-only state-changing operation must either apply all of its related changes or apply none of them.

### PS-152 — Repository and ledger coordination

For operations that affect both Git and the ledger, Taskledger must preserve an operation record sufficient to determine whether the repository change succeeded, failed safely, or requires explicit recovery.

### PS-153 — No silent repair

Taskledger may perform deterministic cleanup, such as aborting a failed integration when it can prove the pre-operation state is restored. It must not perform semantic repair, choose conflict resolutions, reinterpret specifications, or silently rewrite the plan.

### PS-154 — Actionable errors

A rejected command must return:

- the rule that prevented the action;
- the relevant entity identities;
- the current authoritative state; and
- the next permitted corrective actions when determinable.

---

## 25. Required End-to-End Behaviors

The implementation must pass the following product-level scenarios.

### Scenario A — Non-main canonical branch

1. The repository is checked out on `feat/new-feature`.
2. Initialization reports `feat/new-feature` and requires confirmation.
3. The orchestrator confirms it.
4. Taskledger records `feat/new-feature` as canonical.
5. Completed task work is integrated there, not into `main`.

### Scenario B — Detached HEAD

1. The repository is in detached-HEAD state.
2. Initialization cannot detect a checked-out branch.
3. Taskledger stops and requires the orchestrator to select a branch.
4. No project with an assumed branch is created.

### Scenario C — Valid plan

1. The orchestrator registers a specification.
2. It creates task-backed and directly verifiable requirements.
3. It creates tasks, criteria, coverage links, and dependencies.
4. Validation identifies all structural errors at once.
5. After corrections, validation succeeds and execution may begin.

### Scenario D — Dependency enforcement

1. Task B depends on Task A.
2. Task A is submitted but not accepted: Task B is ineligible.
3. Task A is accepted but integration fails: Task B remains ineligible.
4. Task A integrates successfully: Task B becomes eligible if no other gate exists.

### Scenario E — Worker cannot complete work

1. A worker receives an assignment-scoped context.
2. It submits work and evidence.
3. The task remains submitted.
4. The worker attempts to accept or complete the task.
5. Taskledger rejects the operation based on worker scope.

### Scenario F — Rejection and correction

1. A worker submits a commit.
2. The orchestrator rejects it with corrections.
3. The rejected submission remains in history.
4. The assignment returns to implementation.
5. The worker submits a corrected commit.
6. The corrected submission is independently evaluated.

### Scenario G — Accepted but not integrated

1. The orchestrator accepts a submission.
2. Integration encounters a conflict and is safely aborted.
3. The task remains accepted but incomplete.
4. Downstream dependencies remain unsatisfied.
5. A repository blocker describes the conflict.
6. After orchestrator-directed correction, integration succeeds and the task becomes complete.

### Scenario H — Specification change

1. A valid plan exists and execution has begun.
2. A registered specification changes.
3. On the next Taskledger interaction, the change is detected.
4. The project enters Specification Review Required.
5. New assignment, acceptance, integration, requirement verification, and completion are blocked.
6. The orchestrator records affected items, updates the plan as needed, completes review, and validates the plan.
7. Execution resumes.

### Scenario I — Direct requirement

1. A requirement is explicitly marked as not requiring implementation work.
2. Plan validation accepts its lack of task coverage.
3. The orchestrator supplies direct evidence and verifies it.
4. The requirement becomes complete without an artificial task.

### Scenario J — Recovery

1. The orchestrator session ends while tasks are active and one submission awaits verification.
2. A new session requests recovery.
3. Taskledger returns the active assignments, workspace locations, pending submission, blockers, eligible tasks, requirement progress, and completion blockers.
4. No prior conversation is needed.

### Scenario K — External canonical rewrite

1. A task is completed and its integration is recorded.
2. An external Git operation rewrites the canonical branch so the integration is no longer reachable.
3. Taskledger detects the loss.
4. The task and dependent requirement no longer count as currently complete.
5. Completion is blocked until the accepted work is restored or reintegrated.

### Scenario L — Project completion

1. Every active requirement has current verification.
2. All supporting task integrations remain on the canonical branch.
3. No task is active or awaiting action.
4. No blocker, specification review, invalid plan, or uncertain operation remains.
5. The orchestrator requests completion.
6. Taskledger records the canonical completion commit.

---

## 25A. Human-Authorized v0.6 Capabilities

This section records product decisions authorized after the original v1 review.
It is human-facing specification authority. The Codex skill and worker templates
are derived operating guidance and do not supersede these decisions.

Taskledger must deliver rejected-submission corrections through assignment-scoped
durable worker context. The current packet binds the reviewed submission and
commit, failed criteria, exact correction text, relevant blockers, verification
revision, and stable hash. A newer resubmission makes older feedback non-actionable.

Declared local checks may be executed through the CLI. Each observed execution
records caller class, exact command, cwd, source commit/tree fingerprint, timing,
status, and retained bounded output. Worker execution cannot satisfy the
orchestrator's independent-review obligation. Source-changing, failed, timed-out,
or interrupted checks remain explicit and cannot become successful evidence.

Intentional evidence artifacts may be copied into ledger-managed retained
storage with hashes and provenance. Registration is scoped to authorized
worktrees, rejects path escape/symlinks/directories/oversized files, and survives
assignment-worktree cleanup. A deterministic export must preserve source-use
claims and distinguish worker claims, observed execution, and reviewer
conclusions without inventing semantic understanding.

An assignment may define ordered intermediate vertical checkpoints. A worker
records an immutable checkpoint commit and stops; the orchestrator reviews that
exact commit and approves or rejects its criteria. Approval unlocks the next
slice without final acceptance or integration. Definition revisions supersede
prior approvals. Final submission, independent verification, scoped credentials,
and canonical integration retain their existing meaning.

The lightweight path uses this same checkpoint state machine to retain one
worker across a small coherent project. Worker lifecycle, review boundary, task
granularity, and concurrency remain separate choices.

Project preflight must report local versions, repository prerequisites, profile
configuration, declared input metadata, and explicitly required service reachability
without reading secrets. Configuration presence is not proof that a host can
launch the exact named profile; host availability remains a labeled handshake.

The CLI may wait for selected actionable audit events using durable sequences,
a bounded timeout, cancellation, and missed-event diagnostics. The host remains
responsible for waiting outside model reasoning and waking the model.

Usage tooling requires turn identified telemetry, deduplicates exact raw event
IDs, and attributes actual model and effort. For the current app server contract,
`tokenUsage.last` is the latest upstream response value and `tokenUsage.total`
is cumulative thread usage. A tool using turn may contain several response
updates, so the controller deduplicates and adds response local `last` values by
exact turn and event identity. It never adds cumulative `total` snapshots.
Reasoning tokens are an output subset, and measured tokens are not billing.
Missing or inconsistent telemetry is reported and prevents further token budget
admission; legacy token counters are ignored.

## 25B. Executable Project Controller

An approved execution policy is durable project state. For every target it records the task, execution wave, worker profile, parallel safety decision, and prospective write surfaces. Python may derive readiness from current Taskledger state, but it must not infer missing semantic concurrency or routing decisions.

The foreground controller uses generic worker capacity and separate reviewer capacity. The default limits are two workers and one reviewer. A free worker slot may run either a routine or complex assignment according to the target profile. Capacity limits, turn limits, stall limits, runtime failure limits, elapsed time, and token admission limits survive restart. Exhaustion pauses the run.

A completed Codex turn is only a transport event. After every worker turn, the controller reads current Taskledger state. A pending submission enters review. A blocker pauses that target. Revocation stops the old thread. An active assignment continues automatically while durable progress and budgets permit. Model prose, including an empty response or a statement of completion, does not alter this rule.

One worker thread belongs to one assignment attempt. Normal continuation and correction reuse it. Revocation or routine worker escalation closes it, and the replacement assignment receives a new thread. Submitted work remains frozen while controller checks and a bounded Reviewer inspect the exact commit.

Reviewer output must cover every current criterion exactly once and must be structurally consistent with the requested outcome. Required check failure prevents acceptance. A missing, malformed, duplicate, stale, or inconsistent result is retried only within the reviewer budget. It never becomes acceptance by inference.

Accepted work uses the existing Taskledger verification and integration path. Known integration failure, uncertain integration outcome, and successful integration remain distinct. Only successful current integration unlocks dependent tasks.

After approved work is integrated, the controller starts one bounded final Reviewer against canonical state. A satisfied result supplies evidence for existing requirement verification and completion checks. An implementation defect starts one bounded Task Creator job that materializes correction tasks and returns control to Python scheduling. Product ambiguity pauses for the user.

The controller persists dispatch intent, external thread and turn identity, resolved model and effort, agent configuration hash, sandbox policy, protocol identity, raw usage events, and consumption state. Restart reconciliation consumes a proven result once. An external operation that may have happened but cannot be identified or reconciled pauses as uncertain.

The security boundary is proportional to this local product. Orchestrator credentials never reach model prompts or environments. Worker ledger operations remain assignment scoped. Reviewers receive read only source access and no mutation authority. Privileged Taskledger changes are controller owned. This does not claim containment against hostile code running as the local user.

## 26. Scope Boundary for Technical Design

The technical design must implement the behaviors in this document using the simplest reliable architecture.

It must not introduce product scope such as a persistent model orchestrator, hosted collaboration, pull request management, CI orchestration, deployment, cloud state synchronization, general agent chat, distributed workers, or automatic semantic interpretation by the Python scheduler.

A technical mechanism is acceptable when it is necessary to enforce a requirement in this document—for example, scoped credentials, repository workspaces, transactional persistence, version fingerprints, or operation recovery records. Such mechanisms must remain subordinate to the product behavior and must not become additional product features.
