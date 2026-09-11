# Taskledger executable controller

The foreground Python controller executes an approved Taskledger project plan.
It treats a model turn ending as a transport event, not as evidence that an
assignment is complete. The single assignment supervisor is its reusable
execution primitive.

## Supported boundary

The deterministic scheduler derives readiness from the approved execution
policy and authoritative Taskledger state. It enforces dependencies, waves,
declared parallel safety, write surface compatibility, two generic worker slots
by default, and one independent reviewer slot by default. The real ledger
adapter supplies assignment context, worktree fingerprints, reviewer checks,
verification, integration, and requirement completion. Codex app server access
is available only through explicit live opt in because it consumes model usage.

The controller persists resolved model, effort, profile hash, sandbox policy,
protocol identity, thread IDs, turn IDs, raw per turn usage events, budgets, and
target state. Restart reconciliation consumes each completed result once and
pauses on outcomes that cannot be proven. A bounded final reviewer checks the
integrated canonical state. Clear defects invoke a fresh bounded Task Creator;
product ambiguity pauses for the user.

## Commands

`controller run-project` starts the normal foreground execution path. Its strict
request contains `execution_policy`, `live: true`, and optional `limits`. The
policy records task, wave, routine or complex profile, parallel safety, and
prospective write surfaces for every unfinished task.

`controller run-assignment` starts the bounded diagnostic primitive. Its strict request contains
`assignment_id`, `live: true`, and optional `limits`. Supported limits cover
worker turns, consecutive stalls, consecutive known runtime failures, reviewer
turns per submission, total token admission, and reviewer profile.

`controller resume` accepts `run_id` and `live: true` for either run mode. It
inspects every recorded open turn before dispatching anything new. Unknown
outcomes remain paused.

`controller show` reads a run without dispatch. `controller extend-budget`
records a positive grant for worker turns, reviewer turns, tokens, or elapsed
seconds. Project worker and reviewer turn budgets are global across assignments,
and grants never erase prior usage.

## Security boundary

Workers receive write access only to their assignment worktree and the narrow
Git metadata roots required for commits. App-server dynamic tools call a local
broker that holds the assignment worker principal and exposes only existing
worker operations; no credential or outbound network permission is supplied to
the model. Reviewers
receive a read only checkout and no Taskledger credential.
The controller executes declared reviewer checks and applies structured verdicts
through the existing service.

## Verification

The default test suite uses no Codex. Live tests must use disposable repositories
and explicit turn and token budgets. A live failure that cannot prove whether a
turn started pauses and does not dispatch a replacement turn.

Run the opt-in E3 contract suite with:

```sh
TASKLEDGER_LIVE_CODEX=1 TASKLEDGER_LIVE_MODEL=gpt-5.6-luna \
  TASKLEDGER_LIVE_EFFORT=low python3 -m unittest tests.test_controller_live -v
```

Each test declares its maximum live-turn budget in its docstring. The suite
covers initialization and shutdown, same-thread continuation, resume after a
client restart, structured reviewer output, read-only review, usage capture,
assignment-scoped dynamic tools, linked-worktree commits, submission, review,
integration, two concurrent workers with one reviewer slot, bounded final
requirement review, and project completion. It remains skipped in the default
zero-model test run.

The complete verification inventory is [CONTROLLER_TEST_PLAN.md](CONTROLLER_TEST_PLAN.md).
Every additional controller change should retain its case identifier in the test
name or nearby test documentation so coverage can be audited against that file.
