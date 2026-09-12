# Taskledger documentation

Start with the [run guide](RUN_GUIDE.md) for installation, operation, updates,
and recovery. The [repository README](../README.md) provides a quick introduction.

| Area | Current references |
|---|---|
| Operator guidance | [Run guide](RUN_GUIDE.md), [installation contracts](AI_ORCHESTRATED_TOOL_INSTALLATION.md) |
| Required behavior and design | [Product specification](specifications/taskledger-product-spec.md), [technical specification](specifications/taskledger-technical-spec.md) |
| CLI and installed skill | [Command registry](reference/COMMAND_REGISTRY.md), [bundled command reference](../skills/taskledger/references/commands.md) |
| Controller | [Implementation](CONTROLLER_IMPLEMENTATION.md) |
| Console | [Architecture and specs](taskledger-ui-architecture/README.md), [implementation status](taskledger-ui-architecture/IMPLEMENTATION_STATUS.md), [headless acceptance](taskledger-ui-architecture/HEADLESS_ACCEPTANCE.md) |
| Verification | [Traceability](development/TRACEABILITY.md), [testing backlog](TESTING_BACKLOG.md), [controller plan](CONTROLLER_TEST_PLAN.md), [coverage](CONTROLLER_TEST_COVERAGE.md) |
| Release preparation | [Publishing checklist](development/PUBLISHING_CHECKLIST.md) |
| Benchmark contracts | [Protocol](BENCHMARK_PROTOCOL_V1.json), [comparison example](BENCHMARK_COMPARISON_EXAMPLE.json) |

The product and technical specifications govern behavior and design. Test
reports describe recorded executions; they do not automatically certify a later
checkout. Keep unresolved work in the testing backlog and relevant acceptance plan.

Historical build instructions, investigations, audits, and experiment records
are indexed in the [archive](archive/README.md). They explain prior decisions
and observations and are not current operating instructions.

When moving documents, update links and run `python3 scripts/check_docs.py`.
Reference documents are grouped under `specifications/`, `reference/`, and
`development/`. The root contains only the project README among Markdown files.
Update document consumers whenever a path changes.
