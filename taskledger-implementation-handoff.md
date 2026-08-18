# Taskledger implementation handoff

**Status:** Ready for implementation. The simple-v1 decisions in this document have been folded into `taskledger-technical-spec.md`.

**Authority order:**

1. `taskledger-product-spec.md` defines behavior.
2. `taskledger-technical-spec.md` defines implementation and incorporates the resolved v1 decisions below.
3. This handoff controls build order and verification discipline. It does not override either specification.

The product specification contains 66 numbered requirements and 12 mandatory end-to-end scenarios. The architecture is appropriate for the product: a short-lived Python CLI, SQLite, real Git worktrees, scoped local credentials, and a durable Git-operation journal. The design should remain local and standard-library-only.

## 1. Gate result

The original design had one governing-spec conflict and several contracts that allowed multiple reasonable implementations. They are resolved below using the smallest reliable v1 behavior and incorporated into the technical specification.

### Blocking conflict

#### G-01 — Requirement-verification invalidation is accidentally global

The product says a requirement verification becomes stale when that requirement, one of its supporting tasks, an affected specification, a required integration, or an explicit orchestrator decision changes (PS-141).

The technical design stores the global plan fingerprint, every active specification revision, and the canonical branch in each requirement-verification snapshot, then says every snapshot value must still equal current state. That makes any unrelated task, requirement, specification, or branch-name change invalidate every verified requirement. It also makes a specification review assertion of “no existing items affected” ineffective.

**V1 decision:** Keep the global plan fingerprint, full specification snapshot, and branch name as historical evidence, but do not use global equality to decide currency. Recompute currency from only:

- the same active requirement revision;
- the same current coverage task set for that requirement;
- the same task revisions, accepted submission OIDs, and valid integration OIDs in that coverage set;
- the approved revisions of specifications referenced by that requirement, but only when the completed review marked the requirement or one of its covering tasks affected;
- reachability of all required integrations from the current canonical branch; and
- explicit invalidation state.

A branch name change alone must not stale a requirement if every required integration remains reachable. The project completion record is still invalidated by an explicit canonical-branch change.

### Blocking ambiguities

#### G-02 — Repeated edits to one specification while its review is pending

The schema allows only one pending review per specification, while preflight says every distinct observation creates a revision/review. It does not say what happens if a file changes from B to C before the B review is completed.

**V1 decision:** Preserve every observation as an immutable `specification_revisions` row, maintain one pending review per specification, and atomically retarget that pending review to the latest observed revision. Derive `change_kind` against the last approved revision. Audit the superseded pending target. The orchestrator reviews the current file state, not transient intermediate states.

#### G-03 — Assignment revocation has unsafe state semantics

The technical command says revocation always returns the task to `PLANNED`, but the product only says it removes future worker authority and preserves work. A task may already have an immutable pending submission that the orchestrator can still verify. An accepted attempt also leaves its assignment row `ACTIVE` until integration succeeds, even though its worker principal is deactivated.

**V1 decision:**

- `ASSIGNED` task + revoke: assignment becomes `REVOKED`; task becomes `PLANNED`.
- `SUBMITTED` task + revoke: assignment becomes `REVOKED`; task remains `SUBMITTED`; the immutable submission remains verifiable. If later rejected, the task becomes `PLANNED` and a new assignment is required.
- Acceptance closes/deactivates worker authority and sets an active assignment to `CLOSED` before integration. An already revoked assignment remains `REVOKED`. A failed integration leaves the task `ACCEPTED` and the assignment closed or revoked.
- Revocation is rejected for a `CLOSED` assignment or an `ACCEPTED`/`COMPLETED` task.
- Cancelling a submitted task requires an explicit disposition that marks its pending/blocked submission `SUPERSEDED`.

#### G-04 — Submission blockers and proposals have no durable linkage

Submission risks/questions are immutable JSON, but blocking items must create or reference durable blockers. The JSON shapes contain no blocker category or ID. Follow-up items also exist both in submission JSON and in `follow_up_proposals`, without a rule connecting them.

**V1 decision:**

