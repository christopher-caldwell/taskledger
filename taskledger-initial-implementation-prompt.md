You are implementing Taskledger v1 in this repository. Work directly in the repository and deliver the complete working tool, not a design proposal or partial scaffold.

Read these files completely before writing code:

1. `taskledger-product-spec.md`
2. `taskledger-technical-spec.md`
3. `taskledger-implementation-handoff.md`

Use this authority order:

1. The product specification controls required behavior.
2. The technical specification controls implementation details.
3. The implementation handoff controls build order, state machine discipline, and test gates.

The ambiguity decisions are already resolved. Do not reopen them or substitute a different architecture. If you find a real remaining conflict, cite the exact sections and stop before inventing behavior. Otherwise choose the smallest implementation consistent with the documents and continue.

## Goal

Build the complete local `taskledger` command line application described by the specifications.

The required architecture is:

- CPython 3.11 or later
- Python standard library only at runtime
- `unittest` for tests
- SQLite for durable state
- the installed Git executable invoked with argument arrays and `shell=False`
- one external Git branch and worktree per assignment
- one orchestrator credential per project and one scoped worker credential per assignment
- no server, daemon, web interface, network service, or model integration
- POSIX and Git 2.20 or later as the v1 platform target

Keep the implementation inside this lane. Do not add autonomous planning, worker launching, LLM calls, pull requests, CI support, deployment, cloud synchronization, chat, plugins, telemetry services, or automatic Git conflict resolution.

## How to work

Follow the dependency ordered slices in `taskledger-implementation-handoff.md`, starting with Slice 0. Complete one slice at a time. Add its tests and make them pass before starting the next slice.

For each public command, implement in this order:

1. Define and test the complete request contract, including unknown field rejection.
2. Add authorization tests.
3. Add pure state transition and precondition tests.
4. Add database queries and transaction behavior.
5. Add the service method.
6. Wire the command into the CLI.
7. Add a test that invokes the public CLI and checks its JSON envelope.
8. Add failure, stale state, and idempotency tests where relevant.

Do not build every table first and defer behavior until later. Finish vertical behavior slices so state and authorization errors appear early.

Maintain a short implementation checklist that mirrors the handoff slices. Update it as work progresses. Do not mark a slice complete until its gate passes.

## Rules that must remain true

- Workers cannot create or revise the plan, verify submissions or requirements, integrate work, resolve blockers, change the canonical branch, or complete the project.
- Submission is only a claim. Acceptance is not task completion. A task completes only after the exact accepted commit is integrated into the confirmed canonical branch.
- Dependencies count only completed integrations that remain reachable from the canonical branch.
- Project progress comes from current requirement verification, not task percentage.
- Requirement verification invalidation is scoped to that requirement and its own covering tasks and affected specification reviews. Do not invalidate every requirement because an unrelated global snapshot changed.
- Specification changes after planning create a hard review gate. Taskledger never infers affected items.
- Blockers close only through an explicit orchestrator action.
- All blocker effects use the one shared domain calculation defined in the technical specification.
- Ledger only mutations are atomic and include an audit event.
- Git mutations never run inside a long SQLite transaction. They use the durable operation journal and deterministic recovery proofs.
- When a Git result cannot be proven, stop with a recovery gate. Never guess, reset, stash, switch branches, delete work, or resolve conflicts automatically.
- Successful assignment worktrees and branches remain in place in v1.
- Every response is one deterministic JSON object on stdout. Diagnostics go to stderr and never reveal credentials.
- Unknown input fields are rejected. Worker supplied evidence commands are stored as text and never executed.

## Testing requirements

Use temporary directories, temporary SQLite databases, and real temporary Git repositories. Do not mock Git for integration and acceptance coverage.

At minimum, implement:

- unit tests for every state transition, authorization rule, blocker rule, plan rule, fingerprint rule, verification rule, and completion rule
- database tests for migrations, constraints, revision history, concurrency, stale state handling, credentials, audit events, and completion invalidation
- Git tests for initialization, detached HEAD, arbitrary canonical branches, worktrees, checkpoint commits, rejection and correction, merge success, safe merge failure, dirty repositories, idempotent adoption, external rewrites, and operation recovery
- failure injection at every operation journal boundary named in the technical specification
- public CLI acceptance tests for Product Specification Scenarios A through L
- a traceability table proving that all 66 numbered product requirements have automated coverage

Use golden fixtures for plan fingerprint bytes and the complete recovery JSON shape. Stable output must not depend on database row order.

## Completion standard

Do not stop after scaffolding, one workflow, or a passing happy path. Continue until every command in Technical Specification Section 10 exists and every Definition of Done condition in Section 31 passes.

Before reporting completion:

1. Run the full test suite from a clean process.
2. Run all public CLI acceptance scenarios.
3. Confirm that no excluded feature from Technical Specification Section 30 was added.
4. Confirm that no worker credential can reach an orchestrator service, including through direct service calls in tests.
5. Confirm that recovery can continue from repository and Taskledger state without conversation history.
6. Review the final file tree for accidental secrets, generated databases, worktrees, or test artifacts.

In your final report, state what was implemented, list the verification commands and results, identify any remaining limitation, and point to the requirement traceability table. Do not claim completion if any required test or scenario is missing.
