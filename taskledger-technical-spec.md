# Taskledger Technical Design Specification

**Document status:** Final implementation design  
**Governing specification:** `taskledger-product-spec.md`  
**Design rule:** Implement the complete governing product specification with the smallest reliable local architecture. Do not add autonomous planning, hosted collaboration, CI/CD, pull requests, deployment, cloud synchronization, or semantic product reasoning.

---

## 1. Purpose and Conformance

This document defines an implementation-ready design for Taskledger.

The implementation is conformant only when:

- every behavior in the product specification is implemented;
- no worker-scoped operation can mutate planning, verification, integration, canonical-branch, blocker-resolution, requirement-verification, or project-completion state;
- requirement verification remains the authority for product progress;
- accepted work is integrated into the explicitly confirmed branch before a task is complete;
- specification changes and uncertain repository operations create hard execution gates; and
- a fresh orchestrator session can recover all authoritative state from Taskledger and the repository alone.

When this document appears to conflict with the product specification, the product specification controls.

---

## 2. Architecture Decision Summary

Taskledger will be implemented as a local command-line application with the following choices:

| Concern | Decision |
|---|---|
| Runtime | CPython 3.11 or later |
| Runtime dependencies | Python standard library only |
| Supported v1 host | POSIX operating systems with Git 2.20 or later |
| User interface | One `taskledger` CLI with deterministic JSON input/output |
| Persistence | One local SQLite database |
| Repository integration | Installed `git` executable invoked without a shell |
| Worker isolation | One Git branch and one external Git worktree per assignment |
| Authorization | One local orchestrator credential per project and one scoped credential per assignment |
| Concurrency | SQLite transactions plus a durable per-project Git operation journal |
| Background services | None |
| Change detection | Explicit checks and command-boundary preflight checks |
| State model | Relational current state plus immutable historical records and an audit log |
| Network services | None |

This is intentionally not a server, agent framework, web application, or MCP service. A model orchestrator can call the CLI as a tool and consume JSON. A future adapter could wrap the CLI, but no adapter is part of this implementation.

### 2.1 Why this is the simplest adequate architecture

A local CLI avoids process lifecycle, ports, authentication services, deployment, and network failure modes. SQLite provides transactions, referential integrity, safe concurrent readers, and durable recovery without requiring a database server. Git worktrees provide repository isolation with ordinary Git semantics. Python’s standard library supplies SQLite, hashing, subprocess execution, path handling, credential generation, file permissions, and JSON serialization without a runtime dependency graph.

A plain JSON file was rejected because concurrent worker updates, referential integrity, atomic multi-entity transitions, and crash recovery would require reimplementing database behavior. An append-only event-sourced system was rejected because the product does not require distributed replication or historical state reconstruction, and relational current state is simpler to implement and query.

### 2.2 V1 simplicity rules

When more than one implementation could satisfy the product specification, v1 uses these rules:

- requirement-verification currency is scoped to the requirement and its own current sources and covering tasks; global snapshots are historical evidence, not global invalidation switches;
- one pending review per specification always targets the latest observed file state while every observation remains in revision history;
- worker authority closes at submission acceptance, independently of later integration success;
- all blocker effects are calculated by one shared domain function rather than repeated service-specific queries;
- no Git-mutating operation starts while any project operation is `STARTED` or `UNCERTAIN`;
- assignment branches are retained, while clean finalized worktrees are removed after project completion; and
- Taskledger does not add convenience recovery, cleanup, or automation features that are not required by the product specification.

---

## 3. Operating Model and Trust Boundary

### 3.1 Local operating model

Taskledger runs on the same machine as the Git repository and workers. Each CLI invocation is short-lived:

1. load configuration;
2. open and migrate the database;
3. authenticate the caller;
4. resolve the project;
5. run preflight reconciliation and specification checks;
6. enforce command gates;
7. perform the command;
8. emit one JSON response; and
9. exit.

No daemon or background watcher is required.

### 3.2 Trust model

Taskledger enforces authority at its own command boundary. Worker credentials can invoke only worker operations for one assignment. Orchestrator operations require the project’s orchestrator credential.

A worker necessarily receives write access to its Git worktree and can execute development commands there. Taskledger is not a hostile-code sandbox and does not attempt to prevent a process running as the same operating-system user from reading arbitrary files. The orchestrator must not intentionally provide its credential to a worker. This boundary satisfies the product requirement that workers lack unrestricted **Taskledger project authority** without introducing an unrelated container or sandbox platform.

### 3.3 Principal identities

Every state-changing record identifies a principal:

- one active `ORCHESTRATOR` principal per project; and
- one `WORKER` principal per assignment attempt.

The worker principal is permanently scoped to its assignment. Deactivating or revoking it prevents additional worker commands while retaining complete history.

---

## 4. Filesystem Layout

The root directory defaults to `<canonical-repository>/.taskledger` and can be
overridden explicitly with `TASKLEDGER_HOME` for legacy v0.1 or intentionally
external stores.

When no repository-local database exists, the CLI may continue a matching
accessible v0.1 shared-home project. Failure to access that optional legacy
location must not block discovery of a genuinely new repository or imply an
interrupted operation. `TASKLEDGER_HOME` remains the explicit compatibility
path when sandbox or filesystem policy prevents automatic lookup.

```text
<repository>/.taskledger/
├── taskledger.sqlite3
├── credentials/
│   └── <project-id>.orchestrator-token
├── projects/
│   └── <project-id>/
│       ├── assignments/
│       │   └── <assignment-id>/
│       │       └── worker-token
│       └── worktrees/
│           └── <assignment-id>/
└── locks/
```

Requirements:

- `.taskledger`, `credentials`, and assignment credential directories use owner-only permissions where supported.
- Token files use mode `0600` where supported.
- `.taskledger/` must be ignored before initialization. Discovery does not create it, and confirmation fails safely when it is not ignored.
- Assignment worktrees are nested under the ignored directory. The SQLite database remains outside every assignment worktree root.
- Taskledger does not edit the repository’s ignore rules; the orchestrator presents that change at the working-base gate.

The database stores canonical absolute repository paths. Symlinks are resolved at registration time. Registered specification paths are stored relative to the repository root.

---

## 5. Package and Module Structure

```text
pyproject.toml
src/taskledger/
├── __init__.py
├── __main__.py
├── cli.py
├── output.py
├── errors.py
├── auth.py
├── config.py
├── clock.py
├── ids.py
├── db/
│   ├── connection.py
│   ├── migrations.py
│   ├── repositories.py
│   └── sql/
│       ├── 0001_initial.sql
│       └── ...
├── domain/
│   ├── enums.py
│   ├── models.py
│   ├── transitions.py
│   ├── plan_validation.py
│   ├── progress.py
│   └── completion.py
├── services/
│   ├── projects.py
│   ├── specifications.py
│   ├── requirements.py
│   ├── tasks.py
│   ├── assignments.py
│   ├── submissions.py
│   ├── verification.py
│   ├── integration.py
│   ├── blockers.py
│   └── recovery.py
└── git/
    ├── client.py
    ├── inspection.py
    ├── worktrees.py
    ├── submission.py
    ├── integration.py
    └── reconciliation.py

tests/
├── unit/
├── integration/
└── acceptance/
```

The package exposes a console entry point:

```toml
[project.scripts]
taskledger = "taskledger.cli:main"
```

### 5.1 Layer responsibilities

- `cli.py` parses commands, loads JSON input, authenticates, invokes one service, and formats output.
- `services` implement use-case transactions and role checks.
- `domain` contains deterministic state rules and calculations with no subprocess or database access.
- `db` owns SQL, migrations, and transaction boundaries.
- `git` is the only layer allowed to invoke Git.
- `output.py` guarantees stable response envelopes.

No service may bypass domain transition checks or execute ad hoc SQL outside the repository layer.

---

## 6. SQLite Configuration and Transaction Rules

Each connection executes:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;
```

Rules:

1. All timestamps are UTC RFC 3339 strings with millisecond precision.
2. All IDs are lower-case UUIDv4 strings generated with `uuid.uuid4()`.
3. Boolean values are stored as integers with `CHECK (value IN (0,1))`.
4. JSON values are canonical UTF-8 JSON stored as `TEXT`; Python validates shape before write.
5. JSON is serialized with sorted keys and compact separators so hashes are deterministic.
6. Ledger-only mutations run in `BEGIN IMMEDIATE` transactions.
7. A service transaction updates all related state, inserts an audit event, and commits once.
8. Git commands never execute while holding a long-running SQLite transaction.
9. A Git-affecting operation uses the operation journal described in Section 20.
10. Database migrations are ordered SQL files recorded in `schema_migrations` and run before command handling.

---

## 7. Persistent Data Model

The following schema is normative. Column names may vary only when semantics and constraints remain identical.

### 7.1 `schema_migrations`

```text
version             INTEGER PRIMARY KEY
applied_at          TEXT NOT NULL
```

### 7.2 `projects`

```text
id                          TEXT PRIMARY KEY
repository_root             TEXT NOT NULL UNIQUE
git_common_dir              TEXT NOT NULL
canonical_branch            TEXT NOT NULL
canonical_branch_confirmed_at TEXT NOT NULL
lifecycle                   TEXT NOT NULL CHECK (lifecycle IN ('ACTIVE','COMPLETED'))
planning_started_at         TEXT NULL
completed_at                TEXT NULL
completion_head_oid         TEXT NULL
created_at                  TEXT NOT NULL
updated_at                  TEXT NOT NULL
```

Effective project phase is derived rather than duplicated:

1. pending specification review -> `SPECIFICATION_REVIEW_REQUIRED`;
2. uncertain operation/repository gate -> `RECOVERY_REQUIRED`;
3. lifecycle completed and still valid -> `COMPLETED`;
4. current plan valid -> `EXECUTION`;
5. otherwise -> `PLANNING`.

### 7.3 `principals`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
role                TEXT NOT NULL CHECK (role IN ('ORCHESTRATOR','WORKER'))
assignment_id       TEXT NULL
active              INTEGER NOT NULL CHECK (active IN (0,1))
created_at          TEXT NOT NULL
deactivated_at      TEXT NULL
```

Application validation requires `assignment_id` for workers and forbids it for orchestrators. There is exactly one active orchestrator principal per project.

### 7.4 `principal_credentials`

```text
principal_id        TEXT PRIMARY KEY REFERENCES principals(id)
token_hash          TEXT NOT NULL
created_at          TEXT NOT NULL
rotated_at          TEXT NULL
```

`token_hash` is SHA-256 over the 32-byte random token encoded as lowercase hexadecimal. Plaintext tokens are never stored in SQLite.

