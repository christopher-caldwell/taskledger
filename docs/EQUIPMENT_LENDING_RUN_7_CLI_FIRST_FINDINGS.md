# Equipment Lending run 7: CLI-first lifecycle findings through reporting

## Purpose and scope

This report records every material setup, preparation, approval, materialization,
detached-launch, and reporting issue observed while launching Equipment Lending
benchmark run 7 with Taskledger's CLI-first lifecycle. It covers the interval
from fresh repository setup through the first live worker turn. It does not
evaluate later worker continuation, submission review, correction, integration,
final review, or completion because those stages had not yet occurred at the
reporting snapshot.

The two Taskledger correctness defects are analyzed in greater source-level
detail in
[`BUG_PREPARATION_STALE_SPECIFICATION_REVISION.md`](BUG_PREPARATION_STALE_SPECIFICATION_REVISION.md).

## Identities

Taskledger environment:

```text
Taskledger: 0.6.0
Taskledger source commit: c1453448436ec8d46d502eb9605876cca026e3fe
Python: 3.13.7
macOS: 26.3.1 (25D2128)
Codex protocol: codex-cli 0.153.4;app-server-v2-jsonrpc+experimental-dynamic-tools;taskledger-client-0.6.0
```

Benchmark repository:

```text
/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/trials/equipment-lending-run-7
branch: feat/equipment-lending-taskledger-run-7
starting OID: 554e2af1bd5f2cd82fdefbbe697d253119421980
```

Authoritative comparison source:

```text
/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/prompts/equipment-lending-run-5-taskledger.md
SHA-256: e62eb282c325efcb779c46e90f297af0226528a6dac1d49bddd53867b1e35760
```

Run-7 specification:

```text
docs/product-specification.md
SHA-256: 9fc22b36d810ab9e87db39bab6feadfa04ab886731d62b335c69aa6cda7e3928
```

The execution policy uses one worker slot, one Reviewer slot, sequential waves,
Astra/high for Task Creator and Reviewer, and Terra/xhigh for routine and complex
workers.

## Expected lifecycle

The intended CLI-first sequence was:

1. Create a clean isolated benchmark repository and deterministic infrastructure.
2. Run `taskledger project prepare --input taskledger-run-config.json`.
3. Preserve and present the immutable proposal.
4. Receive explicit approval of its exact proposal hash.
5. Run `taskledger project start --input -` with that preparation ID and hash.
6. Capture the execution run ID and leave the foreground Python controller
   running independently.
7. Use `project show`, `controller show`, and `controller report` for read-only
   monitoring.

The observed lifecycle required exceptions at steps 2 and 5. Those exceptions
must remain visible in any benchmark comparison.

## Finding 1: inaccurate initial preflight input

Classification: benchmark setup error, not a Taskledger defect.

The initial run configuration declared `/var/run/docker.sock` as a required
local input. That was not the applicable Docker socket path on this macOS host,
and `project prepare` correctly rejected the preflight before model dispatch.
The configuration was corrected to declare the actual required PostgreSQL
service at `127.0.0.1:55467` and the required ignored environment file.

Preserved response:

```text
review-evidence/taskledger-preparation-preflight-failure.json
SHA-256: 85d8905be10d483a13488fb3e3ff3cc5a19e5c8af308f5c37faecafb867bf81d
```

Recommended practice:

- Declare a Docker socket only when a worker check directly consumes that exact
  socket path.
- Prefer the concrete service host/port health check for Compose-provisioned
  dependencies.
- Keep platform-specific bootstrap inputs in benchmark configuration rather
  than product requirements.

## Finding 2: a known architecture convention was absent from the specification

Classification: specification/bootstrap completeness issue.

The first Astra/high Task Creator run stopped on an ambiguity about how RBV
relates to the named Identity subsystem. Earlier comparable Equipment Lending
runs had already approved the convention that RBV is contained within Identity
for persisted role policy. That convention was added to the run-7 specification
without changing product behavior.

Failed preparation:

```text
preparation ID: 87a0fac2-7d64-49c4-9774-57fac0bf20a3
planning run ID: 95ad1f0c-7e8f-4fdc-bf77-1e9d5b9edd37
proposal hash: sha256:a45da8abde3ace53edcd243af76e1776edf40a66c9c712cbc77bc43611ca84d6
state: FAILED / AMBIGUOUS_REQUIREMENT
```

Exact recorded usage:

