# Bug: preparation binds a stale specification revision across preflight

> Historical record, archived 2026-09-12. Paths, commands, and status statements below describe the original work and may no longer apply. Use the [current documentation index](../../../README.md) for operating guidance.

For the complete run-7 lifecycle findings from setup through the first live
controller report, see
[`EQUIPMENT_LENDING_RUN_7_CLI_FIRST_FINDINGS.md`](EQUIPMENT_LENDING_RUN_7_CLI_FIRST_FINDINGS.md).

## Resolution recorded 2026-09-12

Commit `d386e02` (`fix: Stale docs (#2)`) added the specification identity and
source normalization corrections, with regression coverage in
[`tests/test_initial_planning.py`](../../../../tests/test_initial_planning.py):

- `test_prepare_rebinds_changed_specification_and_prompt_uses_selected_bytes`
- `test_start_rejects_a_changed_specification_before_git_drift`
- `test_mutation_during_planning_never_stores_a_proposal`
- `test_source_normalization_is_deterministic_for_proposals_and_requirements`

The original observations below are preserved as incident evidence. They do
not establish that these defects remain present in the current checkout, or
that the external benchmark subsequently completed.

## Original status

- Observed: 2026-09-11/12 UTC during Equipment Lending benchmark run 7
- Taskledger version: `0.6.0`
- Taskledger source commit: `c1453448436ec8d46d502eb9605876cca026e3fe`
- Codex protocol identity recorded by the preparation: `codex-cli 0.153.4;app-server-v2-jsonrpc+experimental-dynamic-tools;taskledger-client-0.6.0`
- Python: `3.13.7`
- Host: macOS `26.3.1` (`25D2128`)
- Severity: high for preparation integrity

`project prepare` can persist the hash of the previously active specification
revision while the Task Creator reads and plans from the newly changed file.
The resulting immutable preparation cannot pass `project start` once the ledger's
active revision is current. The same stale-row ordering in `project start` can
also fail in the opposite direction: a specification change first discovered by
that command can be compared against the old revision and escape the intended
staleness check.

## Observed benchmark

Repository:

```text
/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/trials/equipment-lending-run-7
```

Canonical branch and commit:

```text
feat/equipment-lending-taskledger-run-7
554e2af1bd5f2cd82fdefbbe697d253119421980
```

The repository was clean when `project start` was attempted. The relevant Git
history was:

```text
554e2af docs: preserve approved RBV identity convention
39e7606 fix: declare macOS preflight inputs accurately
51bcbc6 chore: initialize equipment lending run 7 benchmark
```

Specification and preparation identities:

```text
specification path: docs/product-specification.md
current file SHA-256: 9fc22b36d810ab9e87db39bab6feadfa04ab886731d62b335c69aa6cda7e3928
preparation ID: a688e9fe-6c0c-42e8-af2b-3eb4021b7f23
planning run ID: daab07b6-cb47-4d01-81b2-3b3a9f3c8ec8
proposal hash: sha256:d08be6df712c5ceadc11744fae22f27e33a9a2882e8980ec05eac2165d0be300
preserved prepare output SHA-256: 5de3e493ff3f56d1e5f9716ecdd5b1f85769595b23706c2f810c3c1eb62f273f
```

The specification revision table contained:

```json
[
  {
    "sequence": 1,
    "content_hash": "a8a7da85dfe15f4ad6df39232e8eeb4bea0bc4b179f5691f568b59f583be9bda",
    "observed_at": "2026-09-11T23:56:23.271Z",
    "approved_at": "2026-09-11T23:56:23.271Z",
    "contains_rbv_clarification": false
  },
  {
    "sequence": 2,
    "content_hash": "9fc22b36d810ab9e87db39bab6feadfa04ab886731d62b335c69aa6cda7e3928",
    "observed_at": "2026-09-11T23:58:28.951Z",
    "approved_at": "2026-09-11T23:58:28.951Z",
    "contains_rbv_clarification": true
  }
]
```

The specification row correctly selected revision 2 as active:

```json
{
  "specification_id": "29682f4d-e3a6-4bd7-ab29-a8374dc9a67a",
  "relative_path": "docs/product-specification.md",
  "active_revision_id": "8b7a1482-7b0b-4655-b4aa-933acb327c11",
  "active_sequence": 2,
  "active_hash": "9fc22b36d810ab9e87db39bab6feadfa04ab886731d62b335c69aa6cda7e3928"
}
```

However, the successful preparation stored revision 1's hash:

```json
{
  "id": "a688e9fe-6c0c-42e8-af2b-3eb4021b7f23",
  "planning_run_id": "daab07b6-cb47-4d01-81b2-3b3a9f3c8ec8",
  "starting_oid": "554e2af1bd5f2cd82fdefbbe697d253119421980",
  "specification_hash": "a8a7da85dfe15f4ad6df39232e8eeb4bea0bc4b179f5691f568b59f583be9bda",
  "proposal_hash": "sha256:d08be6df712c5ceadc11744fae22f27e33a9a2882e8980ec05eac2165d0be300",
  "state": "AWAITING_APPROVAL",
  "proposal_contains_rbv_requirement": true
}
```