### 7.5 `specifications`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
relative_path       TEXT NOT NULL
lifecycle           TEXT NOT NULL CHECK (lifecycle IN ('ACTIVE','RETIRED'))
active_revision_id  TEXT NULL
created_at          TEXT NOT NULL
retired_at          TEXT NULL
UNIQUE(project_id, relative_path)
```

### 7.6 `specification_revisions`

```text
id                  TEXT PRIMARY KEY
specification_id    TEXT NOT NULL REFERENCES specifications(id)
sequence            INTEGER NOT NULL
file_state          TEXT NOT NULL CHECK (file_state IN ('PRESENT','MISSING','UNREADABLE'))
content_hash        TEXT NULL
content_bytes       BLOB NULL
error_message       TEXT NULL
observed_at         TEXT NOT NULL
approved_at         TEXT NULL
UNIQUE(specification_id, sequence)
```

For `PRESENT`, `content_hash` and `content_bytes` are required. For `MISSING` or `UNREADABLE`, both are null and `error_message` explains the observation.

### 7.7 `specification_reviews`

```text
id                          TEXT PRIMARY KEY
project_id                  TEXT NOT NULL REFERENCES projects(id)
specification_id            TEXT NOT NULL REFERENCES specifications(id)
from_revision_id            TEXT NULL
to_revision_id              TEXT NOT NULL REFERENCES specification_revisions(id)
change_kind                 TEXT NOT NULL CHECK (change_kind IN (
                              'ADDED','MODIFIED','MISSING','UNREADABLE','RESTORED'))
state                       TEXT NOT NULL CHECK (state IN ('PENDING','COMPLETED'))
resolution                  TEXT NULL CHECK (resolution IN ('APPROVE_REVISION','RETIRE_SPECIFICATION'))
affected_requirement_ids_json TEXT NULL
affected_task_ids_json      TEXT NULL
no_existing_items_affected  INTEGER NULL CHECK (no_existing_items_affected IN (0,1))
review_summary              TEXT NULL
reviewer_principal_id       TEXT NULL REFERENCES principals(id)
created_at                  TEXT NOT NULL
completed_at                TEXT NULL
```

A partial unique index permits at most one pending review per specification. If another distinct state is observed while a review is pending, Taskledger inserts a new immutable specification revision and retargets the existing pending review to that latest revision in the same transaction. `change_kind` is recalculated against the last approved revision, and the replaced pending target is recorded in the audit log. The orchestrator reviews the latest observed state rather than transient intermediate states.

### 7.8 `requirements`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
current_revision    INTEGER NOT NULL
lifecycle           TEXT NOT NULL CHECK (lifecycle IN ('ACTIVE','RETIRED'))
created_at          TEXT NOT NULL
updated_at          TEXT NOT NULL
retired_at          TEXT NULL
```

### 7.9 `requirement_revisions`

```text
requirement_id          TEXT NOT NULL REFERENCES requirements(id)
revision                INTEGER NOT NULL
statement               TEXT NOT NULL
details                 TEXT NOT NULL
implementation_required INTEGER NOT NULL CHECK (implementation_required IN (0,1))
created_by_principal_id TEXT NOT NULL REFERENCES principals(id)
created_at              TEXT NOT NULL
PRIMARY KEY(requirement_id, revision)
```

`statement` and `details` are trimmed and must be non-empty.

### 7.10 `requirement_source_refs`

```text
requirement_id      TEXT NOT NULL
requirement_revision INTEGER NOT NULL
specification_id    TEXT NOT NULL REFERENCES specifications(id)
locator             TEXT NOT NULL
excerpt             TEXT NULL
PRIMARY KEY(requirement_id, requirement_revision, specification_id, locator)
FOREIGN KEY(requirement_id, requirement_revision)
  REFERENCES requirement_revisions(requirement_id, revision)
```

`locator` is an orchestrator-provided section name, line range, heading path, or other stable human-readable pointer. Taskledger does not interpret it.

### 7.10a `requirement_supersessions`

```text
superseded_requirement_id TEXT PRIMARY KEY REFERENCES requirements(id)
replacement_requirement_id TEXT NOT NULL REFERENCES requirements(id)
reason                    TEXT NOT NULL
created_by_principal_id   TEXT NOT NULL REFERENCES principals(id)
created_at                TEXT NOT NULL
```

The mapping records an explicit replacement relationship without rewriting the
prior requirement or its completed-task history.

### 7.11 `tasks`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
current_revision    INTEGER NOT NULL
state               TEXT NOT NULL CHECK (state IN (
                      'PLANNED','ASSIGNED','SUBMITTED','ACCEPTED','COMPLETED','CANCELLED'))
cancellation_reason TEXT NULL
created_at          TEXT NOT NULL
updated_at          TEXT NOT NULL
completed_at        TEXT NULL
cancelled_at        TEXT NULL
```

### 7.12 `task_revisions`

```text
task_id                 TEXT NOT NULL REFERENCES tasks(id)
revision                INTEGER NOT NULL
objective               TEXT NOT NULL
implementation_scope    TEXT NOT NULL
created_by_principal_id TEXT NOT NULL REFERENCES principals(id)
created_at              TEXT NOT NULL
PRIMARY KEY(task_id, revision)
```

### 7.13 `task_acceptance_criteria`

```text
id                  TEXT PRIMARY KEY
task_id             TEXT NOT NULL
task_revision       INTEGER NOT NULL
position            INTEGER NOT NULL
criterion_text      TEXT NOT NULL
FOREIGN KEY(task_id, task_revision)
  REFERENCES task_revisions(task_id, revision)
UNIQUE(task_id, task_revision, position)
```

### 7.13a `task_required_checks`

```text
task_id             TEXT NOT NULL
task_revision       INTEGER NOT NULL
position            INTEGER NOT NULL
command             TEXT NOT NULL
PRIMARY KEY(task_id, task_revision, position)
UNIQUE(task_id, task_revision, command)
FOREIGN KEY(task_id, task_revision)
  REFERENCES task_revisions(task_id, revision)
```

Commands are exact strings. A worker submission must contain evidence with the
same command and `exit_code=0` for every required check in the assigned revision.

### 7.14 `task_requirement_links`

```text
task_id             TEXT NOT NULL
task_revision       INTEGER NOT NULL
requirement_id      TEXT NOT NULL REFERENCES requirements(id)
PRIMARY KEY(task_id, task_revision, requirement_id)
FOREIGN KEY(task_id, task_revision)
  REFERENCES task_revisions(task_id, revision)
```

Links are versioned with the task definition. They point to a stable requirement identity; worker assignment context snapshots the current requirement revision.

### 7.15 `task_dependencies`

```text
task_id             TEXT NOT NULL
task_revision       INTEGER NOT NULL
depends_on_task_id  TEXT NOT NULL REFERENCES tasks(id)
PRIMARY KEY(task_id, task_revision, depends_on_task_id)
FOREIGN KEY(task_id, task_revision)
  REFERENCES task_revisions(task_id, revision)
CHECK(task_id <> depends_on_task_id)
```

### 7.16 `plan_validations`

```text
id                      TEXT PRIMARY KEY
project_id              TEXT NOT NULL REFERENCES projects(id)
fingerprint             TEXT NOT NULL
succeeded               INTEGER NOT NULL CHECK (succeeded IN (0,1))
diagnostics_json        TEXT NOT NULL
specification_snapshot_json TEXT NOT NULL
created_by_principal_id TEXT NOT NULL REFERENCES principals(id)
created_at              TEXT NOT NULL
```

The latest successful validation is current only when its fingerprint equals the freshly calculated plan fingerprint and no review/recovery gate exists.

### 7.17 `assignments`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
task_id             TEXT NOT NULL REFERENCES tasks(id)
task_revision       INTEGER NOT NULL
attempt_number      INTEGER NOT NULL
worker_profile      TEXT NOT NULL CHECK (worker_profile IN ('routine','complex'))
state               TEXT NOT NULL CHECK (state IN (
                      'PREPARING','ACTIVE','CLOSED','REVOKED','UNCERTAIN'))
base_commit_oid     TEXT NOT NULL
branch_name         TEXT NOT NULL
worktree_path       TEXT NOT NULL
context_json        TEXT NOT NULL
created_at          TEXT NOT NULL
activated_at        TEXT NULL
closed_at           TEXT NULL
revoked_at          TEXT NULL
revocation_reason   TEXT NULL
UNIQUE(task_id, attempt_number)
```

A partial unique index enforces at most one assignment in `PREPARING` or `ACTIVE` for a task.

### 7.18 `worker_questions`

```text
id                      TEXT PRIMARY KEY
assignment_id           TEXT NOT NULL REFERENCES assignments(id)
body                    TEXT NOT NULL
is_blocking             INTEGER NOT NULL CHECK (is_blocking IN (0,1))
state                   TEXT NOT NULL CHECK (state IN ('OPEN','ANSWERED'))
blocker_id              TEXT NULL
answer                  TEXT NULL
asked_at                TEXT NOT NULL
answered_by_principal_id TEXT NULL REFERENCES principals(id)
answered_at             TEXT NULL
```

A blocking question creates a linked open blocker in the same transaction.

### 7.19 `follow_up_proposals`

```text
id                  TEXT PRIMARY KEY
assignment_id       TEXT NOT NULL REFERENCES assignments(id)
submission_id       TEXT NULL
body                TEXT NOT NULL
state               TEXT NOT NULL CHECK (state IN ('PROPOSED','ACKNOWLEDGED','DISMISSED'))
created_at          TEXT NOT NULL
reviewed_at         TEXT NULL
reviewed_by_principal_id TEXT NULL REFERENCES principals(id)
```

Acknowledging or dismissing a proposal does not alter the plan.

### 7.20 `submissions`

```text
id                      TEXT PRIMARY KEY
project_id              TEXT NOT NULL REFERENCES projects(id)
assignment_id           TEXT NOT NULL REFERENCES assignments(id)
sequence                INTEGER NOT NULL
state                   TEXT NOT NULL CHECK (state IN (
                          'PENDING','BLOCKED','REJECTED','ACCEPTED','SUPERSEDED'))
summary                 TEXT NOT NULL
head_commit_oid         TEXT NOT NULL
changed_files_json      TEXT NOT NULL
evidence_json           TEXT NOT NULL
risks_json              TEXT NOT NULL
unresolved_questions_json TEXT NOT NULL
follow_up_work_json     TEXT NOT NULL
submitted_by_principal_id TEXT NOT NULL REFERENCES principals(id)
submitted_at            TEXT NOT NULL
resolved_at             TEXT NULL
UNIQUE(assignment_id, sequence)
```

`changed_files_json` is generated from Git. Other JSON fields are validated against the contracts in Section 14. A blocking risk or unresolved question is associated with a durable blocker by creating a `SUBMISSION`-scoped blocker whose `scope_id` is this submission ID in the same transaction. Gate calculations use whether that blocker is open, not the immutable original `blocking` flag after the orchestrator has explicitly resolved it. Submission follow-up items create `follow_up_proposals` rows with this submission ID in that transaction.

### 7.21 `submission_verifications`

```text
id                      TEXT PRIMARY KEY
submission_id           TEXT NOT NULL REFERENCES submissions(id)
verifier_principal_id   TEXT NOT NULL REFERENCES principals(id)
outcome                 TEXT NOT NULL CHECK (outcome IN ('ACCEPTED','REJECTED','BLOCKED'))
criterion_results_json  TEXT NOT NULL
behavior_matches_intent INTEGER NOT NULL CHECK (behavior_matches_intent IN (0,1))
required_evidence_present INTEGER NOT NULL CHECK (required_evidence_present IN (0,1))
blocking_issues_remaining INTEGER NOT NULL CHECK (blocking_issues_remaining IN (0,1))
corrections             TEXT NULL
notes                   TEXT NOT NULL
blocker_id              TEXT NULL REFERENCES blockers(id)
created_at              TEXT NOT NULL
```