```text
turns: 1
input tokens: 207,506
cached input tokens: 164,224
output tokens: 1,147
reasoning tokens: 175
known token subtotal: 208,653
accounting status: COMPLETE
```

Artifacts:

```text
review-evidence/taskledger-preparation-ambiguous.json
SHA-256: e687787a1d44dc2c842bcb0000b5528e825f12fbf3c3df2da562bdb40eb9fcd1

review-evidence/taskledger-preparation-ambiguous-report.json
SHA-256: 05b6c192cb19900e5665606b8143b0e89b29bda41dad070c14866502a2003349
```

Recommended practice: when a prior representative run established a mandatory
architecture convention needed to interpret the same workload, include it in
the authoritative benchmark specification before the first paid preparation.

## Finding 3: preparation stored a stale specification revision

Classification: Taskledger correctness and integrity defect.

After the RBV clarification, the active specification revision had hash
`9fc22b…3928`. The second Task Creator read that current file and generated an
R10 requirement explicitly requiring RBV inside Identity. Nevertheless, the
preparation stored the previous revision hash `a8a7da…9bda`.

Successful planning run:

```text
preparation ID: a688e9fe-6c0c-42e8-af2b-3eb4021b7f23
planning run ID: daab07b6-cb47-4d01-81b2-3b3a9f3c8ec8
original proposal hash: sha256:d08be6df712c5ceadc11744fae22f27e33a9a2882e8980ec05eac2165d0be300
state before approval: AWAITING_APPROVAL
requirements: 20
tasks/waves: 8 sequential
```

Exact successful-preparation usage:

```text
turns: 2
input tokens: 620,886
cached input tokens: 539,008
output tokens: 17,875
reasoning tokens: 829
known token subtotal: 638,761
accounting status: COMPLETE
```

Artifacts:

```text
review-evidence/taskledger-preparation.json
SHA-256: 5de3e493ff3f56d1e5f9716ecdd5b1f85769595b23706c2f810c3c1eb62f273f

review-evidence/taskledger-preparation-controller-show.json
SHA-256: eaf5c2e0c83a5820ce8f470fe6e3a99913c711adf6abb5e9505ee4991701f1df

review-evidence/taskledger-preparation-controller-report.json
SHA-256: f7f9434975c28d464e74f10399c4969effca2edf7bd58319fc20945340dde844
```

`project start` rejected the approved hash before model dispatch:

```json
{"command":"project.start","error":{"allowed_actions":[],"code":"PREPARATION_STALE","details":{},"message":"Specification changed after preparation."},"ok":false}
```

Root cause: `prepare_project_command` reads the specification row before
`service.preflight(project)`. Preflight can advance `active_revision_id`, but
the previously fetched SQLite row remains stale. Taskledger then stores the old
revision hash while `InitialPlanner` reads the current filesystem path.

The same ordering exists in `start_prepared_project`, creating a potential
false-acceptance path if start is the first command to discover a post-prepare
file change.

Required fix:

- Synchronize the specification before fetching its active revision.
- Re-query after synchronization in both prepare and start.
- Bind planner input to the selected immutable revision bytes, eliminating the
  remaining filesystem time-of-check/time-of-use window.
- Add changed-before-prepare, changed-before-start, unchanged-start, and
  mutation-during-planning regression cases.

## Finding 4: proposal validation and persistence disagree about source identity

Classification: Taskledger validation/materialization defect.

After the stale hash was rebound, `project start` passed that gate but failed
before creating any requirements, tasks, assignments, or execution run:

```json
{"command":"project.start","error":{"allowed_actions":[],"code":"INTERNAL_ERROR","details":{},"message":"Taskledger encountered an unexpected internal error."},"ok":false}
```

An isolated database copy revealed the actual exception:

```text
sqlite3.IntegrityError: UNIQUE constraint failed:
requirement_source_refs.requirement_id,
requirement_source_refs.requirement_revision,
requirement_source_refs.specification_id,
requirement_source_refs.locator
```

R20 cited two distinct excerpts from
`docs/product-specification.md#Deliverables and completion`. Proposal validation
accepted both. `materialized_plan` attached the same specification ID to both,
but the persistence primary key allows only one source per specification and
locator.

Preserved response:

```text
review-evidence/taskledger-start-materialization-internal-error.log
SHA-256: df1c3a8db5a9301d5172dcc75f6f2b83e3c81b9fbdffcf85eb3f1dee8d69a0db
```

