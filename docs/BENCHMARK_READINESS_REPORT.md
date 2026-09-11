# Benchmark readiness validation report

Date: 2026-09-11

Branch: `feat/benchmark-readiness`

Starting branch revision: `0cdc8def3ce94b4ea91311ce1aab8896ac922bf4`

Implementation predecessor: `78c99a7557874e63031e79a9d2d7c293b284acd9`

Runtime: `codex-cli 0.153.4`; `gpt-5.6-luna`, low effort for the one controller smoke

Scope: final confidence for one separately authorized Equipment Lending experiment. Equipment Lending was not run.

## Decision

**READY FOR ONE AUTHORIZED EQUIPMENT LENDING EXPERIMENT.** R1 through R4 pass
under the user-approved experimental release bar. No demonstrated defect would
materially invalidate that run. This is not production certification and is
not evidence of cost savings; the representative experiment must supply the
outcome, complete observed spend, and any limitations.

The approved bar deliberately moves timing-dependent live worker/reviewer
overlap to deterministic causal coverage. The earlier live overlap failure and
the stricter CX inventory cases remain unchanged; they are not silently
rewritten as passes.

## Four-gate map and result

| Gate | Result | Production path | Attributable evidence | Limitation |
|---|---|---|---|---|
| R1: Python orchestration | PASS | `ProjectController` -> per-target `Supervisor` -> `TaskledgerLedgerAdapter`/`Service` -> journal -> Git | `test_r1a_project_continuation_rejection_correction_and_completion`; `test_r1a_unsatisfied_final_requirement_prevents_empty_queue_completion`; `test_r1b_review_starts_before_independent_worker_is_released` | Deterministic external behavior/timing is fake; Taskledger state and Git are real. Natural live overlap is diagnostic only. |
| R2: accounting/evidence | PASS | app-server terminal inspection -> supervisor/project reconciliation -> journal -> report; `LiveEvidence` outside disposable fixtures | `test_r2a_supervisor_accounts_failed_retry_and_blocks_partial_uncertainty`; `test_r2a_unknown_adapter_inspection_retains_partial_usage`; `test_r2b_live_evidence_survives_assertion_and_enrichment_failure`; `test_r2b_evidence_observer_uses_acknowledged_runtime_identity`; retained paid artifacts | No real provider-side FAILED turn was forced. Partial/unknown spend remains explicitly incomplete and blocks admission. |
| R3: live continuation | PASS | real `AppServerRuntime`, production controller, dynamic worker tools, independent review, integration, final verification | retained attempt `93135489878d4cb09b5ae6a3073443d4`; zero-model public provider-history verification described below | The unittest result is `FAILED` because a secondary passive observer read an unset key after the project/report had completed. The artifact is not relabeled. Core gate facts are retained independently. |
| R4: bounded capability/reporting | PASS | app-server capability policy and MCP inventory overrides; deterministic `controller_report` and normalized comparison | `test_r4_ambient_mcp_sentinel_is_removed_on_start_and_resume`; `test_nc218_reporting_never_starts_model_turn`; `test_nc219_report_survives_provider_unavailable`; `test_nc221_deterministic_report`; `test_nc242_nc244_deterministic_qualified_comparison`; `test_nc_cli_controller_report_succeeds_without_codex_executable`; retained live report comparison | Existing live reviewer read-only and nested-agent checks are reused from the earlier sweep because those sandbox/delegation paths and profiles did not change. The affected ambient-MCP path was rerun with the executable sentinel. |

## R1 causal and state evidence

R1a exercises this full chain through normal controller APIs: approved plan,
durable partial worker progress without submission, automatic
`ACTIVE_CONTINUATION`, submission, fresh independent rejection with a valid
correction packet, same worker assignment/thread resumed with `CORRECTION`, a
new immutable submission and independent accepting review, guarded integration,
final requirement review, verification, and project completion. Assertions
cover assignment/project/task states, dispatch reasons, worker and reviewer
thread identities, distinct submission IDs, verdicts, integrated OIDs, and the
rule that a reviewer never reviews its own active worker assignment.

The negative companion returns a valid structured final result with an
unsatisfied requirement. Even with no implementation work left, the project
remains active and receives no requirement verification or completion.