`blocker_id` is required for a `BLOCKED` outcome and null for `ACCEPTED` or `REJECTED`. Multiple blocked decisions may exist for one unchanged submission. At most one accepted decision may exist.

### 7.22 `integration_attempts`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
task_id             TEXT NOT NULL REFERENCES tasks(id)
submission_id       TEXT NOT NULL REFERENCES submissions(id)
state               TEXT NOT NULL CHECK (state IN (
                      'STARTED','SUCCEEDED','FAILED','UNCERTAIN','INVALIDATED'))
canonical_branch    TEXT NOT NULL
canonical_before_oid TEXT NOT NULL
accepted_head_oid   TEXT NOT NULL
canonical_after_oid TEXT NULL
error_json          TEXT NULL
started_at          TEXT NOT NULL
finished_at         TEXT NULL
invalidated_at      TEXT NULL
```

The current valid integration for a task is its latest `SUCCEEDED` attempt that has not been invalidated and whose `canonical_after_oid` remains reachable from the canonical branch.

### 7.23 `requirement_verifications`

```text
id                      TEXT PRIMARY KEY
project_id              TEXT NOT NULL REFERENCES projects(id)
requirement_id          TEXT NOT NULL REFERENCES requirements(id)
requirement_revision    INTEGER NOT NULL
plan_fingerprint        TEXT NOT NULL
canonical_branch        TEXT NOT NULL
specification_snapshot_json TEXT NOT NULL
task_snapshot_json      TEXT NOT NULL
evidence_json           TEXT NOT NULL
notes                   TEXT NOT NULL
state                   TEXT NOT NULL CHECK (state IN ('CURRENT','INVALIDATED'))
verified_by_principal_id TEXT NOT NULL REFERENCES principals(id)
verified_at             TEXT NOT NULL
invalidated_at          TEXT NULL
invalidation_reason     TEXT NULL
```

A partial unique index permits one `CURRENT` verification per requirement.

### 7.24 `blockers`

```text
id                      TEXT PRIMARY KEY
project_id              TEXT NOT NULL REFERENCES projects(id)
category                TEXT NOT NULL CHECK (category IN (
                          'MISSING_PRODUCT_DECISION','AMBIGUOUS_REQUIREMENT',
                          'EXTERNAL_DEPENDENCY','REPOSITORY_STATE',
                          'VERIFICATION_FAILURE','SPECIFICATION_STATE',
                          'INTERRUPTED_OPERATION'))
scope_type              TEXT NOT NULL CHECK (scope_type IN (
                          'PROJECT','SPECIFICATION','REQUIREMENT','TASK',
                          'ASSIGNMENT','SUBMISSION','INTEGRATION','OPERATION'))
scope_id                TEXT NULL
description             TEXT NOT NULL
state                   TEXT NOT NULL CHECK (state IN ('OPEN','RESOLVED'))
created_by_principal_id TEXT NULL REFERENCES principals(id)
created_by_system       INTEGER NOT NULL CHECK (created_by_system IN (0,1))
created_at              TEXT NOT NULL
resolution              TEXT NULL
resolved_by_principal_id TEXT NULL REFERENCES principals(id)
resolved_at             TEXT NULL
```

Application validation requires `scope_id` for every scope except `PROJECT`.

### 7.25 `operations`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL REFERENCES projects(id)
kind                TEXT NOT NULL CHECK (kind IN (
                      'ASSIGNMENT_PREPARATION','SUBMISSION_CHECKPOINT','INTEGRATION'))
entity_type         TEXT NOT NULL
entity_id           TEXT NOT NULL
state               TEXT NOT NULL CHECK (state IN ('STARTED','SUCCEEDED','FAILED','UNCERTAIN'))
expected_state_json TEXT NOT NULL
result_json         TEXT NULL
started_at          TEXT NOT NULL
finished_at         TEXT NULL
```

A partial unique index enforces at most one `STARTED` operation per project. This deliberately serializes Git-mutating Taskledger operations and keeps recovery simple.

### 7.26 `audit_events`

```text
sequence                INTEGER PRIMARY KEY AUTOINCREMENT
project_id              TEXT NOT NULL REFERENCES projects(id)
principal_id            TEXT NULL REFERENCES principals(id)
event_type              TEXT NOT NULL
entity_type             TEXT NOT NULL
entity_id               TEXT NOT NULL
payload_json            TEXT NOT NULL
created_at              TEXT NOT NULL
```

The audit table is diagnostic history, not an event-sourced authority. Current relational tables remain authoritative.

---

## 8. Authentication and Authorization

### 8.1 Token generation

Tokens are 32 cryptographically random bytes generated with `secrets.token_bytes(32)` and encoded using URL-safe base64 without padding. SQLite stores only `sha256(token)`.

### 8.2 Orchestrator credential

Project initialization creates the orchestrator principal and credential. The plaintext token is written to:

```text
<repository>/.taskledger/credentials/<project-id>.orchestrator-token
```

The CLI automatically uses this file when an orchestrator command resolves the project. `--token` and `TASKLEDGER_TOKEN` may override it for automation.

V1 does not provide orchestrator-token rotation. Protect and back up this file as local project state. Worker-token rotation remains available because a worker credential is deliberately handed to another execution context.

### 8.3 Worker credential

Assignment creation generates a worker principal and token scoped to that assignment. The plaintext token is written only to the owner-only assignment credential file; model-visible command results return the path, never the secret. The orchestrator passes only the path to the worker execution context.

Worker authentication resolves the project and assignment entirely from the principal record. A worker cannot use `--project` to broaden its scope.

### 8.4 Authorization matrix

| Operation group | Orchestrator | Worker |
|---|---:|---:|
| Read complete project/recovery state | Yes | No |
| Read own assignment context | Yes | Yes, own assignment only |
| Register/review specifications | Yes | No |
| Create/revise/retire requirements | Yes | No |
| Create/revise/cancel/reopen tasks | Yes | No |
| Modify links/dependencies | Yes | No |
| Validate plan | Yes | No |
| Assign/revoke work | Yes | No |
| Ask assignment question | No | Yes, own assignment |
| Answer question | Yes | No |
| Create assignment/task blocker | Yes | Yes, own assignment/task |
| Resolve blocker | Yes | No |
| Submit work | No | Yes, own active assignment |
| Verify submission | Yes | No |
| Integrate accepted work | Yes | No |
| Verify requirement | Yes | No |
| Change canonical branch | Yes | No |
| Complete project | Yes | No |

Every service method repeats authorization checks; CLI command separation alone is not considered enforcement.

---

## 9. CLI Contract

### 9.1 General form

```text
taskledger <resource> <action> [identity flags] [--input <file|->] [--token <token>]
```

- `--input -` reads a JSON object from standard input.
- Commands with small scalar inputs may also expose named flags, but their internal request model is identical.
- Output is exactly one JSON object on standard output.
- Diagnostic logging goes to standard error only when `--verbose` is set and must never include credentials.

### 9.2 Success envelope

```json
{
  "ok": true,
  "command": "task.assign",
  "data": {},
  "warnings": []
}
```

### 9.3 Error envelope

```json
{
  "ok": false,
  "command": "task.assign",
  "error": {
    "code": "DEPENDENCIES_UNSATISFIED",
    "message": "Task cannot be assigned until all dependencies are integrated.",
    "details": {},
    "allowed_actions": []
  }
}
```

### 9.4 Exit codes

| Exit | Meaning |
|---:|---|
| 0 | Success |
| 2 | Invalid request or structural validation failure |
| 3 | Valid request rejected by current ledger state |
| 4 | Authentication or authorization failure |
| 5 | Git/repository precondition failure with known safe state |
| 6 | Recovery required because state is uncertain |
| 70 | Unexpected internal error; operation journal determines recovery state |

### 9.5 Project resolution

Orchestrator commands resolve a project in this order:

1. explicit `--project <uuid>`;
2. repository containing the current working directory, matched by canonical root/common Git directory;
3. otherwise fail with `PROJECT_REQUIRED`.

Worker commands resolve only from the worker credential.

---

## 10. Complete Command Surface

The command surface below is the full v1 product interface. No interactive UI is required.

### 10.1 Project commands

#### `taskledger project init --repo <path>`

Without `--confirm-branch`, performs read-only detection and returns:

```json
{
  "repository_root": "/absolute/path",
  "detected_branch": "feat/new-feature",
  "has_commits": true,
  "ledger_directory": "/absolute/repository/.taskledger",
  "ledger_directory_ignored": true,
  "confirmation_required": true
}
```

It creates no project or ledger directory.

If the repository root is already registered, either invocation form succeeds
idempotently and returns the existing `project_id`, canonical branch,
`already_initialized: true`, and `next_action: "project.recover"`. The caller
continues that project rather than creating a second ledger or ledger home.

#### `taskledger project init --repo <path> --confirm-branch <exact-name>`

Repeats detection. The provided value must exactly equal the current symbolic branch. It creates the project, registers the canonical branch, generates the orchestrator credential, and returns project identity and credential location.

For the repository-local default, confirmation also requires `.taskledger/` to
be covered by Git ignore rules. Failure returns
`LEDGER_DIRECTORY_NOT_IGNORED` without creating the directory.

An unborn symbolic branch may be confirmed. Initialization reports
`initial_commit_required: true`; project state loading and planning remain available,
while assignment and completion return `INITIAL_COMMIT_REQUIRED` until the first
commit exists on the canonical branch.

Detached HEAD, bare repository, missing Git, or mismatch fails without partial project creation.

#### `taskledger project show`

Returns project identity, effective phase, canonical branch/ref, current plan validity, pending reviews, blockers, and progress summary.

#### `taskledger project recover`

Loads and reconciles the complete project document described in Section 21.
Its command name does not itself mean that damage or an interrupted operation
exists; actual recovery work is present only when the returned unresolved
operations list is non-empty.

#### `taskledger project resume`

Returns a compact, preflighted projection of current authoritative state for
routine same-session use. Input is `{}` or `{"cursor":"sha256"}`. A matching
cursor returns a minimal `not_modified` response; a missing or nonmatching
cursor returns a fresh compact snapshot. The cursor is an optimization hint,
not an event-sourcing contract. Any unresolved operation sets
`full_recovery_required: true`. New sessions and callers that lost their
baseline use `project recover`.

#### `taskledger project set-canonical-branch`

Input:

```json
{
  "new_branch": "release/next",
  "confirm_branch": "release/next"
}
```

Preconditions:

- exact confirmation matches;
- branch exists locally;
- no `STARTED` or `UNCERTAIN` Git operation;
- no task is `ASSIGNED`, `SUBMITTED`, or `ACCEPTED`;
- every current successful integration for a completed task is reachable from the new branch.