- Require `blocker_category` when a submitted risk or question has `blocking=true`.
- During the submission transaction, create one `SUBMISSION`-scoped blocker for every blocking item. The blocker is durably linked by its submission scope; no additional association table is needed.
- A resolved submission-scoped blocker makes the item non-blocking for gate calculations while the immutable original claim remains in history.
- Create one `follow_up_proposals` row for every submission follow-up, with `submission_id` populated.
- Make submission blocker creation and proposal creation part of the same transaction as submission insertion.

#### G-05 — Blocker propagation is described differently by each operation

Section 22 says task, assignment, submission, integration, requirement, and project blockers affect specific transitions. Acceptance and integration precondition lists omit some of those scopes. Repeated reconciliation can also create duplicate system blockers.

**V1 decision:** Implement one pure `blocking_reasons(operation, entity)` function and make every service call it. Use this matrix:

| Operation | Blocking scopes |
|---|---|
| Assign task | project, linked requirement, task, uncertain operation/repository gate |
| Accept submission | project, linked requirement, task, assignment, submission, uncertain operation/repository gate |
| Integrate task | project, linked requirement, task, assignment, submission, integration, uncertain operation/repository gate |
| Verify requirement | project, requirement, every current covering task/integration, uncertain operation/repository gate |
| Complete project | every open blocker at every scope |

For a system-detected fact, reuse the existing open blocker with the same category, scope type, scope ID, and deterministic description. Never create one duplicate per preflight.

#### G-06 — Worker submission conflicts with the recovery gate

The gate table allows a worker submission during repository uncertainty if its own worktree is known, but submission requires a project Git-operation slot. The operation protocol forbids another Git-mutating operation while recovery is unresolved.

**V1 decision:** Block `worker submit` whenever any project operation is `STARTED` or `UNCERTAIN`. Continue to allow worker context, questions, blockers, and follow-up proposals. Submission remains allowed during specification review or an invalid plan when no recovery gate exists and the assignment’s own worktree is proven safe.

#### G-07 — Corrected submission and idempotency rules conflict

The design returns an existing submission whenever the assignment HEAD already has one. That prevents an evidence-only corrected submission for the same immutable commit after rejection and can return a terminal rejected submission that cannot be verified again.

**V1 decision:** Define request identity as `(assignment_id, head_oid, canonical_payload_hash)`.

- Exact retry of the same request returns the existing submission.
- While a submission is `PENDING` or `BLOCKED`, reject a second submission.
- After `REJECTED`, permit a new sequence for the same commit only when the payload differs; this supports evidence-only correction.
- A new repository state must be a new commit and a new submission.
- Require the assignment base commit to be an ancestor of the submitted HEAD; do not accept rebased or unrelated history silently.

#### G-08 — Git merge/checkpoint behavior is not deterministic under user Git configuration

Submission supplies an identity, but merge does not. User settings can enable GPG signing or merge autostash, which can cause prompts or violate the no-stash rule. Safe rollback checks only HEAD and merge markers, not the index/worktree.

**V1 decision:** Run Taskledger-created commits/merges with explicit local command configuration:

```text
git -c user.name=Taskledger \
    -c user.email=taskledger@local \
    -c commit.gpgSign=false \
    -c merge.autoStash=false \
    merge --no-ff --no-edit --no-gpg-sign <accepted_oid>
```

Apply `commit.gpgSign=false` to checkpoint commits too. A known-safe rollback requires all of: canonical HEAD equals the before OID, no Git operation marker exists, the index has no staged delta, and the canonical worktree is clean. Otherwise mark the operation uncertain.

### Resolved supporting ambiguities

#### G-09 — Canonical branch change invalidates too much

The command requires every completed integration to be reachable from the new branch and then invalidates all requirement verifications solely because the branch name changed. Use the G-01 scoped-currency rule: preserve current requirement verifications when their integrations are reachable, but always invalidate project completion.

#### G-10 — Direct requirements linked to tasks

The product says a directly verifiable requirement may have no task; the technical validator forbids any task link. Use mutually exclusive modes in v1: `implementation_required=false` means zero current task links. This keeps the verification algorithm deterministic.

#### G-11 — Review assertion is not exclusive

`spec review` currently permits affected IDs and `no_existing_items_affected=true` together. Require exactly one of:

- at least one affected requirement/task ID and `no_existing_items_affected=false`; or
- both affected lists empty and `no_existing_items_affected=true`.

