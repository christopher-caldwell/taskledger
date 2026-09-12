# Historical records

Use the [current documentation index](../README.md) for operating instructions.
The records below preserve original decisions, observations, and evidence.
Their embedded paths and commands refer to the original checkout or machine.

| Record | Contents |
|---|---|
| Original implementation | [Prompt](initial-implementation/taskledger-initial-implementation-prompt.md), [handoff](initial-implementation/taskledger-implementation-handoff.md), [slice checklist](initial-implementation/IMPLEMENTATION_CHECKLIST.md) |
| Controller discovery | [Discovery handoff](controller-discovery/CONTROLLER_DISCOVERY_HANDOFF.md) |
| Benchmark readiness | [Investigation and resolution](benchmark-readiness/BENCHMARK_BLOCKER_HANDOFF.md), [recorded gate decision](benchmark-readiness/BENCHMARK_READINESS_REPORT.md) |
| Equipment Lending run 7 | [Lifecycle findings](incidents/equipment-lending-run-7/EQUIPMENT_LENDING_RUN_7_CLI_FIRST_FINDINGS.md), [bug analysis and later corrections](incidents/equipment-lending-run-7/BUG_PREPARATION_STALE_SPECIFICATION_REVISION.md) |
| Token efficiency | [Historical benchmark cohorts](../../benchmarks/token-efficiency/README.md), [August 22 consensus audit](../../audits/token-efficiency-fixes-2026-08-22/final-consensus-audit.md) and its sibling reviewer reports/configuration |
| Console design experiments | [Scope and reproduction](../taskledger-ui-architecture/README.md#evidence-and-its-limits), with original program, results, and output retained together |

Unfinished original conformance work is carried into the [testing backlog](../TESTING_BACKLOG.md).
Do not overwrite interim checkpoints with final results or discard failed runs.

The ignored `.taskledger-validation-evidence/` directory contains local paid
validation artifacts cited by the readiness report. It is preserved separately
from this tracked archive. Back it up before removing a checkout; it is not a
cache. Durable `.taskledger/` project state is also outside documentation cleanup.