On success, changes the branch and invalidates project completion. A requirement verification remains current when all of its own required integrations remain reachable from the new branch; the stored prior branch name remains historical verification context. The command does not assume or create the branch.

#### `taskledger project complete`

Runs all Section 19 completion checks. On success records current canonical HEAD,
then performs the safe best-effort cleanup in Section 16.4. On failure returns
every known unmet condition.

#### `taskledger project cleanup`

Input is `{}`. Requires a currently completed project and no unresolved Git
operation. Idempotently retries the safe assignment-worktree cleanup in Section
16.4 and returns removed, already-absent, and skipped worktrees. It never deletes
assignment branches.

### 10.2 Specification commands

#### `taskledger spec register`

Input:

```json
{
  "relative_path": "docs/product-spec.md"
}
```

The resolved path must name a present, readable regular file and remain inside the repository root after symlink resolution. Before planning, the present revision becomes approved immediately. After planning begins, registration creates a pending `ADDED` review.

#### `taskledger spec check`

Rechecks every active specification, records newly observed revisions, and returns pending reviews. It is idempotent: an already-recorded pending content hash does not create duplicates.

#### `taskledger spec review`

Input:

```json
{
  "review_id": "uuid",
  "resolution": "APPROVE_REVISION",
  "affected_requirement_ids": ["uuid"],
  "affected_task_ids": ["uuid"],
  "no_existing_items_affected": false,
  "summary": "Requirement R changed and Task T must be revised."
}
```

Rules:

- caller is orchestrator;
- all affected IDs belong to the project;
- the affected-item assertion is exclusive: either at least one affected ID is supplied with `no_existing_items_affected=false`, or both affected lists are empty with `no_existing_items_affected=true`;
- affected requirements must be active and affected tasks must be current non-cancelled tasks in this project;
- `APPROVE_REVISION` requires a present readable revision;
- `RETIRE_SPECIFICATION` is allowed for an intentionally removed specification;
- affected requirement verifications are invalidated;
- requirements linked to affected tasks are also invalidated;
- active specification baseline is updated or the specification is retired;
- the review becomes completed;
- the plan fingerprint changes and must be validated again.

Execution-sensitive commands remain blocked until all pending reviews are completed.

### 10.3 Requirement commands

#### `taskledger requirement create`

Input:

```json
{
  "statement": "Observable behavior",
  "details": "Independent explanatory detail",
  "implementation_required": true,
  "sources": [
    {
      "specification_id": "uuid",
      "locator": "Section 9 / PS-001",
      "excerpt": "optional short excerpt"
    }
  ]
}
```

Creates revision 1. The first requirement creation sets `planning_started_at` if absent.

#### `taskledger requirement update`

Input includes the requirement ID, a complete replacement definition, sources, and dispositions for every active assignment whose task links to the requirement:

```json
{
  "requirement_id": "uuid",
  "statement": "Revised behavior",
  "details": "Revised detail",
  "implementation_required": true,
  "sources": [],
  "assignment_dispositions": [
    {"assignment_id": "uuid", "action": "CONTINUE"}
  ]
}
```

The update inserts a new immutable revision. For `CONTINUE`, Taskledger refreshes the assignment context and records the orchestrator decision. For `REVOKE`, it applies the state-specific assignment-revocation rules in Section 10.5; an already submitted task therefore remains `SUBMITTED`. Missing dispositions cause rejection.

Current requirement verification is invalidated.

#### `taskledger requirement retire`

Requires a reason and dispositions for affected active assignments, using the same `CONTINUE` and state-specific `REVOKE` behavior as requirement update. The historical requirement remains. Current verification is invalidated. Unfinished tasks linked only to retired requirements will cause plan validation errors until revised or cancelled; completed tasks remain historical evidence.

#### `taskledger requirement supersede`

Atomically creates a replacement requirement, retires the named active
requirement, and records the replacement relationship and reason. Active
assignments require the same explicit dispositions as update and retire.
Completed tasks linked only to the superseded requirement remain historical and
are excluded from current-plan validation; unfinished tasks must be revised or
cancelled.

#### `taskledger requirement list`

Supports filters `complete`, `remaining`, `blocked`, `ready`, and `all`. It returns source and task traceability. `ready` is derived from the same plan, blocker, task, and integration preconditions used by verification; it is not a persisted fourth status.

#### `taskledger requirement verify`

Input:

```json
{
  "requirement_id": "uuid",
  "evidence": [
    {"label": "Behavior check", "details": "What was checked and the result"}
  ],
  "notes": "Why the observable requirement is satisfied"
}
```

The service applies Section 18 verification checks and inserts a current verification record.

#### `taskledger requirement invalidate`

Input names an active requirement with a current verification and supplies a non-empty reason. The orchestrator explicitly sets that verification to `INVALIDATED`, records the reason and audit event, and invalidates project completion. It does not revise the requirement or plan.

### 10.4 Task and plan commands

#### `taskledger task create`

Input is one complete task definition:

```json
{
  "objective": "Implement bounded behavior",
  "implementation_scope": "Files and behavior in scope; exclusions where relevant",
  "acceptance_criteria": ["Criterion one", "Criterion two"],
  "required_checks": ["python -m unittest"],
  "requirement_ids": ["uuid"],
  "dependency_task_ids": ["uuid"]
}
```

Creation is atomic with criteria, links, and dependencies. It creates revision 1, state `PLANNED`, and starts planning if necessary.

`required_checks` is optional. Its unique, non-empty command strings become part
of the task revision and submission evidence contract.

#### `taskledger task update`

Replaces the complete current task definition and creates the next immutable revision. If an active assignment exists, input must include `assignment_action` equal to `CONTINUE` or `REVOKE`. `CONTINUE` updates the assignment’s task revision and context; `REVOKE` revokes it and returns the task to planned.

This command is permitted only from `PLANNED` or `ASSIGNED`. A task in `SUBMITTED` must first receive an explicit verification or cancellation disposition, and a task in `ACCEPTED` must first be integrated or explicitly cancelled; reviewed repository state cannot be silently redefined. A `COMPLETED` or `CANCELLED` task must use `task reopen`.

#### `taskledger task cancel`

Requires a reason. Active assignments require explicit revocation. A task with a `PENDING` or `BLOCKED` submission requires `submission_action="SUPERSEDE"`, which preserves the submission and changes its state to `SUPERSEDED`. Completed tasks must be reopened before cancellation. Cancellation preserves history and invalidates the plan.

#### `taskledger task reopen`

Permitted from `COMPLETED` or `CANCELLED`. Creates a new revision from a complete replacement definition, sets state `PLANNED`, invalidates dependent requirement verifications and project completion, and retains prior integrations as history.

#### Task split workflow

V1 does not need a special split algorithm. A split is performed with ordinary orchestrator operations:

1. create replacement tasks;
2. revise dependent tasks to point to the replacements;
3. cancel the original with reason `split into <ids>`; and
4. validate the resulting plan.

The plan remains invalid throughout the intermediate edits, so no new assignment can occur against a partial split. This fulfills task splitting without introducing dependency-rewrite heuristics.

#### `taskledger task list`

Supports `all`, `eligible`, `active`, `submitted`, `accepted`, `completed`, `blocked`, and `cancelled` filters. Eligible output is stably sorted by creation time and ID; this ordering is not a priority recommendation.

#### `taskledger plan validate`

Runs the validation algorithm in Section 12, stores success or failure with all diagnostics, and returns the new fingerprint. Only the orchestrator may invoke it.

#### `taskledger plan apply`

Creates a non-empty batch of new requirements and tasks in one transaction.
Request-local `ref`, `requirement_refs`, and `dependency_refs` values allow tasks
to link to entities created in the same request; existing IDs may also be used.
Unknown, duplicate, and self references reject the whole batch. The result maps
local refs to durable IDs. Validation remains a separate explicit operation.

### 10.5 Assignment commands

#### `taskledger assignment create`

Input:

```json
{"task_id": "uuid", "worker_profile": "routine"}
```

`worker_profile` is required and accepts only `routine` or `complex`. It records
the orchestrator's capability-routing decision; Taskledger does not resolve the
profile to a model. The consuming repository's named-agent configuration owns
that mapping.

The service re-evaluates eligibility in the same operation; a stale eligible-list result is never trusted. On success it prepares a Git worktree, creates the worker principal/credential, activates the assignment, sets task state `ASSIGNED`, and returns:

```json
{
  "assignment_id": "uuid",
  "task_id": "uuid",
  "attempt_number": 1,
  "worker_profile": "routine",
  "worktree_path": "/...",
  "branch_name": "taskledger/p-<project-uuid>/t-<task-uuid>/a-1",
  "base_commit_oid": "...",
  "worker_token_path": "/.../worker-token",
  "context_hash": "sha256"
}
```

The plaintext credential and full assignment snapshot are intentionally absent
from model-visible output. The worker reads the owner-only credential file and
retrieves the persisted assignment snapshot with `worker context`. Conditional
refreshes independently validate the immutable context hash and the dynamic
questions/blockers/submissions hash.

The immutable snapshot also includes active registered specifications as stable
IDs and repository-relative paths. It does not duplicate their contents. Worker
instructions treat these files as frozen unless the task scope explicitly owns
the exact specification edit, preventing incidental documentation maintenance
from repeatedly invalidating an otherwise unchanged execution plan.

The orchestrator selects the task and worker profile. Taskledger does not select
or substitute either one automatically. The profile is included in worker
context, compact/full recovery projections, submission review context, and the
assignment-activation audit event.

After two rejected verifications produced by routine assignments for a task,
assignment creation rejects `routine` and requires `complex`.

Routing uses a routine-preferred capability gate. A task is routine when one
approach is frozen, ownership is explicit, acceptance is deterministic, failure
is local and reversible, and no unresolved product, visual, security,
data-integrity, concurrency, destructive, or compatibility judgment remains.
Task size, file count, and mechanical migration are not complexity signals.
Complex is required when the worker must choose architecture or state ownership,
reconcile shared contracts or implementations, interpret intent, or decide
high-consequence or weakly testable behavior. Vague tasks are clarified before
routing.

Cross-file contracts, sibling-entity rollouts, and generated-client consumer
migrations remain routine when the approach, ownership, and executable checks
are frozen. Plans separate a complex semantic core from mechanical followers.
Manual cache/state reconciliation remains complex unless an executable behavior
matrix removes the judgment. The primary performs the final integrated audit;
it creates correction assignments only for defects that audit actually finds.

Before creating concurrent assignments, the orchestrator derives prospective
write sets from the task scopes and current repository. Shared configuration,
registries, generated contracts, central exports or cleanup, verification
scripts, and application entry points are included even when not the task's
primary output. Parallel tasks must have independent writes and behavioral
assumptions; otherwise one task owns the shared surface and the others are
sequenced after integration. Profile selection does not imply parallel safety.

#### `taskledger assignment revoke`

Revokes worker credentials, preserves the branch/worktree, and records the reason.