Require affected requirements to be active and affected tasks to be current non-cancelled tasks.

#### G-12 — `MISSING` and `REMOVED` overlap

V1 treats `MISSING` as the observed file state/change kind and removes `REMOVED` from the enum. `RETIRE_SPECIFICATION` is the orchestrator’s semantic decision to remove the registration.

#### G-13 — Task-update allowed states

Permit `task update` only from `PLANNED` or `ASSIGNED`. `ASSIGNED` requires `CONTINUE` or `REVOKE`. Require `reopen` for `COMPLETED` or `CANCELLED`. A `SUBMITTED` task first needs verification or cancellation disposition; an `ACCEPTED` task first needs integration or explicit cancellation.

#### G-14 — Worktree cleanup recovery is unspecified

V1 does not automatically remove a successful assignment worktree. It reports the retained worktree path. A later version can add cleanup after specifying its recovery proof cases.

#### G-15 — Orchestrator token rotation lacks a command

V1 omits orchestrator-token rotation because it is not a product requirement. The credential file is protected local project state. Worker-token rotation remains because worker credentials are deliberately handed to another execution context.

#### G-16 — Authorization matrix exposes an absent orchestrator action

The original matrix said the orchestrator could ask an assignment question while the command surface exposed question creation only as a worker command. V1 removes orchestrator question creation from the matrix. The orchestrator can create a blocker or answer a worker question; no product requirement asks it to pose one.

#### G-17 — Worker blocker scope is contradictory

“Ignored and rejected” cannot both be the behavior. Require input `scope_type` equal to `ASSIGNMENT` or `TASK`; derive and validate `scope_id` from the credential. Reject any other scope with `WORKER_SCOPE_VIOLATION`.

#### G-18 — Fingerprint ordering needs a byte-level contract

Define one canonical object shape and sort rules in a fixture. Sort entity arrays by full UUID, source refs by `(specification_id, locator, excerpt-or-empty)`, criteria by `position`, requirement links and dependencies by full UUID. Serialize UTF-8 with `sort_keys=True`, `separators=(",", ":")`, and `ensure_ascii=False`, then SHA-256 the exact bytes. Never sort acceptance criteria by their prose.

#### G-19 — Completion invalidation fields are vague

When completion becomes invalid, set `projects.lifecycle='ACTIVE'` and clear `completed_at` and `completion_head_oid`. Preserve the prior completion timestamp, branch, head, and invalidation reason in audit events. Every mutation that can break a completion condition must invoke the same completion-reconciliation function before returning.

#### G-20 — Recovery snapshot is illustrative, not a complete schema

Before implementing read models, define a golden JSON fixture containing full text and IDs for requirements/tasks, rejection corrections, blocked submissions, operation proof facts, and typed `allowed_next_actions`. Use sorted arrays everywhere. The snapshot must allow a fresh model session to act without database access or conversation history.

#### G-21 — Git timeouts and supported platform are unstated

Use a configurable command timeout with a documented 120-second default. Support CPython 3.11+ on POSIX systems with Git 2.20+ for v1; keep permission calls conditional so Windows does not corrupt data, but do not claim Windows support without acceptance tests.

#### G-22 — Branch-name collision handling is unstated

V1 uses full project and task UUIDs in generated branch names. Before worktree creation, it proves both the generated ref and worktree path are unused and never overwrites or adopts existing data.

## 2. Resolved v1 posture

V1 deliberately chooses the smaller behavior at every fork: scoped verification, one latest pending specification review, preserved immutable submissions after revocation, deterministic submission-scoped blockers, no Git mutation during recovery, evidence-only corrected submissions when the claim changes, retained worktrees, and POSIX/Git 2.20+ support. These are implementation decisions, not new product features.

## 3. Instructions to the implementation model

Section 2 is resolved and its decisions are incorporated into the technical specification. Use the following instructions as the implementation sequence.

### 3.1 Operating rules

