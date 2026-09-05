# RustButVerify v0.3.0 new-spec checkpoint

> Measurement correction (2026-08-24): 113 parent token records copied into
> the three full-history worker forks were previously counted as new usage.
> Corrected full-run input is 19.25M rather than 26.50M. The planning result is
> unchanged; corrected execution input is 17.67M.

Thread: `01a02b0d-c48c-78e3-8950-684336d16f4f`  
Repository: `/Users/christophercaldwell/Code/oss/rust_but_verify`  
Window: 2026-08-22T19:58:37Z–2026-08-22T20:51:30Z

## Qualification

This is a valid v0.3.0 planning-and-execution sample:

- It began after the 2026-08-22T19:55:31Z repository checkpoint.
- The injected skill declared v0.3.0 and the installed CLI returned v0.3.0.
- Runtime turn contexts confirm Sol/high orchestration and Terra/medium workers.
- It created a new specification, five requirements, three tasks, 18 criteria, ten requirement links, and three dependency edges.
- Both approval gates were used, plan validation succeeded once, all three tasks integrated, all five requirements verified, and project completion succeeded at `8bc430ebaa8508f3fb9af96ab44c0f821e13572a`.

## Usage

| Phase/cohort | Input | Cached input | Output |
|---|---:|---:|---:|
| Planning/closure outside assignment windows, Sol/high | 1.58M | 1.50M | 14,629 |
| Execution, Sol/high | 6.99M | 6.87M | 15,378 |
| Execution, Terra/medium | 10.68M | 10.35M | 60,419 |
| Full run, matched cohorts | 19.25M | 18.72M | 90,426 |

There were 229 fresh token events and zero context compactions. Cached input
was 97.2% of input. Planning/closure outside task windows accounted for about
8.2% of matched input; execution accounted for 91.8%.

## Task-level result

| Task | Criteria | Files / lines | Submissions / rejected | Execution input |
|---|---:|---:|---:|---:|
| Core API and snapshot correctness | 7 | 9 / 837 | 2 / 1 | 3.63M |
| Documentation and examples | 5 | 11 / 214 | 2 / 1 | 2.13M |
| Benchmarks and final validation | 6 | 6 / 273 | 2 / 1 | 11.91M |

The benchmark/validation task consumed 67.4% of execution input despite
changing only 20.6% of the lines. This is direct evidence that Git diff size is
not a sufficient complexity denominator: repeated optimized measurements,
concurrency stress, packaging checks, and independent reruns are high-effort
but low-diff work.

Every task's initial submission was rejected and corrected. The 50% submission rejection rate is a real quality/usage cost, but the reviews caught locked-spec omissions: missing equivalence coverage and removed existing tests/docs in core; incomplete teaching examples in documentation; and deletion of the preserved benchmark baseline in validation. The review layer therefore produced visible benefit rather than ceremonial overhead.

## Workflow signals

- One cold-start `project recover`, one compact `project resume`, and no compactions.
- Six focused `submission review-context` reads for six durable submissions.
- Each worker loaded its assignment context once. Durable submission counts are authoritative where shell wrappers hide literal worker commands.
- No blocker, uncertain operation, failed integration, or plan-validation retry occurred.
- v0.3.0 did not surface rejected-submission correction text in the worker context overlay. The primary had to relay the exact recorded correction to the same worker. This is a concrete residual token-efficiency and reliability defect worth fixing.

## Comparison limits

Three earlier RustButVerify tasks were subsequently identified and verified:

| Thread | Pre-v0.3 classification | Primary / worker | Tasks / criteria | Lines | Submissions / rejected | Non-auto-review input |
|---|---|---|---:|---:|---:|---:|
| `01a0259b…` | v0.1 planning, v0.2 execution | Sol/medium, direct primary | 4 / 24 | 1,119 | 4 / 0 | 5.86M |
| `01a02763…` | v0.2, with one reopened task/integration failure | Terra/high, direct primary | 3 new / 15 | 1,272 | 4 / 0 | 12.93M |
| `01a027ce…` | v0.2.1→v0.2.3, recovery-defect interruption | Sol/medium + Terra/medium | 3 / 18 | 1,206 | 7 / 4 | 20.87M |

All three are genuine pre-v0.3.0 evidence, but none is an exact model match for the new run's Sol/high primary. The first also includes seven-gate/v0.1 planning, the second used a different direct-implementation model, and the third spans a Taskledger recovery defect and patch. Auto-review/low usage is excluded from the non-auto-review totals because the new run used primary review.

The `01a027ce…` task portfolio is the closest structural comparator: both runs have three sequential API/documentation-validation tasks, 18 criteria, about 1.2–1.3K changed lines, and Terra/medium workers. It yields two different results that must not be collapsed:

- **Planning/orchestration outside assignment windows:** 6.00M Sol/medium input before v0.3.0 versus 1.58M Sol/high after, a 73.7% reduction despite the newer run using higher primary effort. Broad recover calls fell from seven to one and worker-context reads from eight to three.
- **Execution:** 14.87M matched primary/worker input before versus 17.67M
  after, an 18.8% increase. The new benchmark task alone used 11.91M while
  performing optimized timing, allocation, concurrency-stress, packaging, and
  independent reruns. Its 29.5-minute execution window was more than three
  times the old validation task's 9.1 minutes, so this increase cannot be
  assigned to Taskledger overhead.

A narrower core-API sensitivity check is favorable: the two old
API/authorization tasks used 7.98M input across 768 changed lines and 12
criteria; the new consolidated core task used 3.63M across 837 lines and seven
criteria. That is 54.5% lower total input and 58.2% lower input per changed
line, but task boundaries and primary effort still differ.

The corrected full-run 6.42M input per integrated task is 21.3% above the
corrected first Financial Tracker v0.3.0 queue's 5.29M, but this is not an
effectiveness estimate. The benchmark task shows why neither task count nor
changed lines can make unlike portfolios equivalent.

## Updated inference

The pre-v0.3 evidence supports a large reduction in Taskledger's
planning/state-replay overhead, but it does not support claiming a universal
50% reduction in complete-project tokens. Together with the later v0.4 UI run,
the best current point estimate for comparable ordinary implementation work is
about **30%**, with a wide **10–50%** plausible range. For Taskledger-specific
planning/orchestration overhead, the best observed estimate remains roughly
**70%**. Benchmark-heavy or unusually strict-review runs can consume more total
tokens even when ledger overhead falls.