- If the task is `ASSIGNED`, the assignment becomes `REVOKED` and the task returns to `PLANNED`.
- If the task is `SUBMITTED`, the assignment becomes `REVOKED` and the task remains `SUBMITTED`; its immutable submission remains available for orchestrator verification. A later rejection returns the task to `PLANNED`, because no active worker remains for correction.
- Revocation is rejected for a closed assignment or a task in `ACCEPTED` or `COMPLETED`.
- Cancelling a task with a pending or blocked submission requires an explicit submission disposition and marks that submission `SUPERSEDED`.

#### `taskledger assignment rotate-token`

Orchestrator-only recovery command. Deactivates the old hash and writes a new scoped token to the returned owner-only path without exposing plaintext in JSON or changing assignment state.

### 10.6 Orchestrator verification and integration commands

#### `taskledger submission review-context`

Input is `{"submission_id":"uuid"}`. This orchestrator-only read model returns
the exact base/submitted/canonical OIDs, object-existence status, bounded
diffstat and complete changed-file metadata, current criterion IDs/text, linked
requirements, immutable worker claims, relevant blockers, and prior outcomes.
It never includes an unbounded patch, executes worker evidence, or substitutes
for independent exact-OID inspection and testing.

#### `taskledger submission verify`

Input:

```json
{
  "submission_id": "uuid",
  "outcome": "ACCEPTED",
  "criterion_results": [
    {
      "criterion_id": "uuid",
      "satisfied": true,
      "evidence": "Test output or inspection result"
    }
  ],
  "behavior_matches_intent": true,
  "required_evidence_present": true,
  "blocking_issues_remaining": false,
  "corrections": null,
  "notes": "Independent verification summary"
}
```

Rules are in Section 15. An accepted result immediately attempts integration. The response distinguishes:

- `accepted: true, integrated: true, task_state: COMPLETED`; or
- `accepted: true, integrated: false, task_state: ACCEPTED, blocker_id: ...`.

Rejected and blocked outcomes never attempt integration.

For a `BLOCKED` outcome, the request additionally supplies exactly one of:

```json
{"blocker_id": "existing-open-blocker-uuid"}
```

or:

```json
{"blocker": {"category": "EXTERNAL_DEPENDENCY", "description": "What prevents a safe decision"}}
```

Taskledger stores the referenced or newly created blocker ID on the verification record. Other outcomes reject both fields.

#### `taskledger task integrate`

Retries integration for a task in `ACCEPTED` after corrective action. It always uses the exact accepted submission commit.

### 10.7 Blocker, question, and proposal commands

- `taskledger blocker create` — orchestrator can create any valid scope.
- `taskledger blocker resolve` — orchestrator supplies resolution text; no implicit close.
- `taskledger blocker list` — returns open or historical blockers.
- `taskledger question answer` — records an answer. A linked blocker remains open unless the same request explicitly includes `resolve_blocker: true` and resolution text.
- `taskledger follow-up list` — lists proposals.
- `taskledger follow-up review` — marks a proposal acknowledged or dismissed; does not create tasks.

### 10.8 Worker commands

All worker commands require a worker token and operate only on its assignment.

#### `taskledger worker context`

Returns the assignment context, current task/requirement revisions, active
registered specification IDs and repository-relative paths, answers, open
blockers, and submission state. It exposes no unrelated plan state or complete
specification contents.

#### `taskledger worker question`

Input:

```json
{"body": "Question text", "blocking": true}
```

A blocking question creates an open assignment blocker.
Its deterministic category is `MISSING_PRODUCT_DECISION`; a worker that needs another category uses `worker blocker` explicitly.

#### `taskledger worker blocker`

Input includes one allowed category, a description, and `scope_type` equal to `ASSIGNMENT` or `TASK`. Taskledger derives the corresponding scope ID from the authenticated assignment. Any other scope or mismatched ID is rejected with `WORKER_SCOPE_VIOLATION`.

#### `taskledger worker follow-up`

Records a proposal without changing the plan.

#### `taskledger worker submit`

Input:

```json
{
  "summary": "Implementation summary",
  "evidence": [
    {
      "label": "Tests",
      "details": "All relevant tests passed",
      "command": "python -m unittest",
      "exit_code": 0,
      "artifact_path": null
    }
  ],
  "risks": [
    {"description": "Known risk", "blocking": false, "blocker_category": null}
  ],
  "unresolved_questions": [
    {"body": "Remaining question", "blocking": false, "blocker_category": null}
  ],
  "follow_up_work": [
    {"body": "Possible later work"}
  ]
}
```

`blocker_category` must be one of the supported product blocker categories when `blocking=true` and must be null when `blocking=false`. Taskledger creates one durable `SUBMISSION`-scoped blocker for each blocking item. Each follow-up item also creates a durable `follow_up_proposals` row; neither action changes the plan.

Before checkpointing repository state, the service verifies that evidence names
every required-check command from the assigned task revision with `exit_code=0`.
Worker evidence remains a claim; the orchestrator independently reruns checks.

The exact submission procedure is in Section 14.

### 10.9 Recovery-operation commands

These orchestrator-only commands are available only for an operation already reported by `project recover`:

- `taskledger operation adopt-success` records a proven Git success when the supplied operation ID and current repository OIDs exactly match the proof conditions reported by recovery.
- `taskledger operation mark-failed` closes an operation as failed only after Taskledger proves that the repository is back in the expected pre-operation state.

Neither command accepts a generic force flag. A request that does not satisfy the operation-specific proof conditions is rejected and leaves the recovery gate in place.

---

## 11. Command Gate Matrix

Preflight calculates gates before each command.

| Command category | Pending spec review | Invalid plan | Recovery/repository uncertainty |
|---|---:|---:|---:|
| Read/show/recover/list | Allowed | Allowed | Allowed |
| Specification review and checks | Allowed | Allowed | Allowed unless Git inspection itself is impossible |
| Requirement/task/plan maintenance | Allowed | Allowed | Allowed when no Git mutation is required |
| Worker context/question/blocker/follow-up | Allowed | Allowed | Allowed |
| Worker submission recording | Allowed | Allowed | Blocked |
| Reject or block a submission | Allowed | Allowed | Allowed |
| Create new assignment | Blocked | Blocked | Blocked |
| Accept submission | Blocked | Blocked | Blocked |
| Integrate accepted work | Blocked | Blocked | Blocked |
| Verify requirement | Blocked | Blocked | Blocked |
| Change canonical branch | Blocked | Allowed if no active work | Blocked |
| Complete project | Blocked | Blocked | Blocked |

This preserves worker output during a review while preventing it from becoming accepted product state prematurely.

Commands generally allowed during recovery must not revise, cancel, revoke, or otherwise change an entity named by a `STARTED` or `UNCERTAIN` operation when doing so could change that operation's proof conditions. Such a request returns `RECOVERY_REQUIRED` with the permitted recovery actions.

---

## 12. Plan Fingerprint and Validation

### 12.1 Fingerprint input

The current plan fingerprint is SHA-256 over canonical JSON containing:

- active specification IDs, relative paths, and approved revision hashes;
- every active requirement ID, current revision number, statement, details, implementation-required flag, and sorted source references;
- every non-cancelled task ID, current revision number, objective, scope, sorted acceptance criteria, sorted requirement links, and sorted dependency IDs.

Execution state such as assignment, submission, or completion is not included. A task reopening creates a new revision and therefore changes the fingerprint.

The byte contract is fixed: entity arrays are ordered by full UUID; source references by `(specification_id, locator, excerpt-or-empty)`; acceptance criteria by numeric `position`; and requirement links and dependencies by full UUID. The object is encoded as UTF-8 JSON with sorted object keys, compact separators, and non-ASCII text emitted directly (`ensure_ascii=False`). SHA-256 is calculated over those exact bytes. Criteria are never alphabetized by their prose. Golden fixtures must lock this behavior.

### 12.2 Validation algorithm

Validation returns all diagnostics that can be found in one pass.

1. Reject zero active requirements.
2. For each active requirement:
   - require non-empty statement/details;
   - require at least one source reference;
   - require each source specification active and approved;
   - if implementation is required, require at least one current non-cancelled task link;
   - if implementation is not required, reject any current task link as contradictory.
3. For each non-cancelled current-plan task (excluding completed historical tasks linked only to retired requirements):
   - require non-empty objective and scope;
   - require at least one non-empty criterion;
   - require at least one linked active requirement;
   - reject links to retired/missing requirements;
   - reject dependencies outside the project, to itself, or to cancelled tasks.
4. Run depth-first or Kahn topological cycle detection over current task definitions.
5. Confirm no pending specification review.
6. Confirm every active assignment references the current task revision; otherwise emit an assignment-disposition error.
7. Compute the fingerprint and store the complete result.

A successful validation does not claim semantic completeness. It records that the orchestrator’s plan satisfies enforceable structure.

### 12.3 Current-plan test

The plan is current only when:

- the latest successful validation fingerprint equals the freshly calculated fingerprint;
- no pending specification review exists; and
- no recovery gate invalidates repository assumptions.

---

## 13. Task Eligibility and Assignment Context

### 13.1 Eligibility calculation

A task is eligible when all conditions are true:

1. task state is `PLANNED`;
2. current plan is valid;
3. no pending spec review or uncertain operation exists;
4. no project-level open blocker exists;
5. no open blocker targets the task;
6. no open blocker targets an active requirement linked by the task’s current revision;
7. every current dependency task is `COMPLETED`;
8. each dependency has a current successful integration reachable from the canonical branch; and
9. no active assignment exists for the task.

The calculation is repeated inside assignment creation after obtaining the durable operation slot.

### 13.2 Assignment context document

`context_json` is an immutable snapshot generated at assignment activation, except when the orchestrator explicitly chooses `CONTINUE` after a task or linked requirement revision. It contains:

```json
{
  "assignment": {
    "id": "...",
    "attempt_number": 1,
    "base_commit_oid": "...",
    "branch_name": "...",
    "worktree_path": "..."
  },
  "task": {
    "id": "...",
    "revision": 1,
    "objective": "...",
    "implementation_scope": "...",
    "acceptance_criteria": []
  },
  "requirements": [
    {
      "id": "...",
      "revision": 1,
      "statement": "...",
      "details": "...",
      "sources": []
    }
  ],
  "questions": []
}
```

It excludes unrelated tasks, global progress, other workers, orchestrator credentials, and plan mutation operations.

---

## 14. Git Assignment and Submission Design

### 14.1 Git invocation rules

All Git commands use `subprocess.run()` with an argument array, `shell=False`, captured output, a bounded timeout, and:

```text
GIT_TERMINAL_PROMPT=0
```

Paths are passed as separate arguments. User-supplied branch names are validated with `git check-ref-format --branch`. No command interpolates shell text.

### 14.2 Repository initialization inspection

Commands:

```text
git -C <path> rev-parse --show-toplevel
git -C <root> rev-parse --git-common-dir
git -C <root> rev-parse --is-bare-repository
git -C <root> symbolic-ref --quiet --short HEAD
```

The symbolic-ref result is the only detected current branch. Remote defaults are never consulted.

### 14.3 Assignment branch and worktree

Assignment creation uses the current commit at:

```text
refs/heads/<canonical_branch>
```

Branch names use full stable IDs so collision fallback logic is unnecessary:

```text
taskledger/p-<project-uuid>/t-<task-uuid>/a-<attempt>
```

Before creation, Taskledger proves that both the generated branch and assignment worktree path are unused. It never adopts, resets, or deletes an existing ref or worktree on collision.

Worktree creation:

```text
git -C <repository_root> worktree add \
  -b <assignment_branch> \
  <external_worktree_path> \
  <canonical_head_oid>
```

The exact base OID is recorded before the command. Assignment activation occurs only after Git confirms:

- the branch resolves to the expected base OID;
- the worktree is registered;
- the worktree is on the generated branch; and
- no in-progress Git operation exists.

### 14.4 Submission payload validation

Worker-provided arrays use these shapes:

```text
evidence item:
  label: non-empty string
  details: non-empty string
  command: optional string
  exit_code: optional integer
  artifact_path: optional repository-relative string

risk:
  description: non-empty string
  blocking: boolean
  blocker_category: supported category when blocking, otherwise null

unresolved question:
  body: non-empty string
  blocking: boolean
  blocker_category: supported category when blocking, otherwise null

follow-up:
  body: non-empty string
```

At least one evidence item is required.

### 14.5 Submission checkpoint procedure

`worker submit` performs:

1. Authenticate the active worker principal.
2. Require task state `ASSIGNED` and assignment state `ACTIVE`.
3. Confirm assignment task revision still matches the assigned context.
4. Confirm no earlier submission for this assignment is `PENDING` or `BLOCKED`. Open blockers or unresolved questions do not prevent recording completed work; each blocking risk or question in the payload creates an open submission-scoped blocker in the submission transaction.
5. Require that no project operation is `STARTED` or `UNCERTAIN`, then acquire the project Git operation slot with kind `SUBMISSION_CHECKPOINT`.
6. Confirm the worktree exists, is registered, is on the assignment branch, and has no merge/rebase/cherry-pick/revert/bisect operation in progress.
7. Run `git add -A` in the assignment worktree.
8. If the index contains changes, create a checkpoint commit:

   ```text
   git -c commit.gpgSign=false \
       commit -m "taskledger: submit <task-id> attempt <n>"
   ```

   The commit inherits the consuming repository's normal Git author and
   committer identity. Taskledger does not substitute a synthetic identity.
   Normal repository hooks run. Missing Git identity or a hook failure leaves
   the assignment active and returns a known Git error.
9. Require a clean worktree after the checkpoint.
10. Resolve the branch HEAD OID.
11. Require the recorded assignment base to be an ancestor of HEAD, require HEAD to differ from that base, and require a non-empty tree diff. Rebased or unrelated history is rejected rather than interpreted.
12. Generate `changed_files_json` from:

   ```text
   git diff --name-status -z <base_oid> <head_oid>
   ```

13. Insert the immutable submission, its submission-scoped blockers, its `follow_up_proposals` rows, set task state `SUBMITTED`, and write audit events in one ledger transaction.
14. Mark the operation succeeded.

If the worker has already created commits, they remain part of the branch. The checkpoint commit captures only remaining uncommitted changes. The accepted unit is the exact final HEAD.

### 14.6 Submission immutability

The submission records a commit OID, not a mutable branch name. Verification and integration use that OID. A later branch commit cannot alter the submitted object. After rejection, a corrected submission may use a new commit or may reuse the same commit with materially different evidence or claim data. It is always a new immutable submission record.

---

## 15. Submission Verification State Machine

### 15.1 Preconditions common to all outcomes

- caller is an active orchestrator principal;
- submission belongs to the project;
- task state is `SUBMITTED`;
- submission state is `PENDING` or `BLOCKED`;
- every current acceptance criterion has exactly one criterion result;
- no unknown or duplicate criterion ID appears;
- the verifier principal is not the worker principal that submitted the work;
- the submitted commit still exists in the repository object database.

The orchestrator may perform tests or inspection outside Taskledger. Taskledger records the result; it does not execute semantic verification automatically.

### 15.2 Accepted outcome

Acceptance requires:

- every criterion result `satisfied=true`;
- `behavior_matches_intent=true`;
- `required_evidence_present=true`;
- `blocking_issues_remaining=false`;
- no open project, linked-requirement, task, assignment, or submission blocker that the shared blocker-propagation function applies;
- no open blocker created for a blocking worker or submission question;
- current plan valid;
- no pending specification review or recovery gate.

Transaction effects before Git integration:

1. insert accepted verification;
2. set submission state `ACCEPTED`;
3. set task state `ACCEPTED`;
4. close worker authority for further submissions by deactivating its principal; set an `ACTIVE` assignment to `CLOSED`, while an already `REVOKED` assignment remains `REVOKED`; and
5. record audit events.

Then integration is attempted synchronously. If integration fails safely, acceptance remains valid and task remains `ACCEPTED`.

### 15.3 Rejected outcome

Rejection requires:

- at least one unsatisfied criterion or a false behavioral/evidence assertion; and
- non-empty `corrections`.

Effects:

- insert rejected verification;
- set submission state `REJECTED`;
- after the first rejection of a routine assignment, set task state `ASSIGNED`
  and leave the worker credential active;
- after the task's second routine-worker rejection, revoke the current routine assignment, deactivate its
  worker credential, set the task to `PLANNED`, and require `complex` next;
- for a complex assignment, set task state `ASSIGNED` and leave the worker
  credential active unless the assignment was already revoked;
- otherwise, for a revoked assignment, set task state `PLANNED` so a new assignment is required; and
- preserve all prior submissions.

### 15.4 Blocked outcome

Blocked requires `blocking_issues_remaining=true` and either an existing open blocker ID or both a blocker description and category in the request. An existing blocker must belong to this project and apply to the submission under the shared propagation function. Effects:

- insert blocked verification;
- set submission state `BLOCKED`;
- leave task state `SUBMITTED`;
- create or link an open blocker; and
- leave worker credential active unless orchestrator separately revokes it.

After explicit resolution, the same submission may be re-verified only if its commit is unchanged. If implementation changes are required, the orchestrator rejects it so the worker can create a new submission.

---

## 16. Integration Protocol

### 16.1 Preconditions

Integration uses the exact accepted submission commit and requires:

- task state `ACCEPTED`;
- accepted submission/verification exists;
- no pending review or invalid plan;
- no blocker applied by the shared blocker-propagation function at project, linked-requirement, task, assignment, submission, integration, or operation scope;
- no `STARTED` or `UNCERTAIN` operation;
- repository root exists;
- canonical branch still resolves locally;
- the repository root working tree is currently checked out on the canonical branch;
- the canonical working tree and index are clean;
- no Git operation is in progress in the canonical worktree; and
- the accepted commit exists.

Requiring the registered repository root to be on the canonical branch avoids unsafe direct ref updates to a branch checked out elsewhere. If the user has temporarily checked out another branch or has local changes, Taskledger creates a repository-state blocker and asks the orchestrator to restore the required state. It never stashes, discards, or switches user work automatically.

### 16.2 Idempotent already-integrated case

Before merging, run:

```text
git merge-base --is-ancestor <accepted_oid> refs/heads/<canonical_branch>
```

If true, record a successful integration adopting the current canonical HEAD. This supports safe recovery or an explicit external integration without duplicating work.

### 16.3 Merge procedure

1. Acquire operation slot kind `INTEGRATION` and record expected canonical before OID.
2. Insert `integration_attempts` state `STARTED`.
3. Run in repository root:

   ```text
   git -c commit.gpgSign=false \
       -c merge.autoStash=false \
       merge --no-ff --no-edit --no-gpg-sign <accepted_oid>
   ```

   The merge commit inherits the consuming repository's normal Git author and
   committer identity. Taskledger provenance remains in the commit message and
   durable ledger rather than a synthetic Git identity.

4. On success:
   - resolve canonical after OID;
   - prove accepted OID is an ancestor of after OID;
   - update integration attempt to `SUCCEEDED`;
   - set task state `COMPLETED` and `completed_at`;
   - invalidate any older requirement verification snapshots that referenced a prior revision of this task;
   - mark operation succeeded.
5. On command failure:
   - if a merge is in progress, run `git merge --abort`;
   - prove canonical HEAD equals the before OID, no merge state remains, the index has no staged delta, and the canonical worktree is clean;
   - update attempt `FAILED` with command output;
   - create an open `REPOSITORY_STATE` blocker scoped to the integration;
   - leave task `ACCEPTED`; and
   - mark the operation failed in a known state.
6. If rollback cannot be proven, mark both operation and attempt `UNCERTAIN`, create an `INTERRUPTED_OPERATION` blocker, and block all further Git-mutating actions.

Taskledger never resolves conflicts or edits files during integration.

### 16.4 Finalized assignment worktree cleanup

Assignment worktrees remain available through implementation, submission review,
integration, and requirement verification. After project completion is durably
recorded, Taskledger attempts cleanup for every assignment whose state is final.
For each worktree it proves that:

- the stored path resolves beneath that project's Taskledger-managed worktree directory;
- Git still registers the path to the managed repository;
- the checked-out branch is the assignment's recorded branch;
- the worktree is clean; and
- no merge, rebase, cherry-pick, revert, or bisect operation is in progress.

It then runs ordinary `git worktree remove <path>` without `--force`. Failure or
any failed proof leaves the worktree intact and returns a typed skip reason.
Missing paths are treated as already absent, so completion cleanup and explicit
`project cleanup` retries are idempotent. Taskledger never deletes the assignment
branch or its commits; accepted/integrated history therefore remains reachable,
inspectable, and revertible after the duplicate checkout is removed.

Cleanup does not require an operation-journal row because it does not alter the
retained ref or ledger authority and its post-interruption states are safe to
reinspect: registered existing worktrees can be retried, missing paths are done,
and existing unregistered paths are retained for manual inspection.

### 16.5 Integration validity reconciliation

At project show, recovery, requirement verification, dependency eligibility, and completion, Taskledger checks each completed task’s latest successful integration:

```text
git merge-base --is-ancestor <canonical_after_oid> refs/heads/<canonical_branch>
```

If false:

- mark that integration `INVALIDATED`;
- set the task back to `ACCEPTED` if its accepted submission still exists;
- invalidate requirement verifications that reference it;
- invalidate project completion; and
- create a repository-state blocker.

This is a factual repository reconciliation, not a semantic decision.

---

## 17. Specification Detection and Review Protocol

### 17.1 Preflight detection

Before command gating, Taskledger reads each active specification path and computes SHA-256 over raw file bytes.

For each specification:

- same file state and same hash as active or pending revision -> no action;
- present with different hash -> create `MODIFIED` or `RESTORED` revision/review;
- missing -> create `MISSING` revision/review;
- read failure -> create `UNREADABLE` revision/review.

Before `planning_started_at`, a new present hash may be approved automatically and audited. Missing/unreadable state creates a blocker because there is no valid desired-product source.

After planning begins, any distinct observation creates an immutable revision and an execution gate. If that specification already has a pending review, the existing review is retargeted to the latest observation as specified in Section 7.7; a second pending review is not created.

### 17.2 Review completion

