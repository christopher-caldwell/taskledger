# Task Ledger visual console: architecture package

Current implementation coverage and remaining required work are recorded in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

This package specifies **two implementation parts**, not two services:

| Document | Responsibility |
|---|---|
| [Part 1 — Headless console mechanics](01-headless-mechanics-spec.md) | Application queries and mutations, execution lifetime, emitter integration, committed snapshots, bounded delivery, ownership, recovery, and correctness arguments. |
| [Headless acceptance evidence](HEADLESS_ACCEPTANCE.md) | M01–M24 test mapping, verification commands, measurements, and platform coverage. |
| [Part 2 — Textual display adapter](02-textual-display-spec.md) | The first visual interface: observation, navigation, presentation, forms, approval, safe-exit flows, packaging, and UI verification. |

**Inspected source:** `christopher-caldwell/taskledger`, commit `c1453448436ec8d46d502eb9605876cca026e3fe`, the current remote `main` when reviewed. Repository source citations are pinned in the specifications. Uncommitted local changes were not available.

## Architectural decision

Use one local process with a framework-independent engine and Textual presentation. For interactive mode, put the existing mechanics and their SQLite connection in one dedicated engine thread; keep Textual and signal routing on the main thread.

The engine emits change notifications after successful commits. A query layer produces immutable snapshots from committed state, and a bounded latest-value feed supplies those snapshots to the UI. This is **event-driven observation, not SQLite polling and not a second event-sourced ledger**.

The cache is replaceable. Existing SQLite facts still determine current status, accounting, history, and recovery. Rendering never authorizes execution. A future web adapter can reuse the application contract and feed without moving scheduling or validation into browser code.

The existing blocking subprocess calls are the reason for the separate engine thread. That choice keeps display work from sharing their event loop. It does not make a blocking check immediately interruptible. The specs require a visible distinction between a pause request and a proven paused state.

## Evidence and its limits

The package includes [standalone contract experiments](validation/test_contracts.py), [machine-readable results](validation/results.json), and [test output](validation/test-output.txt).

**Recorded result: 26 tests passed, zero failures or errors.** They use a synthetic ledger, actual SQLite transactions, asyncio loops, and threads. They are mechanism experiments, not production implementation code.

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| P01–P07 | Synthetic commit/rollback notification behavior, safe resnapshot, read-write separation checks, and SQLite owner-thread restriction | Complete hook coverage or transaction behavior in the actual TL service |
| P08–P14 | Replacement snapshots, retained synthetic history, stale-version rejection, an abstract bootstrap counterexample, and controlled workload comparison | Formal verification of the entire ledger or all possible concurrent schedules |
| P15–P22 | Initial seeding, real cross-thread wake-up, slow-consumer convergence, single-reader restriction, and bounded pending delivery under bursts | Textual’s downstream message behavior or terminal rendering performance |
| P23–P26 | Separate-loop progress during a blocked engine, cancellation isolation for an owned job, a stop-request admission model, and correct connection ownership | Real Codex interruption/reconciliation, immediate stopping of synchronous checks, or runtime thread compatibility on every OS |

The suite includes a 10,000-publication burst with one pending snapshot and one scheduled wake-up while the receiving loop has not drained it. The bound is on pending state/wake-ups, not on total callbacks over an indefinitely running session.

The experiment environment was Linux, Python 3.13.5, and SQLite 3.46.1. Exact environment data and case outcomes are in `validation/results.json`. No model calls were made.

The synthetic experiment record above remains unchanged historical design
evidence. The implemented headless layer and Task Ledger regression suite have
now been exercised on macOS; current commands and results are recorded in
[HEADLESS_ACCEPTANCE.md](HEADLESS_ACCEPTANCE.md). Linux implementation coverage,
live Codex tests, and the complete Textual/Pilot matrix have not been run for
the current working tree.

The specifications separately identify **24 headless acceptance cases and 24 Textual acceptance cases** that implementation must satisfy. Performance figures in them are proposed test targets, not measured product claims.

## Reproduce the experiments

The script uses only the standard library. From this directory:

```sh
python3 validation/test_contracts.py
```

It writes `validation/results.json` next to itself and exits nonzero on failure. To retain new console output as well:

```sh
python3 validation/test_contracts.py > validation/test-output.txt 2>&1
```

Rerunning replaces those local evidence files. It does not modify a Task Ledger repository, open a model connection, or require network access. Use Python 3.11 or newer; only the recorded environment above was exercised for this delivery.

## Implementation use

Implement and verify Part 1 without Textual first. Build Part 2 against fixture/fake snapshots in parallel only for display work; it must not fill mechanical gaps inside widgets.

Before implementing, inspect the actual repository worktree and reconcile changes since the pinned source. Preserve existing work. The specifications are proposed additions and targeted extraction work, not permission to reset the repository or redesign the scheduler.

Part 1 is implemented and its current acceptance evidence is recorded above.
The visual-console feature as a whole still requires the Part 2 Textual
acceptance suite.