The last field is important. Revision 1 did not contain the RBV clarification;
revision 2 did. The stored proposal explicitly requires "Use RBV inside named
Identity behavior," proving that the Task Creator planned from the current
revision-2 filesystem content even though the preparation metadata was bound to
revision 1.

The approved start request was:

```json
{
  "preparation_id": "a688e9fe-6c0c-42e8-af2b-3eb4021b7f23",
  "approve_proposal_hash": "sha256:d08be6df712c5ceadc11744fae22f27e33a9a2882e8980ec05eac2165d0be300",
  "live": true
}
```

`taskledger project start --input -` rejected it before plan materialization,
controller creation, or model dispatch:

```json
{"command":"project.start","error":{"allowed_actions":[],"code":"PREPARATION_STALE","details":{},"message":"Specification changed after preparation."},"ok":false}
```

The detached launcher initially retried this short-lived command, so the exact
line appeared 89 times before the launch job was removed. This repetition is a
launcher behavior, not 89 distinct Taskledger state transitions. The preserved
raw log is:

```text
/Users/christophercaldwell/Code/trial/rust/rust_simpler_playbook/trials/equipment-lending-run-7/review-evidence/taskledger-start-preparation-stale.log
SHA-256: 479d840f38990d2f031414a0f191f7f6a273de970e781e57a0def3ff20960e86
```

No execution controller run ID was created by these rejected starts.

## Reproduction sequence

1. Initialize a project and register specification revision A.
2. Run `project prepare`; allow the Task Creator to return an ambiguity so the
   preparation reaches `FAILED` rather than remaining open.
3. Change and commit the specification, producing revision B on disk.
4. Invoke `project prepare` directly, without a separate earlier command that
   happens to refresh the registered specification row.
5. Let preflight discover and activate revision B.
6. Observe that the Task Creator reads revision B and produces a proposal using
   B-only content.
7. Observe that `project_preparations.specification_hash` contains A's hash.
8. Approve the proposal and invoke `project start`.
9. Observe `PREPARATION_STALE: Specification changed after preparation.`

The public `project prepare` contract says that it validates the repository and
stores an immutable proposal. It does not require callers to run `spec check`
first, so this should be fixed inside the command rather than documented as a
caller prerequisite.

## Root cause

In `prepare_project_command` (`src/taskledger/cli.py`, lines 245-255 at the
observed commit), Taskledger reads the `specifications` row before calling
`service.preflight(project)`:

```python
spec = service.con.execute(
    "SELECT * FROM specifications WHERE project_id=? AND relative_path=? AND lifecycle='ACTIVE'",
    (project["id"], spec_path),
).fetchone()
# ...
service.preflight(project)
revision = service.con.execute(
    "SELECT * FROM specification_revisions WHERE id=?",
    (spec["active_revision_id"],),
).fetchone()
```

SQLite row objects are snapshots. If preflight discovers changed specification
bytes and updates `specifications.active_revision_id`, the already-fetched
`spec` object still contains the previous revision ID. Lines 273 and 281 then
persist that old revision's hash into both the preparation controller manifest
and `project_preparations.specification_hash`. Meanwhile, `InitialPlanner` at
lines 286-289 receives `spec_path` and reads the current filesystem content.
The proposal and its specification identity can therefore refer to different
revisions.

`start_prepared_project` has the same ordering at lines 327-330: it fetches the
specification row, calls preflight, then dereferences the stale row. In addition
to the observed false rejection, this creates a potential false acceptance. If
the file changes after preparation and `project start` is the first command to
observe it, preflight can advance the database to the changed revision while
the stale local row still points to the prepared revision; the equality check
can pass even though the active specification has changed.

## Required fix

For both preparation and start:

1. Run the filesystem/specification synchronization step first.
2. Re-query the `specifications` row after synchronization.
3. Resolve the active revision from that newly queried row, preferably in one
   join/query that returns the specification and active revision atomically.
4. Bind the planner input to the same immutable revision bytes/hash rather than
   letting it independently reopen a mutable filesystem path after validation.

The fourth point closes a remaining time-of-check/time-of-use window even after
the stale-row bug is fixed.

## Regression tests

Add at least these cases to `tests/test_initial_planning.py`:

1. **Changed spec before re-prepare:** prepare revision A to a terminal failed
   state, change/commit to B, prepare again, and assert the new preparation hash,
   planning manifest hash, and planner bytes all equal B.
2. **Changed spec before start:** prepare A, change/commit to B without an
   intervening spec refresh, then assert `project start` rejects the preparation
   as stale.
3. **Unchanged approved start:** prepare A and start it unchanged; assert it gets
   past the specification check and materializes only the approved proposal.
