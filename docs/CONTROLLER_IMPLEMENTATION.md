# Taskledger executable controller

The foreground Python controller executes an approved Taskledger project plan.
It treats a model turn ending as a transport event, not as evidence that an
assignment is complete. The single assignment supervisor is its reusable
execution primitive.

Python is the only persistent orchestration and decision loop. Codex threads
may persist for context and caching, but a model runs only for a bounded
semantic job explicitly dispatched by Python. Idle threads and the app-server
transport process do not schedule, poll, report, or decide what runs next.

## Supported boundary

The deterministic scheduler derives readiness from the approved execution
policy and authoritative Taskledger state. It enforces dependencies, waves,
declared parallel safety, write surface compatibility, two generic worker slots
by default, and one independent reviewer slot by default. The real ledger
adapter supplies assignment context, worktree fingerprints, reviewer checks,
verification, integration, and requirement completion. Codex app server access
is available only through explicit live opt in because it consumes model usage.

The controller resolves Codex-native `.codex/agents/*.toml` role files with
project, user/global, then bundled precedence. It persists the source, complete
file hash, resolved model and effort, effective configuration hash, sandbox,
protocol identity, thread and turn IDs, controller context measurements,
cumulative-usage deltas and provenance, budgets, and target state. Native Codex
configuration validation rejects unknown fields. Controller-owned authority
always wins; a conflicting role file fails loudly.

Restart reconciliation consumes each completed result once and pauses on
outcomes that cannot be proven. The first worker turn receives the complete
assignment packet; same-thread continuations and structured-output retries send
only changed state or a compact correction request. A bounded final reviewer
checks the integrated canonical state. Clear defects invoke a fresh bounded
Task Creator; product ambiguity pauses for the user.

## Commands

`project prepare` is the normal entry point. It runs the bounded initial Task
Creator, stores an immutable proposal, and stops for hash approval. `project
start` validates that preparation, materializes it, and invokes the foreground
project controller.

`controller run-project` remains the lower-level post-approval execution path. Its strict
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
records a positive grant for worker turns, reviewer turns, Task Creator turns,
tokens, or elapsed seconds. Project worker, reviewer, and correction-planning
budgets are global across assignments, and grants never erase prior usage.

`controller report` is deterministic Python reporting over Taskledger's
authoritative domain/controller rows. Optional provider detail is read from
Codex persisted history through `thread/turns/list` and `thread/items/list`.
Reporting never starts a model turn, does not persist provider history copies,
and remains valid with provider detail marked unavailable when Codex history
cannot be read.

## Security boundary

Workers receive write access only to their assignment worktree and the narrow
Git metadata roots required for commits. App-server dynamic tools call a local
broker that holds the assignment worker principal and exposes only existing
worker operations; no credential or outbound network permission is supplied to
the model. Reviewers
receive a read only checkout and no Taskledger credential.
The controller executes declared reviewer checks and applies structured verdicts
through the existing service.

Controller-owned app-server processes force `agents.enabled=false`,
`features.multi_agent_v2=false`, the legacy `features.collab=false` gate, and
collaboration-mode instruction injection off. Controller-owned models cannot
recursively delegate or spawn model agents.
Any observed collaboration-agent call or subagent activity is reported as an
architecture-invariant violation.

## Storage boundary

Taskledger stores facts Python uniquely owns or needs for correctness: dispatch
reasons, state transitions, immutable run identity, injected-context byte counts
and hashes, cumulative usage deltas and precision, budgets, and validated
structured semantic results needed for recovery. New worker prose is reduced to
a response hash.

On resume, a replayed Codex cumulative total is cross-checked with the durable
controller total. Legacy local rollouts that contain no replayable token-count
record restart Codex's live counter at zero; in that case the already-proven
durable total is retained as an accounting offset. If neither a replay nor a
durable baseline exists, usage remains `MISSING` and token admission fails
closed.

Codex remains authoritative for model messages, reasoning, commands and output,
file changes, patches, dynamic/MCP tool items, compactions, collaboration items,
and turn history/timing. Taskledger does not mirror those items or raw response
bodies. Cached input is a component of input usage; token admission remains
`input_tokens + output_tokens` and does not add cached input again.

## Benchmark readiness

Every dispatched turn begins as an incomplete accounting allocation. Terminal
`COMPLETED`, `FAILED`, and `UNCERTAIN` turns retain any attributable cumulative
delta; exact response observations without a provable final delta are retained
as `PARTIAL_OBSERVATION`. Reconciliation improves the same external-turn
allocation idempotently. Under a token budget, any terminal incomplete
allocation stops new admission while preserving the known lower-bound subtotal.

Controller reports aggregate every terminal outcome and reconcile counts and
token dimensions by model/runtime identity, conceptual role, dispatch reason,
semantic outcome, and accounting class. Timing distinguishes gross elapsed,
recorded pause, non-paused wall time, summed attempt time, and interval unions
at a fixed report cutoff. Provider history is optional, bounded, read-only
enrichment and cannot prevent the core CLI report.

The frozen pre-run protocol is
[`BENCHMARK_PROTOCOL_V1.json`](BENCHMARK_PROTOCOL_V1.json). The pure-Python
`taskledger.controller.benchmark` module validates normalized measurements and
keeps outcome, accounting quality, and comparability independent. It never
fetches prices, converts tokens to credits without a supplied immutable
valuation snapshot, or declares a winner from incomplete or confounded runs.

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