R1b uses two disjoint same-wave assignments and asynchronous events rather than
sleeps. Worker B is observed at the external runtime and held unresolved;
worker A submits; review A must be observed at the runtime before B is released.
The test has bounded timeouts, cleans pending fake work in `finally`, asserts a
maximum of two workers and one reviewer, and forbids overlap between a review
and the worker that produced its submission. It passed five repeated flake
checks as well as the final suite.

## R2 accounting and retained evidence

The deterministic all-spend oracle reconciles completed A, failed B, and
completed retry C across total, model, role, dispatch reason, outcome, and
accounting class:

| Attempt | Outcome | Input | Output |
|---|---|---:|---:|
| A | Completed | 1,000 | 80 |
| B | Failed after model work | 400 | 20 |
| C | Completed retry | 600 | 50 |
| **Total** |  | **2,000** | **150** |

Admission is 2,150 (`input + output`), not 1,730. Reconciliation of B is
idempotent. Cached input remains a component of input and reasoning is reported
separately. A partial observation on an unresolved/unknown terminal attempt is
retained but is not treated as a complete final total; token admission pauses.

`LiveEvidence` registers before paid dispatch and writes each acknowledged
session, turn, and usage allocation atomically. It writes the local report
before optional provider enrichment, preserves the original failure when
reporting/enrichment also fails, and is independent of disposable-ledger
cleanup. It excludes prompts, transcripts, reasoning, patches, commands, shell
output, credentials, and provider event bodies.

Paid validation artifacts, retained locally and gitignored:

- `.taskledger-validation-evidence/benchmark-readiness/r3-live-controller-continuation-310da87f35224f4ca75acb2c3bed93f6.json`
- `.taskledger-validation-evidence/benchmark-readiness/r3-live-controller-continuation-93135489878d4cb09b5ae6a3073443d4.json`

The deduplicated rollup across both new attempts is six provider turns: 325,490
input, 232,448 cached input, 0 cache-write input, 3,443 output, 673 reasoning,
and 328,933 admission tokens. The first attempt disconnected during app-server
startup and retained zero turns/usage. The corrected attempt contains all six
complete allocations with `THREAD_TOTAL_DELTA` precision and complete provider
history coverage. The five allocations lost in the prior sweep were not
reconstructed or estimated.

## R3 live controller evidence

The validation allowance was declared once before the first paid dispatch:

| Limit | Per attempt | Total across attempts |
|---|---:|---:|
| Worker turns | 3 | within 14 total-turn cap |
| Reviewer turns | 3 | within 14 total-turn cap |
| Admission tokens | 600,000 | 1,200,000 |
| Elapsed | 300 s | 600 s |
| Corrected reruns | n/a | at most 1 |

Attempt `310da87f35224f4ca75acb2c3bed93f6` found a zero-model startup regression:
Codex 0.153.4 replaced a named MCP table for a dotted CLI override, losing its
transport and closing app-server before any turn. The executable sentinel and
actual installed inventory passed after the narrow fix, so the one predeclared
corrected run was used.

Attempt `93135489878d4cb09b5ae6a3073443d4` ran the real controller and records:

- project report state `COMPLETED` and terminal event `RUN_COMPLETED`;
- two worker assignments, two successful guarded integrations, two accepted
  independent submission reviews, one final review, and verified completion;
- exactly three worker turns, two submission-review turns, and one final-review
  turn, all with complete usage;
- one complex-worker session marked resumed, containing two completed turns on
  external thread `01a091e8-18df-7272-b904-3cdbafe488d9` with distinct turn IDs
  `01a091e8-19f3-7832-b3df-4417788a412e` and
  `01a091e8-4020-7d72-9048-7182dbd8d43d`;
- journal dispatch order `INITIAL_WORK` then `ACTIVE_CONTINUATION`, one
  progressing early stop, zero no-progress turns, zero stalls, and no human
  continuation;
- reviewer maximum configured at one and a recorded wait for reviewer capacity,
  demonstrating serialized submission reviews.

Both artifacts identify source HEAD
`0cdc8def3ce94b4ea91311ce1aab8896ac922bf4` and retain the then-current tracked
diff hash. Subsequent implementation changes are limited to the documented
post-run observer regression and non-secret placeholder hardening; both have
no-model coverage.

A lazy, zero-model `thread/read` diagnostic confirmed that both worker turns
completed normally. The serialized user-message sizes were 2,088 then 397
bytes; the immutable objective and acceptance markers appeared in the first
and not the second. The first and second turns each had one successful command
execution; sanitized checks established that the first performed the declared
incomplete-file commit and the second performed the completion commit, while
only the second turn invoked the worker submission tool.

