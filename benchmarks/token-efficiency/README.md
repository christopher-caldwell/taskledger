# Token-efficiency checkpoints

These checkpoints compare Taskledger workflow usage without pretending that
different models consume tokens or credits equivalently.

## Current phase marker

`new-planning-v0.3.0-checkpoint.json` records `2026-08-22T19:28:55Z` as the
start of the first brand-new v0.3.0 planning sample. The earlier v0.3.0 sample
remains part of the evidence series, but is labeled as execution of a queue that
was already planned and materialized. The next comparison should split planning
from execution and extend the series rather than replacing earlier checkpoints.

Use `task-complexity-methodology.md` for the comparison hierarchy. The broad
22-task pre-v0.3.0 total is historical context, not the primary comparator for
a new task family. `financial-tracker-http-spec-task-complexity.json` is the
task-level reconstruction of the first HTTP queue.

`rust-but-verify-pre-next-run-checkpoint.json` marks the next planned sample: a
small-scope, brand-new Taskledger effort in `rust_but_verify`. Its existing
completed ledger and dirty working-tree fingerprint are recorded so later
analysis uses checkpoint deltas and does not attribute earlier work to the run.
The completed sample is captured in `rust-but-verify-post-v0.3.0-thread-01a02b0d.json`,
its task reconstruction, and `comparison-rust-but-verify-v0.3.0-thread-01a02b0d.md`.
The three verified earlier RustButVerify tasks and their inclusion limits are
documented in `rust-but-verify-pre-v0.3.0-thread-verification.md`; their usage
checkpoints and combined task reconstruction preserve each model/version cohort.

`blind-sol-quality-audit-prompt.md` is the predeclared shadow-review protocol
for a future Luna/medium implementation plus Terra/high review experiment. It
freezes Sol/high findings before exposing Taskledger review history and reports
evaluation cost separately from operational cost.

`financial-tracker-ui-terra-xhigh-interim-audit.md` records an in-progress
orchestrator-quality audit. Its associated usage and task-complexity JSON files
are interim artifacts and must be regenerated after the active thread ends.

`comparison-financial-tracker-v0.4.0-thread-01a030bc.md` records the first
v0.4.0 two-profile Financial Tracker run. It separates three Taskledger plan
waves from an intervening direct repair, verifies two routine/Luna and four
complex/Terra assignments, and compares the corrected 104.68M input total with
the earlier UI run.

The current collectors require modern top-level `token_usage_record` events,
deduplicate response IDs across root/descendant rollouts, and reconcile response
sums with final thread counters. Cumulative turn/thread counters are never added.
Totals are also broken out by thread source and spawned agent role so review or
guardian usage is never silently blended into implementation usage. Legacy
`token_count` events are ignored, so older checkpoints must be regenerated from
modern telemetry before comparison.

## Comparison protocol

1. Keep the primary model, worker model, reasoning effort, Codex version, and
   task-risk mix unchanged for the cleanest A/B comparison.
2. Define the measurement window and explicit root session before examining the
   result. Root/descendant identity drives selection; a primary whose cwd is a
   parent of a nested project remains included.
3. Compare each exact `model|reasoning-effort` cohort separately.
4. Normalize by durable progress: submissions recorded, submissions verified,
   integrations completed, and requirements verified. Compare both totals and
   tokens per event.
5. Track input, cached input, output, turns, compactions, and broad Taskledger
   calls separately. A lower token total with more defects or rework is not a
   win.
6. Use actual provider credit observations when available. Do not extrapolate a
   lower model's token count into a higher model's expected behavior.
7. Match task families before normalizing. Preserve criteria, requirements,
   submissions/corrections, files, changed lines, and elapsed windows as
   separate dimensions rather than inventing one complexity score.

If the model mix changes, calculate the post/pre ratio inside each model cohort,
then weight those ratios using the *baseline* model mix. Label that result a
standardized usage index, not observed credits.

## Capture command

```sh
python3 scripts/token_usage_checkpoint.py \
  --repo /absolute/path/to/project \
  --since 2026-08-23T00:00:00Z \
  --until 2026-08-30T00:00:00Z \
  --label post-v0.3.0-round-1 \
  --taskledger-version 0.3.0 \
  --session-id ROOT_SESSION_ID \
  --ledger-db /absolute/path/to/.taskledger/taskledger.sqlite3 \
  --output benchmarks/token-efficiency/post-v0.3.0-round-1.json
```

The script reads the database immutably and stores counts only. It does not store prompts, source,
tool outputs, credentials, or evidence text.

For a task-level execution reconstruction, pass the completed task IDs to:

```sh
python3 scripts/task_complexity_checkpoint.py \
  --repo /absolute/path/to/project \
  --ledger-db /absolute/path/to/.taskledger/taskledger.sqlite3 \
  --label post-v0.3.0-task-family \
  --task-id TASK_ID_1 \
  --task-id TASK_ID_2 \
  --output benchmarks/token-efficiency/post-v0.3.0-task-family.json
```

This collector reads the ledger and Git history without mutation. It attributes
modern response-usage records to each assignment-activation through
successful-integration window and reports overlaps explicitly.

## Decision rule

Treat v0.3.0 as materially better when matched cohorts show at least a 20%
reduction in input tokens per durable progress event, fewer broad state calls
and compactions, and no increase in rejection, correction, blocker, or recovery
rates. With fewer than roughly 20 completed tasks, call the result directional
rather than conclusive.
