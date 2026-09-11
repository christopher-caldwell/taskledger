# Controller verification coverage

This file records the implementation and execution status of every case in
`CONTROLLER_TEST_PLAN.md`. It is a coverage ledger, not a substitute for the
tests. Status reflects the current checkout and the latest test run recorded in
the final section.

## Status summary

| Environment | PASSING | IMPLEMENTED | PLANNED | DEFERRED | NOT_APPLICABLE | Total |
|---|---:|---:|---:|---:|---:|---:|
| No Codex (`NC`) | 132 | 64 | 28 | 0 | 0 | 224 |
| Live Codex (`CX`) | 20 | 67 | 6 | 27 | 0 | 120 |
| **All cases** | **152** | **131** | **34** | **27** | **0** | **344** |

`PASSING` means the required result is exercised by the current automated
suite. A broad test may cover several adjacent cases. `IMPLEMENTED` means the
production behavior exists but the exact inventory case has not yet passed as
a separately attributable test. `PLANNED` means required production or fixture
work remains. `DEFERRED` is reserved for opt-in experiments, expensive fault
runs, benchmarks, and upgrade checks.

## No Codex cases

The following 132 cases are passing:

- NC-001 through NC-006
- NC-010 through NC-012
- NC-015, NC-018, NC-023
- NC-025 through NC-042
- NC-049
- NC-051 through NC-058
- NC-066 through NC-069
- NC-071 through NC-074
- NC-099, NC-100, NC-105, NC-106
- NC-111 through NC-115
- NC-118 through NC-125
- NC-127
- NC-131 through NC-143
- NC-149 through NC-153
- NC-159, NC-160
- NC-168 through NC-177
- NC-180 through NC-182
- NC-191 through NC-224

Coverage is supplied by `tests/test_controller.py`,
`tests/test_project_controller.py`, and the real Taskledger disposable Git
repository cases in `tests/test_acceptance.py`.

The following 28 cases remain planned because they require dedicated process
fault injection, scale/property fixtures, portability fixtures, or explicit
performance thresholds:

- NC-024, NC-050, NC-077, NC-081, NC-082
- NC-085, NC-086, NC-088, NC-090 through NC-093
- NC-095, NC-096, NC-098, NC-128
- NC-145 through NC-148
- NC-165, NC-166, NC-178, NC-179
- NC-187 through NC-190

All other NC cases are implemented but do not yet have exact attributable
coverage. This includes several crash windows whose fail-closed behavior is
covered at a neighboring boundary but not at the exact process kill point.

## Live Codex cases

The bounded opt-in suite directly passed these 20 cases on 2026-09-11:

- CX-001, CX-008 through CX-010, CX-012
- CX-028, CX-035, CX-041, CX-082
- CX-099, CX-103
- CX-112 through CX-120

CX-011, CX-049, and CX-094 have live coverage for neighboring behavior but
remain `implemented`: restart was exercised with a fresh app-server client in
one test process rather than a new supervisor process; the lifecycle exercised
the intended worker worktree without an adversarial write attempt against the
canonical checkout or ledger secrets; and the lifecycle completed in one
worker turn rather than proving forced early-stop continuation.

The 67 implemented live cases are CX-011, CX-013 through CX-019, CX-023
through CX-027, CX-029 through CX-034, CX-036 through CX-040, CX-042 through
CX-046, CX-048 through CX-053, CX-055 through CX-057, CX-060 through CX-069,
CX-072 through CX-079, CX-083, CX-090, CX-091, CX-094 through CX-098, and
CX-100 through CX-102.

CX-002 through CX-007 remain planned. They require isolated authentication,
missing executable, unavailable model, and invalid effort fixtures that do not
disturb the user's normal Codex installation.

The following 27 cases are deferred:

- CX-020 through CX-022: context compaction and fork experiments are not part
  of correctness.
- CX-047, CX-054, CX-058, CX-059: large-context and expanded sandbox/network
  experiments need separate bounded fixtures.
- CX-070, CX-071, CX-080, CX-081: sleep, runtime restart, out-of-order burst,
  and multi-turn shutdown tests need a process harness.
- CX-084 through CX-089, CX-092, CX-093: caching and attribution experiments
  follow functional completion.
- CX-104 through CX-108: representative and comparative benchmarks require
  explicit user authorization before model spend.
- CX-109 through CX-111: runtime upgrade compatibility is run when evaluating
  a specific new Codex version.

## Latest execution

The release-candidate default suite passed 113 tests and skipped the eight
explicitly opt-in live methods in 80.94 seconds. The dedicated NC-191 through NC-224 module
passed all 34 tests. The final bounded live sweep used `gpt-5.6-luna` at `low`
effort and passed all eight methods in 165.285 seconds; those methods directly
cover the 20 CX cases listed above.

| Live case group | Turns | Input | Cached input | Cache-write input | Output | Reasoning |
|---|---:|---:|---:|---:|---:|---:|
| same-thread continuation | 2 | 35,357 | 25,088 | 0 | 12 | 0 |
| restart accounting/history | 2 | 35,187 | 25,088 | 0 | 39 | 21 |
| assignment lifecycle | 2 | 115,540 | 78,592 | 0 | 1,132 | 165 |
| read-only structured review | 1 | 27,071 | 13,056 | 0 | 123 | 14 |
| two-task project + final review | 5 | 449,763 | 356,352 | 0 | 3,900 | 580 |
| nested-agent denial | 1 | 15,150 | 11,008 | 0 | 39 | 27 |
| multi-response accounting | 1 | 30,266 | 20,992 | 0 | 101 | 18 |
| project-local profile resolution | 0 | 0 | 0 | 0 | 0 | 0 |
| **Total** | **14** | **708,334** | **530,176** | **0** | **5,346** | **825** |

Admission usage for this sweep was 713,680 tokens (`input + output`). Cached
input is already a component of input and is not added again. The diagnostic
multi-response turn contained two exact upstream responses: cumulative usage
moved from zero to 30,266 input, 20,992 cached input, 101 output, and 18
reasoning tokens; the exact-response sum reconciled to that delta and precision
was `EXACT_RESPONSES`.

The bounded two-task report was generated twice with identical structured
output. It joined all 5 expected Codex turns, reported 0 missing-usage turns, 0
nested-agent calls, 0 subagent activity, and 0 untracked turns. Project wall
time was 93,801 ms, summed model-turn duration was 117,217 ms, and the union of
active model intervals was 91,815 ms. Task Ledger stored no raw provider-usage
events or worker prose for that run; provider history remained in Codex.

Historical `last`-usage totals are intentionally not reused as a ground truth
for this accounting model.