Review completion does not edit requirements or tasks automatically. It records the orchestrator’s affected-item determination, approves the revision or retires the specification, invalidates affected requirement verification, and changes the plan fingerprint.

The orchestrator then uses normal requirement/task commands and must validate the plan before execution-sensitive actions resume.

### 17.3 Multiple simultaneous changes

Each specification has its own review. The project remains review-gated until no `PENDING` review exists. A repeated `spec check` is idempotent.

---

## 18. Requirement Verification and Progress Algorithm

### 18.1 Verification preconditions

A requirement can be verified only when:

- it is active and the requested revision is current;
- current plan is valid;
- no pending specification review or recovery gate exists;
- no project- or requirement-level blocker is open;
- evidence is non-empty; and
- caller is orchestrator.

For `implementation_required=true`:

- at least one current non-cancelled task links to the requirement;
- every such task is `COMPLETED`;
- every such task has a current valid integration reachable from the canonical branch; and
- no linked task blocker is open.

For `implementation_required=false`:

- no current non-cancelled task links to the requirement; and
- direct evidence is recorded.

### 18.2 Verification snapshot

A successful verification stores:

- current requirement revision;
- current plan fingerprint;
- canonical branch;
- active specification IDs/revision hashes;
- for each covering task: task ID, revision, accepted submission OID, and canonical integration OID;
- evidence and notes.

The plan fingerprint, full active-specification snapshot, and canonical branch are historical evidence describing what the orchestrator considered. They are not, by themselves, global invalidation keys.

### 18.3 Current verification test

A verification counts as current only when all of these requirement-scoped conditions hold:

1. the requirement is active and its current revision equals the verified revision;
2. the current non-cancelled covering-task set equals the stored task snapshot;
3. every covering task has the same task revision and accepted submission OID recorded in the snapshot;
4. every recorded integration remains valid and reachable from the current canonical branch;
5. no completed specification review marked this requirement or one of its covering tasks affected after `verified_at`; and
6. the verification has not been explicitly invalidated.

An unrelated plan edit, an approved specification change asserted to affect no existing item, or a canonical branch-name change that preserves all required integration reachability does not invalidate the verification. Services proactively set `INVALIDATED` for known scoped causes, and status calculation recomputes these conditions to protect against missed invalidation.

### 18.4 Requirement status algorithm

For each active requirement:

1. If a current valid verification exists -> `COMPLETE`.
2. Else if a pending spec review exists -> `BLOCKED` with review reason.
3. Else if an open project/requirement/relevant task blocker exists -> `BLOCKED`.
4. Else -> `REMAINING`.

A task-backed remaining requirement can include a substate:

- `AWAITING_TASKS`;
- `AWAITING_SUBMISSION_VERIFICATION`;
- `AWAITING_INTEGRATION`; or
- `AWAITING_REQUIREMENT_VERIFICATION`.

These substates are derived operational detail, not separate persisted requirement states.

### 18.5 Progress result

```json
{
  "requirements": {
    "total": 12,
    "complete": 7,
    "remaining": 3,
    "blocked": 2,
    "awaiting_final_verification": 1
  },
  "tasks": {
    "eligible": 2,
    "active_implementation": 1,
    "awaiting_submission_verification": 1,
    "accepted_awaiting_integration": 0,
    "completed": 8,
    "cancelled": 1
  },
  "open_blockers": 2
}
```

No task-derived percentage is labeled as project completion. If a percentage is emitted, it is `complete active requirements / total active requirements`.

---

## 19. Project Completion Algorithm

`project complete` performs one fresh reconciliation and then evaluates all failures without short-circuiting:

1. project has at least one active requirement;
2. no pending specification review;
3. current plan is valid;
4. every active requirement has a current verification;
5. every non-cancelled current task linked to an active requirement is `COMPLETED`;
6. every completed task’s integration remains reachable from the canonical branch;
7. no task is `ASSIGNED`, `SUBMITTED`, or `ACCEPTED`;
8. no assignment is `PREPARING`, `ACTIVE`, or `UNCERTAIN`;
9. no open blocker exists at any scope;
10. no operation is `STARTED` or `UNCERTAIN`;
11. no submitted state lacks a terminal verification/integration disposition; and
12. canonical branch resolves to a commit.

If any fail, completion is rejected with an array of typed reasons and relevant IDs.

On success, in one transaction:

- set project lifecycle `COMPLETED`;
- set `completed_at`;
- record `completion_head_oid` equal to current canonical HEAD; and
- insert audit event.

After that transaction commits, run Section 16.4 cleanup. Cleanup failures do not
roll back or misreport project completion; they are returned as skipped worktrees
that can be retried explicitly.

Before any later command reports the project as completed, it rechecks:

- specification hashes;
- canonical branch identity and head ancestry for all integrations;
- no new open blocker; and
- requirement verification validity.

A failed recheck sets lifecycle back to `ACTIVE`, clears `completed_at` and `completion_head_oid`, preserves the prior completion timestamp, branch, head, and invalidation reason in an audit event, and reports the reason. Every mutation that can break a completion condition runs this same completion reconciliation before returning.

---

## 20. Operation Journal and Crash Recovery

Git and SQLite cannot participate in one atomic transaction. The `operations` table provides a durable intent/result protocol.

### 20.1 Operation lifecycle

For each Git-mutating action:

1. `BEGIN IMMEDIATE`.
2. Ensure no `STARTED` or `UNCERTAIN` operation exists for the project.
3. Insert `STARTED` with exact expected Git state and intended entity transition.
4. Commit.
5. Execute Git commands.
6. Inspect actual state.
7. In a new transaction, write ledger result and mark operation `SUCCEEDED`, `FAILED`, or `UNCERTAIN`.

A second Git-mutating command cannot start while any operation for the project is `STARTED` or `UNCERTAIN`.

### 20.2 Startup reconciliation

Before normal command execution, Taskledger checks `STARTED` operations.

#### Assignment preparation

Expected state includes base OID, branch name, and worktree path.

- neither branch nor worktree exists -> mark failed and roll back `PREPARING` assignment;
- branch and worktree exist, branch points to base, worktree registered and clean -> deterministically activate assignment;
- any partial or contradictory state -> mark uncertain and create recovery blocker.

#### Submission checkpoint

Expected state includes pre-checkpoint HEAD and worktree.

- worktree clean and HEAD unchanged -> operation failed with no submission; worker may retry;
- worktree clean and HEAD advanced, but no submission record -> mark the operation failed with result `CHECKPOINT_CREATED_WITHOUT_SUBMISSION`; leave the task assigned and instruct the worker to rerun submit, which adopts the current exact HEAD after validation;
- dirty or in-progress Git state -> uncertain blocker.

Taskledger does not infer worker summary/evidence that was not durably stored. A rerun must provide that payload again.

#### Integration

Expected state includes canonical before OID and accepted OID.

- canonical HEAD equals before OID and no merge state -> mark failed/no change;
- canonical HEAD has exactly a merge commit whose first parent is before OID and accepted OID is reachable from another parent -> deterministically finalize success;
- accepted OID is already an ancestor of canonical and no conflicting integration record exists -> deterministically adopt success;
- merge state exists or ancestry does not prove one result -> mark uncertain and require explicit recovery.

### 20.3 Explicit recovery actions

`project recover` lists allowed actions for uncertain operations. Orchestrator-only recovery commands are:

- `operation adopt-success` — permitted only when Taskledger presents proof conditions and the supplied current OID matches;
- `operation mark-failed` — permitted only after the orchestrator restores and confirms the expected pre-operation Git state;
- `assignment revoke` — preserves partial branch/worktree;
- `task integrate` — retries a known accepted state.

No generic “force continue” command exists.

---

## 21. Recovery Snapshot Contract

`project recover` returns one self-contained JSON document suitable as the first input to a new orchestrator session.

```json
{
  "project": {
    "id": "...",
    "repository_root": "...",
    "canonical_branch": "feat/new-feature",
    "canonical_head_oid": "...",
    "effective_phase": "EXECUTION",
    "completed": false
  },
  "specifications": {
    "active": [],
    "pending_reviews": []
  },
  "plan": {
    "valid": true,
    "fingerprint": "...",
    "diagnostics": []
  },
  "progress": {},
  "requirements": {
    "complete": [],
    "remaining": [],
    "blocked": [],
    "awaiting_verification": []
  },
  "tasks": {
    "eligible": [],
    "active_assignments": [],
    "awaiting_submission_verification": [],
    "accepted_awaiting_integration": [],
    "completed": [],
    "cancelled": []
  },
  "questions": {
    "open": []
  },
  "follow_up_proposals": {
    "unreviewed": []
  },
  "blockers": {
    "open": []
  },
  "operations": {
    "started_or_uncertain": []
  },
  "completion": {
    "eligible": false,
    "blocking_reasons": []
  },
  "allowed_next_actions": []
}
```

The snapshot must include enough task and requirement text to act without conversation history, while worker-scoped `worker context` remains limited.
Completed, accepted, submitted, and revoked task entries include their latest
assignment branch and recorded worktree path. The path remains historical context
even when post-completion cleanup has removed that checkout; the retained branch
is the durable inspection and recovery reference.

---

## 22. Blocker Propagation

Blocker records retain one explicit scope. Query logic propagates effect as follows:

- `PROJECT` blocks every new assignment, acceptance, integration, requirement verification, and completion.
- `SPECIFICATION` contributes to the project review/recovery gate.
- `REQUIREMENT` blocks that requirement and every planned task currently linked to it.
- `TASK` blocks assignment/acceptance/integration for that task and marks linked incomplete requirements blocked.
- `ASSIGNMENT` blocks submission acceptance for that assignment’s task.
- `SUBMISSION` blocks acceptance of that submission.
- `INTEGRATION` blocks task completion and downstream dependencies.
- `OPERATION` blocks Git-mutating actions project-wide when uncertain.

Resolving a blocker changes only the blocker record. The requested action must still recheck its factual preconditions; resolving a blocker does not make an unsafe state valid.

One pure domain function, `blocking_reasons(operation, entity_context)`, implements blocker effects for every service:

| Operation | Blocking scopes |
|---|---|
| Assign task | project, linked requirement, task, uncertain operation/repository gate |
| Accept submission | project, linked requirement, task, assignment, submission, uncertain operation/repository gate |
| Integrate task | project, linked requirement, task, assignment, submission, integration, uncertain operation/repository gate |
| Verify requirement | project, requirement, every current covering task and integration, uncertain operation/repository gate |
| Complete project | every open blocker at every scope |

System reconciliation reuses an existing open blocker having the same project, category, scope type, scope ID, and deterministic description. Repeated preflight checks must not create duplicate blockers for the same unresolved fact. The check and any insert occur in one `BEGIN IMMEDIATE` transaction.

---

## 23. Concurrency and Idempotency

### 23.1 Ledger concurrency

SQLite WAL allows simultaneous readers. `BEGIN IMMEDIATE` serializes writers. A 5-second busy timeout is sufficient for expected low-volume local use.

### 23.2 Git concurrency

