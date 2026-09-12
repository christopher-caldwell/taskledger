# Benchmark readiness blocker investigation

> Historical record, archived 2026-09-12. Paths, commands, and status statements below describe the original work and may no longer apply. Use the [current documentation index](../../README.md) for operating guidance.

## Objective

Investigate the remaining benchmark readiness blocker on branch
`feat/benchmark-readiness`, determine whether it is a scheduler defect, a live
fixture design problem, or an acceptance evidence problem, and recommend the
smallest justified next step.

Do not run Equipment Lending. Do not weaken review, completion, accounting, or
capability controls. Do not change the expected result merely to make a test
pass. Start with deterministic and read only analysis. Another paid live run is
appropriate only after the fixture can reliably exercise the intended timing.

## Starting point

Branch: `feat/benchmark-readiness`  
Implementation commit: `78c99a7557874e63031e79a9d2d7c293b284acd9`  
Baseline: `e807ac5b70fc8a7c382674f050e26118c0740aaf`  
Installed runtime during validation: `codex-cli 0.153.4`  
Live profile: `gpt-5.6-luna`, low effort

Read these files first:

1. `docs/BENCHMARK_READINESS_REPORT.md`
2. `docs/BENCHMARK_PROTOCOL_V1.json`
3. `docs/CONTROLLER_IMPLEMENTATION.md`
4. `docs/CONTROLLER_TEST_PLAN.md`, especially CX-121 through CX-124
5. `tests/test_controller_live.py`, lines 410 through 576
6. `src/taskledger/controller/project.py`, especially scheduling and target execution
7. `src/taskledger/controller/supervisor.py`, especially worker and reviewer capacity
8. `src/taskledger/controller/reporting.py`

## Observed blocker

The bounded live suite ran with:

```sh
TASKLEDGER_LIVE_CODEX=1 \
TASKLEDGER_LIVE_MODEL=gpt-5.6-luna \
TASKLEDGER_LIVE_EFFORT=low \
python3 -m unittest tests.test_controller_live -v
```

Seven of eight methods passed. The failing method was:

```text
test_cx099_cx103_cx118_cx119_cx120_live_multi_task_project
```

The disposable two task project completed. Both files were correct, both tasks
integrated, final review completed, requirements were verified, and the project
reached `COMPLETED`. Two worker turns overlapped each other, and the single
reviewer semaphore serialized the two submission reviews. The failure occurred
at this assertion:

```python
self.assertTrue(any(
    worker["started_at"] < reviewer["finished_at"]
    and reviewer["started_at"] < worker["finished_at"]
    for worker in worker_turns
    for reviewer in reviewer_turns
), "expected an independent worker turn to overlap a submission-review turn")
```

No worker turn happened to overlap a reviewer turn in that run. The scheduler
permits this overlap, but the tiny tasks can both finish before the first review
starts. A passing rerun based on timing luck would not establish reliable
evidence for CX-123.

The test failed before printing and preserving the five project turn
allocations. Its disposable ledger was then removed. The validation report
therefore labels the live sweep accounting as `PARTIAL/MISSING`. The known
subtotal from the other nine printed turns is:

| Dimension | Observed subtotal |
|---|---:|
| Input | 292,876 |
| Cached input | 203,008 |
| Cache write input | 0 |
| Output | 1,353 |
| Reasoning | 314 |
| Admission, input plus output | 294,229 |

Do not estimate the missing five turns.

## Questions to answer

1. Does the production scheduler begin review as soon as one submission is
   ready while another independent target remains active, or does a barrier in
   `ProjectController` delay review until all active targets finish?
2. If overlap is permitted, can the existing live fixture force it through a
   deterministic controller observable condition without asking a model to
   sleep or relying on response speed?
3. Would a fake runtime with real Git and Task Ledger prove the scheduling
   invariant more directly, leaving the live gate to prove only the provider
   contract?
4. Is worker and reviewer overlap actually required by CX-123, or is the true
   requirement independent capacity plus evidence that review can proceed
   before the wave completes? Do not alter the contract without documenting a
   proposed deviation and obtaining approval.
5. How should a live fixture preserve its report and accounting rows even when
   a later assertion fails?
6. Which remaining blocked gates in `BENCHMARK_READINESS_REPORT.md` are release
   blockers, and which are evidence gaps around already implemented behavior?

## Investigation sequence

1. Trace `_run_loop`, `_run_target`, worker capacity, reviewer capacity, and
   target event creation. Produce a short event sequence for two independent
   targets where one submits before the other worker finishes.
2. Add or refine a deterministic fake runtime test that controls worker and
   reviewer completion with events. Use real Task Ledger and Git if practical.
   The test should fail if review waits for the other worker unnecessarily.
3. Determine how to make the live fixture reliably retain one active worker
   while review begins. Prefer controller controlled gates or fixture work over
   model instructions about timing.
4. Move report generation and usage printing into a `finally` safe evidence
   path, or preserve the disposable ledger on failure, so failed validation is
   still measurable.
5. Review CX-121 and CX-122 coverage. Confirm whether a real failed provider
   turn and executable ambient capability sentinels are still missing.
