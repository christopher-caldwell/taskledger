# Benchmark readiness validation report

Date: 2026-09-11  
Baseline and fetched `origin/main`: `e807ac5b70fc8a7c382674f050e26118c0740aaf`  
Branch: `main` (synchronized with `origin/main` before edits)  
Runtime: `codex-cli 0.153.4`; `gpt-5.6-luna`, low effort for bounded live checks  
Scope: readiness development and disposable validation only. Equipment Lending was not run.

## Decision

**BLOCKED — not yet READY FOR AUTHORIZED LENDING RUN.** The implementation is
materially closer, but AC-1/AC-2/AC-4/AC-5/AC-6/AC-8 still require the exact
fixtures listed below. CX-123 also failed its worker/reviewer overlap assertion
in this validation run. A rerun-by-chance is not accepted as evidence.

## Gate matrix

| Gate | Status | Production locations | Exact tests/evidence | Residual limitation |
|---|---|---|---|---|
| AC-1 | BLOCKED | `controller/supervisor.py`, `controller/project.py`, `controller/taskledger_adapter.py` | Existing fake/real-ledger lifecycle and live same-thread tests pass | No single E1 fixture yet proves the entire early-stop → rejection → correction → final-review chain plus a negative requirement fixture |
| AC-2 | BLOCKED | `controller/app_server.py`, `controller/journal.py`, `controller/model.py` | NC-225–NC-228 cover failed usage, numerical oracle, partial reconciliation, and incomplete admission | Exact lost-ack/crash-before-terminal and unavailable/incompatible restart-generation fixtures remain |
| AC-3 | PASS | `controller/reporting.py` | NC-229–NC-231 and NC-236/238 reconcile counts/tokens and validate cache vectors | Historical legacy data remains labeled legacy; no valuation was supplied |
| AC-4 | BLOCKED | `controller/reporting.py`, controller events | NC-232–NC-234 fixed-clock oracle passes | Immutable historical task/blocker revision fixture and requirement-level finding fixture remain |
| AC-5 | BLOCKED | `controller/profiles.py`, `controller/app_server.py`, runtime identity | Strict app-server startup accepted the process overrides; live worker/reviewer/nested-agent methods passed | Disposable executable MCP/hook sentinel after both start and resume remains |
| AC-6 | BLOCKED | `cli.py`, `controller/reporting.py` | Provider failure/read-only/determinism tests pass; constructor failure now falls back | Exact subprocess CLI fixtures for missing executable, constructor/version failure, timeout, partial history, and unexpected/later turns remain |
| AC-7 | PASS | `controller/benchmark.py`, `docs/BENCHMARK_PROTOCOL_V1.json` | NC-242–NC-244; synthetic comparison example | Historical comparison is insufficient until a real normalized baseline is supplied |
| AC-8 | BLOCKED | test plan, coverage ledger, this report | Non-live suite passed; live suite passed 7/8 methods | CX-123 overlap assertion failed; CX-121–CX-124 exact gates remain deferred |

## Commands and results

```text
git fetch origin main
python3 -m unittest discover -s tests -v
  132 tests, 8 live-only skips, 0 failures, 79.865 s

TASKLEDGER_LIVE_CODEX=1 TASKLEDGER_LIVE_MODEL=gpt-5.6-luna \
TASKLEDGER_LIVE_EFFORT=low python3 -m unittest tests.test_controller_live -v
  8 methods: 7 passed, 1 failed, 160.622 s
  failure: two-task project had no observed worker-turn/reviewer-turn overlap
```

The live suite's printed, attributable subtotal before the failing method's
unprinted project rows was 9 turns: 292,876 input, 203,008 cached input, 0
cache-write input, 1,353 output, and 314 reasoning tokens. Admission usage was
294,229 tokens. The five-turn two-task project also incurred usage and completed
semantically, but its fixture failed before printing those allocations and then
removed the disposable ledger. Therefore this validation run's accounting is
**PARTIAL/MISSING** and no exact grand total is claimed.

No model performed scheduling, waiting, accounting, reporting, or comparison.
No representative benchmark or historical savings claim was made.