Only one Taskledger Git-mutating operation per project may be `STARTED`. This serializes worktree creation, submission checkpoints, and integration. Workers may concurrently edit and run tests in different worktrees; only ledger/Git checkpoints serialize.

### 23.3 Idempotent commands

- `spec check` does not duplicate an already-pending observed revision.
- `plan validate` may create repeated validation records, but current state is unchanged for the same fingerprint.
- `worker submit` identifies an exact retry by assignment ID, submitted HEAD OID, and SHA-256 of the canonical claim payload. An exact retry returns the existing submission. A second submission is rejected while one is `PENDING` or `BLOCKED`. After rejection, the same commit may be submitted again only with materially different claim/evidence data; otherwise the rejected submission is returned with its corrections.
- `task integrate` treats an accepted commit already reachable from canonical as success.
- blocker resolution on an already resolved blocker returns the existing resolution without changing it, unless the supplied resolution conflicts.
- project completion on an already-current completed project returns the existing completion record.

### 23.4 Optimistic checks

Every mutation includes expected current revision/state in the service query. SQL updates use state/revision predicates and require one affected row. A mismatch returns `STALE_STATE` rather than overwriting another command’s result.

---

## 24. Error Codes

The implementation must provide stable machine-readable codes at least for:

```text
AUTHENTICATION_FAILED
AUTHORIZATION_DENIED
PROJECT_NOT_FOUND
PROJECT_REQUIRED
NOT_A_GIT_REPOSITORY
BARE_REPOSITORY_UNSUPPORTED
DETACHED_HEAD
BRANCH_CONFIRMATION_MISMATCH
CANONICAL_BRANCH_NOT_FOUND
CANONICAL_BRANCH_NOT_CHECKED_OUT
CANONICAL_WORKTREE_DIRTY
LEDGER_DIRECTORY_NOT_IGNORED
LEDGER_STORAGE_UNAVAILABLE
INITIAL_COMMIT_REQUIRED
SPECIFICATION_NOT_FOUND
SPECIFICATION_OUTSIDE_REPOSITORY
SPECIFICATION_REVIEW_REQUIRED
PLAN_INVALID
PLAN_VALIDATION_FAILED
REQUIREMENT_NOT_FOUND
REQUIREMENT_NOT_ACTIVE
TASK_NOT_FOUND
TASK_STATE_INVALID
TASK_REVISION_STALE
DEPENDENCIES_UNSATISFIED
TASK_BLOCKED
ASSIGNMENT_ALREADY_ACTIVE
ASSIGNMENT_NOT_ACTIVE
WORKER_SCOPE_VIOLATION
SUBMISSION_NOT_FOUND
SUBMISSION_STATE_INVALID
SUBMISSION_EVIDENCE_REQUIRED
SUBMISSION_ALREADY_PENDING
VERIFICATION_CRITERIA_INCOMPLETE
VERIFICATION_OUTCOME_INVALID
INTEGRATION_FAILED_SAFE
INTEGRATION_STATE_UNCERTAIN
RECOVERY_REQUIRED
BLOCKER_OPEN
REQUIREMENT_NOT_READY_FOR_VERIFICATION
PROJECT_NOT_READY_FOR_COMPLETION
GIT_COMMAND_FAILED
STALE_STATE
INTERNAL_ERROR
```

Errors must include entity IDs and allowed corrective actions when known.

---

## 25. Security and Integrity Controls

1. Never invoke a shell for Git commands.
2. Validate branch names with Git itself.
3. Resolve and constrain specification paths to the repository root.
4. Hash credentials and compare using `hmac.compare_digest`.
5. Redact tokens from logs, audit payloads, errors, and recovery output.
6. Set restrictive local file permissions where supported.
7. Enforce foreign keys and check constraints.
8. Validate every JSON input field and reject unknown fields by default.
9. Bound input sizes: individual text fields 1 MiB, specification files 50 MiB, JSON payload 10 MiB. These are safety limits, not product quotas; exceeding them returns an actionable error and does not truncate.
10. Use deterministic Git timeouts and return command/stdout/stderr excerpts with secrets redacted.
11. Never run worker-supplied command strings as part of Taskledger verification. Evidence commands are stored as text only.
12. Never auto-stash, auto-reset, force-push, delete assignment branches, discard worktrees with changes, force worktree removal, or resolve conflicts.
13. Back up the SQLite file before applying a schema migration that changes existing tables. Initial v1 migrations are additive.

The Git command timeout is configurable and defaults to 120 seconds. Migration backups use SQLite's backup API so a live WAL database is copied consistently. V1 is tested and supported on POSIX hosts with Git 2.20 or later; permission-setting calls remain conditional and must fail safely on unsupported filesystems.

---

## 26. Testing Strategy

### 26.1 Unit tests

Unit tests cover pure domain rules:

- all task and submission state transitions;
- requirement status derivation;
- blocker propagation;
- dependency satisfaction;
- cycle detection;
- plan fingerprint stability;
- plan invalidation rules;
- requirement verification validity;
- project completion checks;
- command gate matrix; and
- authorization matrix.

### 26.2 Database integration tests

Using a temporary SQLite database:

- migrations apply from empty state;
- foreign keys and unique partial indexes enforce invariants;
- requirement/task revision history is retained;
- concurrent state updates return stale-state errors;
- blocker resolution remains explicit;
- successful and failed plan validation records are correct;
- worker credential rotation invalidates old hashes; and
- completion invalidation is durable.

### 26.3 Git integration tests

Each test creates a temporary repository and invokes the real local Git executable.

Required cases:

1. initialization on `feat/new-feature` confirms that branch;
2. detached HEAD is rejected;
3. arbitrary valid branch names work;
4. assignment creates branch/worktree at exact canonical OID;
5. two unrelated tasks can have concurrent worktrees;
6. submission checkpoints uncommitted changes;
7. existing worker commits plus checkpoint produce one immutable submitted HEAD;
8. empty diff submission is rejected;
9. rejected submission allows corrected submission;
10. accepted commit merges into canonical branch;
11. merge conflict is aborted and canonical HEAD restored;
12. dirty canonical worktree blocks integration without alteration;
13. accepted commit already on canonical is adopted idempotently;
14. external canonical rewrite invalidates integration;
15. canonical branch change rejects a branch missing prior integrations;
16. crash reconciliation handles each operation proof case; and
17. user signing and autostash configuration cannot change Taskledger merge behavior.

### 26.4 End-to-end acceptance tests

Automated scenarios mirror every scenario in Product Specification Section 25. Each acceptance test interacts only through the public CLI and verifies both JSON output and final Git/SQLite state.

### 26.5 Failure injection

Tests inject termination or exceptions at each boundary:

- after operation `STARTED` but before Git;
- after Git worktree creation but before assignment activation;
- after checkpoint commit but before submission insert;
- after merge commit but before integration update;
- during merge conflict abort; and
- after ledger completion update before response serialization.

Recovery must produce a known result or an explicit uncertain-operation blocker; it may never report false completion.

---

## 27. Observability

No telemetry service is required.

The implementation provides:

- `--verbose` structured diagnostic lines on stderr;
- immutable `audit_events` for state transitions;
- stored Git command result details for failed/uncertain operations; and
- complete recovery output containing repository/database diagnostics.

---

## 28. Implementation Sequence

The implementation should proceed in these dependency-ordered slices.

### Slice 1 — Foundation

- package/CLI entry point;
- response/error envelopes;
- configuration and local filesystem paths;
- SQLite connection and migrations;
- IDs, clock, canonical JSON;
- project initialization and credentials;
- repository inspection.

### Slice 2 — Specifications, requirements, and planning

- specification registration/revisions/checks/reviews;
- requirement revision model and source refs;
- task revisions, criteria, links, dependencies;
- plan fingerprint and validation;
- project/recovery read model without assignments.

### Slice 3 — Assignment and worker scope

- task eligibility;
- operation journal;
- Git branch/worktree creation;
- worker principals and scoped commands;
- questions, blockers, and follow-up proposals.

### Slice 4 — Submission and verification

- Git checkpoint submission;
- immutable submission data;
- verification outcomes and corrections;
- worker continuation after rejection.

### Slice 5 — Integration and requirement completion

- merge protocol and safe rollback;
- integration validity reconciliation;
- requirement verification snapshots;
- requirement-based progress;
- project completion/invalidation.

### Slice 6 — Recovery hardening

- interrupted-operation reconciliation;
- failure injection tests;
- complete recovery snapshot;
- canonical branch change safeguards;
- security/error redaction review.

Each slice must include unit and integration tests before the next slice begins.

---

## 29. Requirements Traceability Matrix

| Product specification area | Technical sections |
|---|---|
| Project initialization and canonical branch | 4, 8, 9, 10.1, 14.2, 16 |
| Specification registration/change review | 7.5–7.7, 10.2, 11, 17 |
| Requirement management/traceability | 7.8–7.10, 10.3, 12, 18 |
| Planning validation | 7.16, 10.4, 12 |
| Task lifecycle/revision/split/cancel/reopen | 7.11–7.15, 10.4, 15, 16 |
| Dependencies and task eligibility | 12, 13, 16.5 |
| Assignment and limited worker context | 7.17, 8, 10.5, 13, 14.3 |
| Questions/blockers/follow-up work | 7.18–7.19, 7.24, 10.7–10.8, 22 |
| Submission contents and immutability | 7.20, 10.8, 14.4–14.6 |
| Independent verification | 7.21, 8.4, 10.6, 15 |
| Integration before completion | 7.22, 10.6, 16 |
| Recovery after interruption | 7.25, 20, 21 |
| Requirement-based progress | 7.23, 18 |
| Project completion | 18, 19 |
| Fail-safe behavior | 6, 16.3, 20, 23–25 |

---

## 30. Explicitly Excluded Implementation Work

The implementation must not include:

- LLM API calls;
- automatic requirement extraction;
- automatic task generation;
- automatic prioritization;
- automatic file-conflict prediction for parallel tasks;
- autonomous verification;
- hosted accounts or multi-user permissions;
- web UI;
- remote database;
- GitHub/GitLab pull requests or issue synchronization;
- CI execution or CI result ingestion;
- deployment;
- cloud backup/synchronization;
- worker process launching or model-provider integration;
- chat history storage;
- arbitrary plugin systems; or
- automatic conflict resolution.

The orchestrator may invoke workers by whatever external mechanism it already uses. Taskledger begins at assignment creation and records the durable handoff.

---

## 31. Definition of Done

Taskledger v1 is complete when:

1. all public commands in Section 10 exist and return the Section 9 envelopes;
2. the Section 7 schema and constraints are applied by migrations;
3. every product invariant is enforced by service and database tests;
4. every Product Specification Section 25 scenario passes through the public CLI;
5. a repository based on a non-main canonical branch completes end to end;
6. a worker credential cannot invoke any orchestrator operation;
7. a task cannot satisfy a dependency before integration;
8. a spec change blocks execution until explicit review and revalidation;
9. a failed integration leaves a known safe state or an explicit recovery gate;
10. recovery output is sufficient to continue from a new session without conversation history;
11. progress and completion are calculated from current requirement verification; and
12. no excluded implementation work in Section 30 is present.