6. Review the exact CLI and history coverage for AC-6. The current branch has a
   passing no Codex subprocess test and provider fixtures for missing,
   unexpected, and later turns, but constructor failure, version probe failure,
   timeout, and partial pagination may still need separate cases.
7. Run the complete non live suite. Run a paid live method only after the new
   fixture has a deterministic reason to produce the required overlap.

## Constraints

- Python remains the only persistent coordinator.
- Models perform bounded Task Creator, Worker, and Reviewer jobs only.
- Preserve same thread continuation and independent review.
- Preserve guarded integration and requirement based completion.
- Do not add model delegation, another routing abstraction, provider storage
  parsing, a telemetry service, or a reporting model.
- Keep input plus output as the token admission basis.
- Include failed and uncertain spend when observed.
- Keep partial or missing accounting explicit.
- Do not edit user global Codex configuration.
- Do not persist prompts, transcripts, commands, patches, reasoning, secrets,
  provider event bodies, or private Codex storage.
- Do not commit or push until the complete diff and gate evidence are reviewed.

## Expected deliverable

Return a short investigation report containing:

- root cause with code and test references;
- whether production behavior is wrong or only the fixture is unreliable;
- the recommended fix and alternatives considered;
- tests added or proposed, with their exact assertions;
- expected additional live spend before running it;
- an updated AC-1 through AC-8 gate matrix;
- a clear release decision: ready for an authorized lending run, or blocked with
  the exact missing evidence.

If implementation is justified, make the smallest scoped change on this branch,
run the non live suite, preserve all live evidence even on failure, and stop
before Equipment Lending.

## Resolution under the approved experimental release bar

The user subsequently approved a narrower four-gate confidence pass for one
representative experiment. This changes the evidence required before that
experiment, not the production guarantees or the historical inventory result:

- scheduler concurrency and rejection/correction are proved causally with the
  production controller, real Taskledger/Git, and an event-controlled external
  runtime;
- real Codex is required to prove automatic same-thread continuation and the
  provider integration, but worker/reviewer overlap no longer depends on live
  response timing;
- earlier AC-1 through AC-8 fixtures and all 368 original inventory cases are
  not required to be closed for this experimental release;
- the earlier live overlap failure remains failed and is not rewritten as a
  pass.

The scheduler investigation confirmed that each selected target owns its own
assignment supervisor, worker and reviewer semaphores are independent, and the
outer gather is only the next-wave barrier. The original blocker was therefore
a fixture/evidence-retention defect, not a scheduler barrier. NC-246 now holds
worker B at an observed runtime dispatch, lets worker A submit, and requires
review A to reach the runtime before B is released. It fails under a bounded
timeout if review waits for B.

The confidence pass also found two narrow production defects:

1. an unresolved adapter inspection could discard already observed partial
   usage; terminal UNKNOWN/FAILED reconciliation now carries that observation
   while semantic uncertainty remains fail-closed;
2. project-wide worker/reviewer turn caps were correctly checked at paid
   admission but were also checked by the non-spending global lifecycle gate,
   which could pause after the last permitted worker turn before integration or
   final review. The duplicate global checks were removed; the admission checks
   remain.

An executable MCP sentinel exposed a Codex 0.153.4 command-line overlay detail:
an `enabled=false` dotted override replaces the named server table and loses
its required transport. Taskledger now inventories servers with the public
`codex mcp list --json` command, reconstructs only the minimum non-secret
transport in each disabled override, and fails closed on invalid or unsupported
inventory. The disabled override uses a harmless non-secret placeholder
transport rather than copying configured arguments, URLs, or environment. The
sentinel passes on both start and resume, and a zero-model check
against the installed six-server inventory reported zero running servers.

Paid validation was capped before dispatch at two attempts total, fourteen
turns, 1,200,000 admission tokens, and 600 seconds. Attempt
`310da87f35224f4ca75acb2c3bed93f6` disconnected before any provider turn and
retains zero usage. The one corrected attempt,
`93135489878d4cb09b5ae6a3073443d4`, completed the controller project in six
turns with complete provider reconciliation: 325,490 input, 232,448 cached
input (already included in input), 0 cache-write input, 3,443 output, and 673
reasoning tokens; admission usage was 328,933. Its test wrapper then failed on
a secondary observer key that the callback had not populated. The artifact
truthfully remains `FAILED`, while its retained report records `COMPLETED`, two
successful integrations, two accepted independent submission reviews, final
review, and requirement completion. Provider-history diagnostics, performed
without starting a turn, confirmed two completed turns on one worker thread,
distinct turn IDs, prompt sizes of 2,088 then 397 serialized bytes, no immutable
assignment markers in the second prompt, and separate successful partial and
completion commit commands. The callback is repaired for future validation;
the paid allowance was not reset and no third run was made.

The retained local artifacts are under
`.taskledger-validation-evidence/benchmark-readiness/` and are intentionally
gitignored. `BENCHMARK_READINESS_REPORT.md` contains the final R1-R4 decision.