1. Read both specifications completely before changing files.
2. Build only Taskledger. Do not add an LLM client, worker launcher, server, web UI, pull-request support, CI integration, deployment, cloud state, chat, or plugin system.
3. Use CPython 3.11+ and the standard library at runtime. Use `unittest`; do not introduce pytest as a required dependency.
4. Invoke Git only through one client module using argument arrays and `shell=False`. Never execute evidence commands supplied by workers.
5. Keep domain calculations pure. Services own authorization and use-case orchestration. Database repositories own SQL. Only the Git package may invoke Git.
6. Reject unknown JSON fields. Validate complete request objects before any mutation.
7. Every ledger-only mutation is one `BEGIN IMMEDIATE` transaction containing entity changes and an audit event.
8. Never hold a SQLite transaction open while Git runs. Every Git mutation uses the operation journal.
9. Never silently infer product meaning, affected requirements, conflict resolution, task priority, or assignment parallelism.
10. Do not continue to the next slice until the slice’s tests pass.

### 3.2 Build method

For every command, implement in this order:

1. request dataclass/parser and unknown-field rejection;
2. authorization test;
3. pure precondition/transition tests;
4. repository queries and transaction;
5. service method;
6. CLI wiring and stable envelope;
7. public-CLI acceptance test;
8. failure/idempotency test.

Do not implement all tables first and all behavior later. Complete vertical slices so an error in state ownership appears early.

## 4. Dependency-ordered build plan

### Slice 0 — Freeze executable contracts

Deliver:

- a checked-in decision record resolving G-01 through G-22;
- a complete command registry listing command name, role, request schema, success `data` schema, errors, and allowed gates;
- golden canonical-JSON/fingerprint fixtures;
- golden recovery-snapshot fixtures;
- state transition tables for tasks, assignments, submissions, integrations, blockers, requirements, and project completion;
- a PS-001 through PS-154 traceability table assigning at least one automated test to every product requirement.

Gate: no production service code until these fixtures are reviewed. A weaker model must not design API behavior while implementing it.

### Slice 1 — CLI, configuration, and deterministic output

Deliver:

- `pyproject.toml` and console entry point;
- `cli.py`, `output.py`, `errors.py`, `config.py`, `clock.py`, and `ids.py`;
- JSON input size limit, exact-one-object stdout behavior, verbose stderr behavior, exit-code mapping, canonical JSON helper, UUID and RFC-3339 clock helpers;
- `TASKLEDGER_HOME` resolution and safe directory/file creation helpers.

Tests:

- malformed JSON, non-object JSON, unknown fields, oversized JSON, conflicting flags, and broken authentication return one error envelope;
- stdout contains no logging; stderr contains no token;
- canonical JSON and timestamp fixtures are byte-stable.

Gate: every CLI failure path returns a stable envelope without traceback leakage unless a developer-only test explicitly asks for one.

### Slice 2 — Database foundation and project initialization

Deliver:

- connection pragmas and ordered migrations;
- the complete Section 7 schema, foreign keys, CHECK constraints, and partial unique indexes;
- repository-layer transaction helpers;
- Git discovery for root, common dir, bare status, and symbolic branch;
- two-step, repeat-detection project initialization;
- orchestrator principal/token creation with atomic `0600` file write where supported;
- project resolution and token precedence: `--token`, then `TASKLEDGER_TOKEN`, then orchestrator token file.

Tests:

- Scenario A branch detection and Scenario B detached HEAD;
- non-repository, bare repository, confirmation mismatch, duplicate repository registration, symlink root canonicalization;
- orchestrator credential files are created atomically with owner-only permissions where supported;
- worker token cannot authenticate an orchestrator service.

Gate: no project row, token, or directory debris remains after failed initialization.

### Slice 3 — Specifications and preflight

Deliver:

- specification registration constrained to regular files inside the repository root;
- raw-byte hashing and immutable revisions;
- before-planning auto-baseline behavior;
- after-planning pending reviews using the G-02 rule;
- missing/unreadable/oversize behavior and deduplicated system blockers;
- review XOR validation, affected-item recording, baseline approval/retirement, scoped invalidation, and plan invalidation;
- preflight entry point used by every project command.

Tests:

- unchanged checks are idempotent;
- B then C while one review is pending reviews C and preserves B;
- missing, unreadable, restored, and intentionally retired specifications;
- an unrelated approved change does not stale unaffected requirement verification under G-01;
- registration and symlink swaps cannot escape the repository.

Gate: no execution-sensitive command can pass a newly detected post-planning change.