Required fix:

- Define whether multiple excerpts at one locator are valid.
- Reject or normalize duplicate persistence identities during proposal
  validation, before approval.
- If multiple excerpts are valid, add stable source position or excerpt identity
  to persistence.
- Convert content-induced integrity errors into specific `LedgerError` results.
- Verify transaction rollback leaves no partially visible plan.

## Finding 5: internal errors are not diagnosable from the CLI

Classification: Taskledger diagnostic/operability defect.

The source-collision exception was reported only as `INTERNAL_ERROR` with empty
details. `--verbose` prints only the Python exception class, not a traceback,
correlation ID, failing entity, or retained diagnostic path.

Recommended fix:

- Map expected database/request integrity failures to public named errors.
- Retain a local diagnostic record with a correlation ID for unexpected errors.
- Let verbose local execution emit a safe traceback or diagnostic path without
  exposing credentials.
- Include the command phase, such as `PLAN_MATERIALIZATION`, in the error details.

## Authorized benchmark exceptions

The benchmark owner explicitly declined another Astra/high preparation and
authorized direct state correction. These are not supported recovery procedures.

First correction:

- Updated only
  `project_preparations.specification_hash` for preparation
  `a688e9fe-6c0c-42e8-af2b-3eb4021b7f23` from `a8a7da…9bda` to the already-active
  committed hash `9fc22b…3928`.
- Preserved proposal JSON, original proposal hash, starting OID, profiles,
  configuration, and usage at that step.

Second correction:

- Combined R20's two excerpts into one source entry at the identical locator.
- Preserved both excerpt texts, all requirement/task semantics, routing,
  acceptance criteria, checks, assumptions, and ambiguities.
- Recomputed the canonical exception proposal hash because proposal JSON changed.

Exception proposal hash:

```text
sha256:c25595fe84f4a5aba370fe350277459e954210e7f076403294affa0a3830c164
```

State snapshots:

```text
review-evidence/taskledger-before-preparation-rebind.sqlite3
SHA-256: b2d284c341c11e63b39b924510c987af0a0f1f9b023bf401ac424a920b72f61d

review-evidence/taskledger-after-spec-rebind-before-source-normalization.sqlite3
SHA-256: 4a04083679a30815be88e7afcad0e0fd235b857790b349119569ce642ea5ebb8
```

Before the live retry, an isolated copy successfully materialized 20
requirements and 8 tasks and passed `plan validate` with no diagnostics.

Benchmark consequence: run 7 is useful for controller behavior and economic
observation, but its approval/materialization path is no longer a pristine
measurement of the unmodified v0.6.0 happy path. Reports must retain both the
original approved hash and the exception hash.

## Finding 6: detached foreground-equivalent launching lacked a safe documented pattern

Classification: launcher/environment friction; only the missing documented
operational pattern is a Taskledger product concern.

Observed attempts:

1. A plain `nohup ... &` child did not survive the invoking command host and
   produced no Taskledger output.
2. A `launchctl submit` job started with a restricted environment and could not
   resolve `taskledger` by name. The wrapper was corrected to use the resolved
   absolute executable path.
3. `launchctl submit` automatically restarted the short-lived stale-preparation
   failure, producing 89 identical error lines before the exact launch job was
   removed. These were repeated process attempts, not 89 Taskledger state
   transitions.
4. A detached `tmux` session invoking a small shell wrapper with absolute paths
   worked and did not respawn failed or completed commands.

Repeated-start log:

```text
review-evidence/taskledger-start-preparation-stale.log
lines: 89
SHA-256: 479d840f38990d2f031414a0f191f7f6a273de970e781e57a0def3ff20960e86
```

Recommended fix/documentation:

- Publish one supported detached foreground-equivalent recipe for macOS and
  Linux that preserves signal delivery and exact PID ownership.
- Require absolute executable/repository/log paths or explicitly preserve a
  validated PATH.
- Warn against respawning service managers for one-shot `project start` unless
  restart policy is disabled.
- Document `tmux`/`screen` as acceptable operator-owned wrappers if Taskledger
  intentionally remains foreground-only.
- Continue recommending SIGINT to the recorded controller PID for durable pause.

## Successful execution start

After the authorized state corrections, the same normal CLI entry point
materialized the plan and emitted:

```text
Taskledger execution run:
cd5b0c1e-43c7-49b3-805d-09f1febf716f

Ctrl-C requests a safe pause.
```