4. **Mutation during planning:** change the filesystem file after the immutable
   revision is selected but before/during planner launch; assert either that the
   planner receives the selected immutable bytes or that preparation aborts
   without storing a proposal.

## One-off benchmark recovery performed

The benchmark owner explicitly declined another paid Astra planning run and
authorized a direct state correction. Before that correction, the database and
failure log were preserved at:

```text
review-evidence/taskledger-before-preparation-rebind.sqlite3
SHA-256: b2d284c341c11e63b39b924510c987af0a0f1f9b023bf401ac424a920b72f61d

review-evidence/taskledger-start-preparation-stale.log
SHA-256: 479d840f38990d2f031414a0f191f7f6a273de970e781e57a0def3ff20960e86
```

The authorized correction changes only
`project_preparations.specification_hash` for preparation
`a688e9fe-6c0c-42e8-af2b-3eb4021b7f23`, from revision 1's hash to the already
active revision-2 hash. It does not change the proposal JSON, proposal hash,
starting OID, run configuration, profile identities, planning usage, or source
file. This is an out-of-protocol benchmark exception, not a supported recovery
procedure or a proposed public command.

## Secondary materialization defect exposed after the rebind

After the specification hash was rebound, `project start` passed the stale-spec
gate but returned a generic internal error before creating requirements, tasks,
assignments, or an execution controller run:

```json
{"command":"project.start","error":{"allowed_actions":[],"code":"INTERNAL_ERROR","details":{},"message":"Taskledger encountered an unexpected internal error."},"ok":false}
```

The raw one-line log is preserved at:

```text
review-evidence/taskledger-start-materialization-internal-error.log
SHA-256: df1c3a8db5a9301d5172dcc75f6f2b83e3c81b9fbdffcf85eb3f1dee8d69a0db
```

An isolated copy of the rebound ledger produced the underlying traceback without
mutating the benchmark ledger:

```text
Traceback (most recent call last):
  File "<stdin>", line 15, in <module>
  File "src/taskledger/service.py", line 665, in apply_plan
    self.con.execute("INSERT INTO requirement_source_refs VALUES(?,?,?,?,?)", ...)
sqlite3.IntegrityError: UNIQUE constraint failed:
requirement_source_refs.requirement_id,
requirement_source_refs.requirement_revision,
requirement_source_refs.specification_id,
requirement_source_refs.locator
```

The approved proposal's R20 contains two distinct excerpts from the same
specification locator:

```json
{
  "requirement_ref": "R20",
  "locator": "docs/product-specification.md#Deliverables and completion",
  "sources": [
    {
      "excerpt": "Deliver the runnable application, SQL/setup assets, tests",
      "locator": "docs/product-specification.md#Deliverables and completion"
    },
    {
      "excerpt": "`README.md` with exact setup, migration, seed, test, server, request, restart, and cleanup commands;",
      "locator": "docs/product-specification.md#Deliverables and completion"
    }
  ]
}
```

`validate_proposal` accepts this shape, but `requirement_source_refs` uses
`(requirement_id, requirement_revision, specification_id, locator)` as its
primary key. `materialized_plan` adds the same specification ID to both source
objects, and `Service.apply_plan` attempts both inserts. The second insert raises
an unhandled `sqlite3.IntegrityError`; the surrounding plan transaction rolls
back, leaving zero requirements and zero tasks.

This should be fixed at both boundaries:

1. Proposal validation should reject or normalize duplicate source identity
   keys before an immutable proposal reaches approval.
2. `apply_plan` should convert uniqueness violations caused by request content
   into a specific `LedgerError`, not the generic `INTERNAL_ERROR` envelope.
3. The proposal schema/prompt and command reference should define whether
   multiple excerpts at one locator are allowed. If they are allowed, the
   persistence model needs a stable source position or excerpt component in its
   key. If they are not, callers should combine excerpts into one source record.

Regression coverage should submit one requirement with two excerpts sharing a
locator and assert either deterministic normalization or a named validation
error before transaction entry. It should also assert that no partial plan is
visible after rejection.

Before the one-off source normalization, a second database snapshot was retained:

```text
review-evidence/taskledger-after-spec-rebind-before-source-normalization.sqlite3
SHA-256: 4a04083679a30815be88e7afcad0e0fd235b857790b349119569ce642ea5ebb8
```

For the authorized benchmark exception, the two R20 source objects are combined
into one source at the identical locator whose excerpt contains both original
snippets verbatim. No requirement statement, detail, task, routing decision,
acceptance criterion, check, assumption, or ambiguity changes. Because proposal
JSON changes, its canonical proposal hash must also be updated and retained as a
separate exception identity. This is not a supported Taskledger recovery path.

The resulting exception proposal hash is:

```text
sha256:c25595fe84f4a5aba370fe350277459e954210e7f076403294affa0a3830c164
```