The project and report were complete before the unittest accessed
`first_a_boundary["provider_completed_normally"]`. The passive callback had
tried to recover role/subject identity through the journal at the runtime
boundary and never populated the dictionary. The callback now receives the
already acknowledged runtime identity directly. The artifact outcome remains
`FAILED`, as required; the retained controller/provider facts above are the R3
gate evidence. No third paid run was made.

## R4 capability and reporting evidence

The disposable sentinel is a valid executable MCP server. A control app-server
loads it and creates its marker, proving the trigger works. Under Taskledger's
bounded start and resume policy the marker is absent, the server remains listed
with null runtime status, and nested-agent/collaboration flags are disabled.
A second zero-model startup against the actual local inventory found six
configured servers and zero running server processes. Taskledger supplies a
harmless non-secret placeholder transport of the same type while applying
`enabled=false`; invalid inventory or unsupported transport fails closed.

The earlier live read-only reviewer and nested-agent denial results are reused:
same Codex 0.153.4, Luna/low profiles, sandbox/delegation path, and capability
hash inputs. The affected inherited-MCP path alone changed and was rerun. The
successful R3 report independently records zero nested-agent calls, zero
subagent activity, and zero unexpected/untracked provider turns.

The retained live report was normalized and compared twice, without starting a
model, against a clearly labeled synthetic `INCOMPLETE` / `PARTIAL_MISSING`
fixture. Both comparisons agreed and declared no winner; the completed run's
outcome and complete accounting remained visible. The real CLI no-Codex test
also passed, returning the local report with provider detail unavailable.

## Commands and results

```text
python3 -m unittest discover -s tests -v
  140 discovered, 131 non-live passed, 9 opt-in skipped, 0 failures, 85.589 s

TASKLEDGER_ZERO_MODEL_CODEX=1 python3 -m unittest \
  tests.test_controller_hardening.ControllerHardeningTests.test_r4_ambient_mcp_sentinel_is_removed_on_start_and_resume -v
  1 passed, 1.074 s

Selected no-model reporting/CLI checks
  5 passed

TASKLEDGER_LIVE_CODEX=1 TASKLEDGER_LIVE_MODEL=gpt-5.6-luna \
TASKLEDGER_LIVE_EFFORT=low TASKLEDGER_LIVE_EVIDENCE_DIR="$PWD/.taskledger-validation-evidence/benchmark-readiness" \
python3 -m unittest tests.test_controller_live.ControllerLiveContractTests.test_r3_live_project_automatic_same_thread_continuation -v
  attempt 1: FAILED before provider dispatch, 0 turns, 3.355 s
  corrected attempt: project/report completed in 6 turns; unittest FAILED on
  secondary observer KeyError after evidence export, 84.986 s
```

## Deferred production hardening

The following remain explicitly unproven or deferred and are not gates for this
single experiment:

- exhaustive crash/lost-ack and provider-history failure permutations;
- a reproducible real provider-side FAILED turn;
- full portability and ambient capability sentinel matrices;
- perfect retrospective task/blocker/requirement revision reporting;
- unavailable historical normalized baseline/valuation;
- remaining planned, implemented-but-unproven, and deferred inventory cases,
  including the literal timing-based CX-119/CX-123 assertions.

## Later Equipment Lending invocation

Do not run until the representative experiment is separately authorized and
its approved Taskledger plan exists. From the managed Equipment Lending
repository, record the starting OID, approved plan fingerprint, source/spec
hash, execution policy, limits, and Luna/low profile identity in a strict JSON
request, then invoke:

```sh
TASKLEDGER_LIVE_MODEL=gpt-5.6-luna TASKLEDGER_LIVE_EFFORT=low \
taskledger controller run-project --input equipment-lending-controller-run.json
```

The request must contain `"live": true`, an `execution_policy` entry for every
unfinished approved task (real task ID, wave, `routine` or `complex` profile,
parallel-safety decision, and exact write surfaces), and explicit limits for
worker/reviewer/Task-Creator turns, reviewer reserve, `input + output` token
admission, turn timeout, and elapsed time. The specific input still needed to
construct this safely is the initialized Equipment Lending project/repository,
its approved current plan and task IDs, the exact execution policy/write
surfaces, and the separately authorized experiment budget. Creating a new run
must not reset that authorization's aggregate allowance.