Startup evidence:

```text
execution run ID: cd5b0c1e-43c7-49b3-805d-09f1febf716f
recorded PID: 60939
mode: PROJECT
state: RUNNING
pause reason: none
wave 1: ACTIVE, complex worker, gpt-5.6-terra/xhigh
waves 2-8: QUEUED
```

The controller process and detached tmux session were both alive at startup
verification. No manual worker or Reviewer command was issued.

## Finding 7: execution reporting omits earlier preparation attempts

Classification: Taskledger benchmark-accounting/report aggregation gap.

`controller report` for the execution run identifies only the preparation run
that directly produced the approved proposal:

```text
preparation_run_id: daab07b6-cb47-4d01-81b2-3b3a9f3c8ec8
preparation_known_tokens: 638,761
post_approval_execution_known_tokens: 0 at the first active-turn snapshot
whole-taskledger-run known_total_tokens: 638,761 at that snapshot
```

The earlier failed Task Creator run remains queryable and has complete usage:

```text
run ID: 95ad1f0c-7e8f-4fdc-bf77-1e9d5b9edd37
state: FAILED / AMBIGUOUS_REQUIREMENT
known token subtotal: 208,653
accounting status: COMPLETE
```

Therefore, exact preparation usage from fresh benchmark setup through execution
approval is:

```text
208,653 + 638,761 = 847,414 known tokens
```

This is an exact sum of two complete Taskledger reports, not an estimate.

The execution report's `whole_taskledger_run` value undercounts the economic
cost of reaching execution by 208,653 tokens because it follows only the direct
`preparation_run_id`. A representative benchmark must aggregate every
preparation attempt belonging to the trial, including terminal ambiguity and
failed planning runs.

Recommended fix:

- Add project/trial-level attempt lineage or an explicit benchmark-run identity.
- Report all preparation attempts leading to the selected execution, with each
  run's state and exact usage.
- Keep direct-preparation usage as a separate subtotal.
- Never merge missing or incomplete usage into zero; preserve accounting status
  per attempt and for the aggregate.
- Include preflight failures that used no model as zero-token attempts with their
  failure reason, rather than silently omitting the attempt.

## First live report snapshot

The read-only `controller show` and deterministic `controller report` snapshot
showed:

```text
controller state: RUNNING
active worker sessions: 1
active target: wave 1 complex
queued targets: waves 2-8
max workers: 1
max reviewers: 1
recorded pauses: 0
runtime failures: 0
uncertain outcomes: 0
open blockers: 0
integration attempts: 0
reviews: 0
completed execution turns: 0
unresolved active turns: 1
execution accounting status: UNRECONCILED_ACTIVE_TURN
```

`UNRECONCILED_ACTIVE_TURN`, zero execution tokens, and incomplete accounting are
expected while the first provider turn is still active. They are not failures.
Provider detail was deliberately not requested, so `NOT_REQUESTED` coverage and
zero provider-activity counters are also expected.

No conclusion can yet be drawn about automatic continuation, check execution,
Reviewer dispatch, correction routing, integration, final review, requirement
verification, completion, or cleanup. Those paths require later terminal events.

## Prioritized remediation

1. Fix stale specification selection and bind planning to immutable bytes. This
   is the highest integrity risk because it can cause both false rejection and
   false acceptance.
2. Align proposal validation, public schema, and source-reference persistence.
3. Replace content-induced generic internal errors with named validation errors
   and retain safe diagnostics for unexpected failures.
4. Add multi-attempt preparation lineage and economic aggregation to controller
   reporting.
5. Document a non-respawning detached-launch recipe with signal/PID semantics.
6. Strengthen benchmark setup review so platform-specific preflight inputs and
   prior-run architecture conventions are settled before paid preparation.

## Integrity notes for final benchmark reporting

Any final run-7 report must preserve and disclose:

- both preparation runs and their exact usage;
- the preflight failure;
- original proposal hash and exception proposal hash;
- original and active specification hashes;
- both direct state corrections;
- the two pre-correction database snapshots;
- both failed-start logs;
- the execution run ID and controller report;
- all later pauses, failures, uncertain turns, reviews, integrations, final OID,
  and completion status without reinterpretation.

Do not describe run 7 as an unmodified Taskledger v0.6.0 approval path. Do not
discard it either: its preserved lineage remains useful for diagnosing the
controller and measuring post-approval execution, provided the exceptions are
kept explicit.
