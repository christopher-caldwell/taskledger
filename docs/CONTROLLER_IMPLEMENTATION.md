# Taskledger executable controller

The controller supervises one existing isolated assignment in the foreground.
It treats a model turn ending as a transport event, not as evidence that the
assignment is complete.

## Supported boundary

The deterministic supervisor and fake runtime cover continuation, stalls, retry
limits, structured verdicts, durable results, and uncertain outcomes. The real
ledger adapter uses Taskledger assignment context, worktree fingerprints,
reviewer checks, verification, and integration. The Codex app server adapter is
available only through explicit live opt in because those tests consume model
usage.

Project scheduling, requirement reviewer automation, and project completion are
not enabled by the single assignment command. Taskledger continues to handle
those operations through its existing orchestrator workflow.

## Commands

`controller run-assignment` starts a foreground run. Its strict request contains
`assignment_id`, `live: true`, and optional `limits`. Supported limits cover
worker turns, consecutive stalls, consecutive known runtime failures, reviewer
turns per submission, total token admission, and reviewer profile.

`controller resume` accepts `run_id` and `live: true`. It inspects every recorded
open turn before dispatching anything new. Unknown outcomes remain paused.

`controller show` reads a run without dispatch. `controller extend-budget`
records a positive grant for worker turns, reviewer turns, tokens, or elapsed
seconds. Grants never erase prior usage.

## Security boundary

Workers receive write access only to their assignment worktree and the narrow
Git metadata roots required for commits. App-server dynamic tools call a local
broker that holds the assignment worker principal and exposes only existing
worker operations; no credential or network access reaches the model. Reviewers
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
and integration. It remains skipped in the default zero-model test run.

The complete verification inventory is [CONTROLLER_TEST_PLAN.md](CONTROLLER_TEST_PLAN.md).
Every additional controller change should retain its case identifier in the test
name or nearby test documentation so coverage can be audited against that file.