### Slice 4 — Requirements, tasks, and plan validation

Deliver:

- immutable requirement/task revisions and source/criterion/link/dependency rows;
- create, update, retire, cancel, reopen, and split-by-ordinary-operations behavior;
- explicit active-assignment dispositions;
- one canonical plan object and fingerprint implementation matching golden bytes;
- all-at-once structural diagnostics and cycle detection;
- current-plan test.

Tests:

- every PS-031 structural error alone and several combined in one response;
- direct vs task-backed coverage modes;
- revision history never changes after insertion;
- `CONTINUE` refreshes assignment context; `REVOKE` follows G-03;
- stale revisions return `STALE_STATE`;
- criteria order affects the fingerprint; database row order does not.

Gate: Scenario C passes through the public CLI.

### Slice 5 — Blockers, questions, proposals, and derived progress

Deliver:

- blocker CRUD with creator/scope/project validation;
- the single G-05 propagation function;
- explicit resolution only;
- worker question and task/assignment blocker creation;
- orchestrator answer with optional explicit blocker resolution;
- follow-up proposal review without plan mutation;
- requirement state and progress read models.

Tests:

- every blocker scope against every affected operation;
- a factual issue still blocks after its blocker is resolved;
- repeated preflight does not duplicate a system blocker;
- worker cannot broaden blocker scope or resolve it;
- progress is requirement-based and stable.

Gate: no service contains an ad hoc blocker query outside the shared propagation implementation.

### Slice 6 — Assignment preparation and worker authority

Deliver:

- exact eligibility calculation;
- operation slot acquisition;
- assignment branch/worktree creation at the exact canonical OID;
- activation proof checks;
- scoped worker principal/token and limited context;
- revocation and worker-token rotation behavior;
- assignment-preparation reconciliation.

Tests:

- Scenario D before/after dependency integration;
- two unrelated tasks have concurrent worktrees but one Git journal operation at a time;
- task cannot have two active/preparing assignments;
- worker context contains required linked requirement data and no unrelated plan state;
- crash before Git, after branch creation, and after worktree creation;
- generated branch collision is detected without adopting/deleting existing data.

Gate: an assignment is `ACTIVE` only after all Git facts match the recorded expected state.

### Slice 7 — Submission checkpoint and immutable claims

Deliver:

- strict submission payload parser;
- clean/in-progress worktree inspection;
- checkpoint commit with deterministic Taskledger identity/config;
- base-ancestor and non-empty-diff proof;
- NUL-safe changed-file parsing;
- atomic submission, blockers, proposals, task transition, and audit writes;
- G-07 idempotency behavior;
- checkpoint crash reconciliation.

Tests:

- uncommitted files, pre-existing worker commits, file deletion, rename, spaces/newlines in filenames, ignored-only changes, empty diff, hook failure, dirty-after-hook, base not ancestor;
- exact request retry, evidence-only correction after rejection, changed commit correction;
- crash after checkpoint commit but before submission insert;
- Scenario E worker authority rejection.

Gate: verification always receives a commit OID and generated file set, never a mutable branch tip or worker-supplied file list.

### Slice 8 — Submission verification

Deliver:

- complete criterion-result validation;
- accepted/rejected/blocked rules;
- blocker creation/linkage for blocked decisions;
- rejected continuation for active assignments and planned fallback for revoked assignments;
- assignment closure and worker deactivation on acceptance;
- synchronous handoff to integration only after the acceptance transaction commits.

Tests:

- missing, duplicate, unknown, false, and all-true criterion sets;
- invalid outcome/boolean combinations;
- acceptance blocked by every G-05 relevant scope;
- re-verification of unchanged blocked submission after explicit resolution;
- Scenario F rejection/correction.

Gate: no worker credential can invoke any verification path, including by calling service functions directly in tests.

### Slice 9 — Integration and validity reconciliation

Deliver:

- exact accepted-OID integration preconditions;
- idempotent already-ancestor adoption;
- deterministic merge command from G-08;
- safe abort proof and uncertain fallback;
- successful task completion and downstream eligibility;
- integration reachability reconciliation and scoped invalidation;
- no automatic worktree cleanup under G-14.

Tests:

- successful no-ff merge, accepted OID already present, dirty/wrong canonical worktree, missing accepted object, hook failure, signing/autostash user config, merge conflict safe abort, failed abort uncertainty;
- external canonical rewrite and restoration/reintegration;
- Scenarios D, G, and K.

Gate: a task reaches `COMPLETED` only in the same transaction that records a proven successful/adopted integration.

### Slice 10 — Requirement verification, recovery view, and completion

Deliver:

- task-backed and direct verification preconditions;
- G-01 scoped verification currency and proactive invalidation;
- self-rechecking status calculation;
- complete deterministic recovery snapshot;
- all-at-once completion reasons;
- explicit completion record and G-19 invalidation behavior;
- canonical branch change safeguards.

Tests:

- unrelated plan/spec/branch changes do not stale a requirement;
- every PS-141 listed cause does stale the affected requirement;
- direct evidence path (Scenario I);
- recovery snapshot from each non-final task/submission/operation state (Scenario J);
- all completion blockers together and success (Scenario L);
- completion invalidates after every condition-breaking mutation.

Gate: a fresh test process can use only `project recover` plus the repository to identify the next legal action.

### Slice 11 — Failure injection, security, and full conformance

Deliver:

- injected failure point after every journal boundary in Section 26.5;
- migration backup using SQLite’s backup API rather than copying a live WAL database file;
- token/error/log redaction review;
- complete product-ID traceability report;
- all 12 end-to-end scenarios using only the public CLI.

Tests:

- kill/restart at every operation boundary yields success, known failure, or an explicit recovery blocker—never guessed success;
- hostile branch/path/text/JSON inputs do not invoke a shell or escape configured roots;
- all error codes return the documented exit class and actionable entity IDs/actions.

Gate: all 66 product requirements have at least one passing automated test and all 12 mandatory scenarios pass.

## 5. State-machine rules the implementation must centralize

### Task

```text
PLANNED -> ASSIGNED -> SUBMITTED -> ACCEPTED -> COMPLETED
                         |    |
                         |    -> BLOCKED submission remains task SUBMITTED
                         -> REJECTED returns to ASSIGNED, or PLANNED if assignment revoked

PLANNED/ASSIGNED -> CANCELLED only through orchestrator disposition rules
COMPLETED/CANCELLED -> PLANNED only through reopen with a new revision
ACCEPTED -> ACCEPTED on safe integration failure
COMPLETED -> ACCEPTED when integration validity is lost
```

No generic `set_state` method may be public. Expose named transitions with preconditions.

### Assignment

```text
PREPARING -> ACTIVE -> CLOSED
     |          |
     -> REVOKED -> REVOKED
PREPARING/ACTIVE -> UNCERTAIN only when Git outcome cannot be proven
```

Acceptance closes the assignment. Revocation removes future worker authority but does not erase existing submissions.

### Submission

```text
PENDING -> ACCEPTED
PENDING -> REJECTED
PENDING -> BLOCKED -> ACCEPTED/REJECTED/BLOCKED
PENDING/BLOCKED -> SUPERSEDED only by an explicit task/assignment disposition
```

### Integration

```text
STARTED -> SUCCEEDED
STARTED -> FAILED
STARTED -> UNCERTAIN
SUCCEEDED -> INVALIDATED when recorded integration is no longer reachable
```

### Project

Project phase is derived in this order: pending review, recovery required, current valid completion, execution with current plan, otherwise planning. Blockers are an overlay except where the gate matrix explicitly makes them transition blockers.

## 6. Final handoff checklist

The implementation model must not claim completion until all are true:

- every public command has a frozen request/success/error contract;
- every state mutation authorizes again inside the service layer;
- every database write path is transaction-tested;
- every Git mutation is journaled and restart-tested;
- plan fingerprint and recovery output match golden fixtures byte-for-byte;
- requirement verification invalidation is scoped, not global;
- a worker can submit but cannot accept, integrate, verify, resolve, plan, change branch, or complete;
- accepted work does not satisfy dependencies before successful integration;
- specification changes gate execution and never infer affected items;
- all blockers require explicit orchestrator resolution;
- external canonical rewrites remove current completion credit;
- all 66 numbered product requirements have traceable automated coverage;
- Scenarios A through L pass solely through the installed CLI;
- no Section 30 excluded feature exists.
