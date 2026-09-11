# Controller verification coverage

This file records the implementation and execution status of every case in
`CONTROLLER_TEST_PLAN.md`. It is a coverage ledger, not a substitute for the
tests. Status reflects the current checkout and the latest test run recorded in
the final section.

## Status summary

| Environment | Passing | Implemented | Planned | Deferred | Not applicable | Total |
|---|---:|---:|---:|---:|---:|---:|
| No Codex (`NC`) | 98 | 64 | 28 | 0 | 0 | 190 |
| Live Codex (`CX`) | 11 | 67 | 6 | 27 | 0 | 111 |
| **All cases** | **109** | **131** | **34** | **27** | **0** | **301** |

`Passing` means the required result is exercised by the current automated
suite. A broad test may cover several adjacent cases. `Implemented` means the
production behavior exists but the exact inventory case has not yet passed as
a separately attributable test. `Planned` means required production or fixture
work remains. `Deferred` is reserved for opt-in experiments, expensive fault
runs, benchmarks, and upgrade checks.

## No Codex cases

The following 98 cases are passing:

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

The bounded opt-in suite directly passed these 11 cases on 2026-09-11:

- CX-001, CX-008 through CX-010, CX-012
- CX-028, CX-035, CX-041, CX-082
- CX-099, CX-103

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

The complete default non-live suite passed 78 tests and skipped the five
explicitly opt-in live cases in 78.95 seconds. It includes real Taskledger and
disposable Git integration plus subprocess termination at both unacknowledged
and acknowledged dispatch boundaries.

On 2026-09-11, the final app-server transport was exercised with
`gpt-5.6-luna` at `low` effort. All three opt-in invocations passed: the
assignment lifecycle passed in 38.15 seconds, the three protocol contract tests
passed in 19.20 seconds, and the live two-task project passed in 88.60 seconds.

| Case/turn | Role | Input | Cached input | Output | Reasoning |
|---|---|---:|---:|---:|---:|
| assignment lifecycle worker | worker | 85,971 | 48,384 | 462 | 117 |
| assignment lifecycle review | reviewer | 56,171 | 34,304 | 844 | 113 |
| worker thread, turn 1 | worker | 14,159 | 6,912 | 6 | 0 |
| worker thread, turn 2 | worker | 19,162 | 13,056 | 6 | 0 |
| restart, before | worker | 14,175 | 5,888 | 18 | 9 |
| restart, after | worker | 14,210 | 13,056 | 7 | 0 |
| read-only structured review | reviewer | 27,064 | 13,056 | 115 | 19 |
| multi-task project, task A | worker | 70,017 | 50,432 | 516 | 95 |
| multi-task project, task B | worker | 88,263 | 70,400 | 539 | 103 |
| multi-task project, review A | reviewer | 76,421 | 35,328 | 1,241 | 135 |
| multi-task project, review B | reviewer | 35,552 | 15,104 | 592 | 61 |
| multi-task final review | requirement reviewer | 79,664 | 55,552 | 929 | 257 |
| **Total** |  | **580,829** | **361,472** | **5,275** | **909** |

All 12 turn records had complete usage. The controller computes admission
tokens as input plus output (586,104 here); cached input and reasoning are kept
as separate immutable telemetry fields rather than added again.
