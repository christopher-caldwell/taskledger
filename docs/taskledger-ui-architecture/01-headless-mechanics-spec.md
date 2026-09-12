# Task Ledger: headless console mechanics

**Specification:** TL-CONSOLE-1, Part 1 of 2  
**Status:** proposed implementation specification; source-reviewed with executable contract experiments  
**Source baseline:** `christopher-caldwell/taskledger`, commit `c1453448436ec8d46d502eb9605876cca026e3fe` (`main` when inspected)  
**Prepared:** September 12, 2026 UTC / September 11, 2026 America/Denver  
**Companion:** [02-textual-display-spec.md](02-textual-display-spec.md)

## 1. Decision and purpose

Build a headless application interface around Task Ledger’s existing execution machinery. Make Textual the first display adapter. Use **in-process, event-driven observation**, not a database poller, a daemon, a local web server, or a second orchestration system.

The intended deployment is one person, one local project, one process, and one visual interface. That process may contain multiple model-worker sessions because those are already part of Task Ledger. Multiple model workers are not multiple UI clients.

The durable ledger remains authoritative. The controller remains responsible for model dispatch, review, integration, limits, and recovery. A display can request an operation and observe its result; it cannot become the authority for that operation. The same mechanics must work with no Textual installation.

**The reusable investment is an application contract, not a web API.** A later lightweight web adapter should reuse the query objects, operation handlers, lifecycle owner, and snapshot feed. Its routes, HTML, browser connection, and security controls would be new display/transport work—not a rewrite of the scheduler.

This specification makes two corrections to the preceding discussion. SQLite remains useful for normal authoritative reads, not just crash recovery. And starting the controller as a Textual-managed worker would couple execution to a UI lifecycle. Neither is necessary to obtain immediate event-driven updates.

### 1.1 Evidence terminology

**Observed** means supported by the inspected repository or cited primary documentation. **Decision** means a proposed design choice argued here. **Demonstrated** means exercised by the supplied standalone experiments. **Required verification** means an acceptance test that must run against the actual implementation before it can be called complete.

The 26 supplied experiments use a synthetic ledger and real Python/SQLite concurrency primitives. They are not a test run of Task Ledger, Textual, Codex, or a finished UI. Conditional correctness arguments are not a formal verification of the whole application.

### 1.2 Source precedence and boundaries

The current user decisions are: visual convenience, Textual first, eventual web reuse, no multiple-client requirement, an emitter connected to execution rather than SQLite polling, and strict separation of mechanical and display work.

The older attached functional specification describes a model as the orchestrator. Current controller documentation explicitly makes Python the persistent orchestration loop. This design follows the user’s newer architecture and the current implementation for orchestration ownership; it does not silently treat the old model-orchestrator description as current. Existing requirements, verification, canonical-branch, evidence, and integration rules are preserved. [R1, R2]

The inspected baseline is a remote commit. Uncommitted files on the user’s computer were not available. Implementation must inspect the actual worktree and reconcile source drift without resetting or discarding it.

## 2. What the current code actually gives us

| Observed source | Relevant behavior | Architectural consequence |
|---|---|---|
| `pyproject.toml` | Python 3.11+, CLI entry point, no declared third-party runtime dependencies | Keep the headless foundation standard-library-only; make Textual optional. [R3] |
| `cli.py::run_project_controller` | Creates the async execution task, owns signal handling, interruption/reconciliation, runtime closure, and calls `asyncio.run` | Extract lifecycle ownership; do not call this loop-owning wrapper inside a Textual event handler. [R4] |
| `prepare_project_command`, `start_prepared_project` | Preparation and approval logic is mixed with CLI parsing, connection lifetime, printing, and execution | Move reusable operations inward while keeping current CLI envelopes and approval checks. [R4] |
| `cli.py::dispatch` | `project.show` invokes preflight despite being a show operation | A console query cannot simply call a “show” command and assume it has no side effects. [R4] |
| `Service._execute_check` | Uses blocking `Popen.communicate`; declared check timeouts can be 1–3,600 seconds | An async UI wrapper alone does not keep the display responsive. Isolate its event loop from these waits. [R5] |
| `Service.audit` | Writes durable domain events, frequently inside a transaction | Do not emit a committed-state notification from this method before the surrounding transaction commits. [R5] |
| `Journal` | Stores runs, sessions, turns, grants, targets, accounting, and controller events | Reuse these facts instead of building a second event-sourced domain model. [R6] |
| `Journal.acknowledge_turn` and `complete_turn` | Change useful console state without a complete existing live-notification API | Hook the actual mutation boundaries, not just existing calls to `append_event`. [R6] |
| `db.connect` | Configures SQLite and executes schema/migration work | Separate an explicit initialization/migration step from read-only snapshot queries. [R7] |
| `ProjectController.run` | Takes a project lock inside execution, after callers may have created run state | Keep one lock owner and ensure the new admission path does not introduce nested locking or double starts. [R8] |
| `controller_report` | Reads substantial history and computes accounting/review/scheduling aggregates | Keep the full report on demand, not in the hot repaint path. [R9] |
| `TaskledgerLedgerAdapter` and `WorkerBroker` path | Worker tools and controller actions call ledger service operations | A hook only around user-facing commands would miss changes made by model workers. [R1, R10] |

The existing start path contains multiple durable steps: plan application, validation, run creation, and preparation approval. They must not be described as one atomic transaction merely because each step uses transactions internally. On an error after an earlier committed step, the console must show the committed result and the failure; it must not announce a complete rollback or blindly retry. [R4]

## 3. Scope

### 3.1 Included

Part 1 provides typed application queries and operation requests, reusable lifecycle handling, explicit execution change notifications, immutable cached console snapshots, a bounded in-process feed, and a thread-safe host adapter. It also specifies integration points and tests for preparation/start/resume, safe pause, answering questions, resolving blockers, and extending budgets.

It preserves the existing CLI as a non-visual adapter. A CLI command can continue to wait for a whole run; a UI operation can return an admission receipt and observe the continuing run. Both invoke the same underlying operation.

### 3.2 Excluded

There is no network service, remote attachment, multiple-client support, database watcher, periodic database refresh, event broker, event-sourcing conversion, generic CQRS framework, new ORM, automatic model usage for display, or scheduler redesign. The UI does not edit the approved execution policy, change model roles during a run, or approve worker submissions outside existing review rules.

Full model conversation streaming, reasoning display, raw provider-event storage, automatic valuation in currency/credits, dependency-graph visualization, and a general task editor are not in this first implementation. Existing deterministic reports remain accessible on demand.

## 4. Separation and deployment topology

### 4.1 Dependency direction

```text
CLI adapter                  Textual adapter              future HTTP/HTML adapter
     \                            |                               /
      +---------------- application contract -------------------+
                                   |
                    commands / queries / lifecycle / observation
                                   |
              existing Service + Journal + ProjectController
                                   |
                         SQLite / Git / AgentRuntime
```

The shared contract contains ordinary Python values and protocols. It contains no widgets, CSS, terminal keys, HTTP requests, routes, status codes, or browser concepts. Existing core/controller modules may depend on a small framework-free notification protocol; they must not import Textual or the console host.

### 4.2 Runtime topology for Textual

```text
ONE LOCAL PROCESS

Main thread / UI asyncio loop          Engine thread / engine asyncio loop
--------------------------------       -----------------------------------
Textual widgets and navigation         Application operation admission
Display formatting                     ProjectController and supervisors
App-level feed consumer                Service, Journal, AgentRuntime
OS signal routing                      SQLite connection ownership
             |                                      |
             +---- immutable requests / receipts -->+
             +<--- latest immutable snapshot --------+
                      in-process bridge
```

**Decision:** use one dedicated, non-daemon engine thread for interactive mode. All service, journal, SQLite, and runtime objects used by that engine are created, used, and closed in that thread. Textual owns the main thread. The host is a thread boundary, not another scheduler.

This is a measured compromise with the existing synchronous code. It prevents a blocking check or Git call from occupying the UI event loop without requiring every existing ledger method to become asynchronous. It does **not** make engine operations themselves nonblocking, create fault isolation between processes, or guarantee hard real-time responsiveness. Python documents that one event loop executes its tasks in one thread and provides explicit cross-thread scheduling primitives. [P1]

A single shared loop would be simpler only after every material blocking path was removed from that loop. A separate process would require IPC for the desired emitter and add process-attachment semantics the user does not need. A Textual worker is the wrong owner for the engine: Textual workers belong to their creating UI node and may be cancelled when it disappears. [P2]

### 4.3 Thread ownership rules

SQLite connections never cross this boundary. Keep `check_same_thread=True`; do not disable its protection as a shortcut. Database rows, mutable controller objects, runtime handles, and credentials also stay inside the engine. Python’s SQLite documentation explicitly describes the owner-thread restriction. [P3]

Requests cross as frozen value objects. Results cross as frozen value objects or separately allocated JSON-compatible data. Frozen dataclasses must use immutable nested fields—such as tuples of frozen rows—not mutable dictionaries masquerading as immutable state.

The bridge must use `loop.call_soon_threadsafe` or `asyncio.run_coroutine_threadsafe`, as appropriate. A normal `asyncio.Queue` must not be accessed directly from both threads. It is not a thread-safe queue. [P1, P4]

The main thread owns OS signal handlers. It translates signals into a framework-neutral stop request to the host. Signal installation must not be silently attempted and ignored inside the engine thread. [P1]

### 4.4 Headless CLI topology

Non-visual CLI commands may execute the same application operations on a main-thread asyncio loop without creating the interactive host thread. Their orchestration/lifecycle implementation is the same; their waiting and output behavior differs.

The composition root chooses the topology. The controller must not detect whether it is being called by Textual and branch its business behavior accordingly.

## 5. Responsibilities and proposed modules

```text
src/taskledger/
  notifications.py              # framework-free change/observation protocol; no-op sink
  application/
    contracts.py                # immutable requests, results, snapshots, errors
    queries.py                  # side-effect-free projection/detail/history queries
    commands.py                 # operation admission; existing service validation
    lifecycle.py                # extracted prepare/start/resume/stop execution ownership
    live.py                     # projection scheduling, cached snapshot, latest-value feed
    host.py                     # engine thread, safe bridge, operation/task lifetime
  ui/
    app.py                      # Textual root, app-level observation
    bridge.py                   # application values -> Textual messages
    screens/                    # presentation and input only
    formatting.py               # labels, truncation, units, dates
    theme.tcss                  # display styling
```

This is a responsibility map, not an instruction to manufacture an abstraction for every method. Keep related methods together. There is no generic command bus or repository class per table.

| Component | Owns | Must not own |
|---|---|---|
| Existing controller/supervisor | Readiness, routing, continuation, review, integration, budget admission | Rendering, widget events, UI refresh schedules |
| Existing Service and Journal | Domain transitions and durable controller facts | Terminal/browser behavior |
| Application commands/lifecycle | Request validation, principal resolution, operation admission, task lifetime, safe stop/recovery delegation | Duplicate verification or scheduling rules |
| Application queries | Deriving truthful console views from authoritative state | Preflight mutations, model dispatch, implicit recovery |
| Live observation | Change invalidation, projection freshness, immutable snapshots, bounded delivery | Deciding whether work proceeds |
| EngineHost | Thread/resource ownership and transport into the application | An alternative run-state machine |
| Textual | Layout, selection, focus, forms, confirmation presentation | SQLite, Git, credentials, model dispatch, ledger writes |

## 6. Public application contract

The following names are normative for this design’s responsibilities, but can be adjusted to repository naming conventions without changing semantics.

### 6.1 Core shapes

```python
@dataclass(frozen=True)
class SnapshotVersion:
    engine_epoch: str       # unique per host lifetime
    revision: int           # increasing within that epoch only

@dataclass(frozen=True)
class ChangeNotice:
    scopes: frozenset[ChangeScope]
    subject_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class ApplicationError:
    code: str
    message: str
    # In implementation: explicitly typed, safe detail values only.

@dataclass(frozen=True)
class OperationReceipt:
    operation_id: str
    disposition: str       # ACCEPTED, APPLIED, or REJECTED
    run_id: str | None
    preparation_id: str | None
    error: ApplicationError | None
```

A separate `PauseRequestReceipt` carries the target lifecycle ID and a status of `REQUESTED`, `ALREADY_REQUESTED`, or `NOT_RUNNING`; it is not an `OperationReceipt` with an undocumented disposition. A target outside the bound project is an error, not `NOT_RUNNING`.

Every submitted mutation carries a caller-generated opaque request ID. Use that same ID as `operation_id` in its receipt and registry; do not create a second correlation identity. Run, preparation, and task IDs remain separate domain identities. `operation_status(operation_id)` returns a recorded operation phase (`ACCEPTED`, `SUCCEEDED`, or `FAILED`), its receipt/result and safe error, or explicit `UNKNOWN_OPERATION` when no retained record exists. A registry miss never authorizes automatic resubmission.

These are contract sketches, not a drop-in implementation. Existing ledger IDs remain opaque strings. Identifiers in request payloads are validated against the bound project and principal, not merely accepted because the UI produced them.

`ACCEPTED` means an operation has been admitted or started. It does not mean a model turn completed, a task was accepted, or a run finished. `APPLIED` means a short operation has completed its durable effect. A run’s completion is reported through its authoritative run/project state.

### 6.2 Query surface

| Query | Required behavior |
|---|---|
| `current_snapshot()` | Return the latest immutable cached console snapshot; no database/model access on the caller thread. May be `None` during initial loading. |
| `open_feed()` | Atomically register the one display observer and seed it with the current snapshot, then asynchronously yield newer complete snapshots. |
| `refresh_snapshot()` | Schedule a pure full projection on the engine thread, without model calls, preflight, or recovery. Explicit resynchronization, not polling. |
| `operation_status(operation_id)` | Return the retained admission/outcome record, or `UNKNOWN_OPERATION`; never retry a mutation as part of this read. |
| `task_detail(task_id)` | Return current definition/revision, scope, dependencies, criteria, related requirements, assignment/submission/check status, and recorded evidence references. |
| `preparation_detail(preparation_id)` | Return the stored immutable proposal, fingerprint, branch/OID, declared configuration, and current preparation state. |
| `history_page(cursor, limit)` | Return bounded durable domain/controller event pages with separate source cursors. |
| `run_report(run_id)` | Invoke deterministic local reporting on demand, provider detail disabled by default. |

Queries must not start app-server work to make the dashboard informative. Optional provider-history enrichment remains a separately requested existing report capability, not an automatic refresh dependency. [R1, R9]

### 6.3 Operation surface

| Operation | Inputs and admission | Result and limits |
|---|---|---|
| `prepare_project` | Existing validated run configuration, repository/spec identity, explicit live consent | Admit one preparation job; return IDs; preserve immutable proposal and stop-for-approval behavior. |
| `start_prepared_project` | Preparation ID, exact approved proposal hash, explicit live consent | Revalidate every existing preparation guard; admit one run, never start from a UI-reconstructed plan. |
| `resume_run` | Existing run ID and explicit live consent | Reconcile existing identities before dispatch; do not turn uncertain outcomes into retries. |
| `request_pause` | Bound run/operation, reason | Record a thread-safe intent immediately; acknowledge `REQUESTED`, not durable `PAUSED`. Apply safe lifecycle handling at an engine safe point. |
| `answer_question` | Question ID, answer, expected open state | Delegate to existing service; answering does not implicitly resume a paused run. |
| `resolve_blocker` | Blocker ID, resolution, expected open state | Delegate to existing service and eligibility checks; no automatic “unblock everything.” |
| `extend_budget` | Run ID, existing grant kind, positive integer amount, reason | Record existing audited grant; preserve consumed usage; no automatic resume. |

Presentation-only navigation is not an application command. Selecting a task, changing tabs, filtering rows, copying an ID, or closing a detail pane has no execution effect.

### 6.4 Admission, overlap, and duplicate input

One preparation/execution lifecycle operation may be active per project. Admission must be serialized on the engine loop. Set the in-memory admission guard before the first await or externally visible action, and retain database/OS ownership checks. A second start or resume while admission/execution is active returns a stable conflict error.

Do not hold a command-processing coroutine waiting for the entire run before it can process other requests. Admission creates a strongly owned lifecycle task and returns a receipt. Queries and allowed interventions can subsequently be serviced at safe engine scheduling points.

Maintain a bounded operation registry for accepted requests and their terminal results. Suggested initial limits are 32 outstanding requests and 256 recent completed receipts; these are implementation limits, not new product capabilities. Repeating the same request ID while its registry entry exists returns that entry; repeating an ID with different parameters is rejected. Never evict an unresolved operation merely to make room.

This is not a durable exactly-once command system. After a process restart, inspect current ledger state before presenting another action. Never automatically retry a start, answer, blocker resolution, or budget grant following a lost response. Existing operation identities and domain guards remain authoritative.

Cancelling the UI’s wait for a receipt must not cancel an admitted operation. Keep the engine task/future strongly owned and shield the caller’s wait from propagating cancellation. Python’s cancellation rules make this an explicit requirement, not an automatic property of `await`. [P5]

### 6.5 Eligibility and validation

Snapshots may include `AvailableAction` values with a code, enabled/disabled status, and reason. They are presentation hints, not authorization. On invocation, reread and validate current state through existing services. A button rendered enabled can become stale before it is clicked.

Keep existing error codes where they apply, including preparation-staleness, plan validity, project identity, and budget errors. Add small console-specific codes only for new conditions such as `ENGINE_BUSY`, `ENGINE_CLOSING`, `DISPLAY_ALREADY_ATTACHED`, or `OBSERVATION_UNAVAILABLE`. Do not substitute raw tracebacks for stable application errors.

## 7. Read model and state ownership

### 7.1 Durable state versus display cache

The cache is a **projection**, not a second ledger. For committed database state `D` and known transient engine observations `O`:

```text
ConsoleSnapshot = project(D, O)
```

The projection never increments a token total because it received `TurnFinished`, never completes a task because it saw a worker stop, and never infers acceptance from an event name. It reads the existing persisted allocation, task/submission state, and verified integration facts.

The emitter says “relevant state changed.” The query layer determines what the state is. This keeps skipped notifications, duplicate notifications, and display restarts from changing the meaning of the work.

### 7.2 Snapshot fields

| Section | Required fields and semantics |
|---|---|
| Identity | Schema version, engine epoch/revision, project ID, repository root, confirmed canonical branch, selected/current execution and preparation IDs |
| Freshness | Projection time, last durable observation time, cache health, observation failure if present; distinguish “last changed” from liveness |
| Project | Persisted lifecycle/effective phase; requirement counts and verified coverage; unresolved gates |
| Preparation | `PLANNING`, `AWAITING_APPROVAL`, `APPROVED`, `SUPERSEDED`, or `FAILED`; fingerprint and immutable identity references |
| Run | Persisted state, pause reason/detail, started/finished timestamps, whether this host owns active execution, and any stop request in progress |
| Tasks | Stable ID/revision, objective, persisted task state, wave/profile, assignment ID, display activity, dependencies/blocker references, integrated status |
| Sessions/turns | Role/profile/model/effort, thread/turn identity references, stored session state, current or latest turn state, turn counts, timestamps |
| Usage | Known inclusive input, cached input subset, output, reasoning as a reported component, total admission usage, precision/completeness, unresolved allocation counts |
| Budget | Configured limits plus audited grants and consumed values; preserve existing limit semantics |
| Interventions | Open questions, blockers, required approvals, uncertainty/recovery issues; stable IDs and permitted actions |
| Recent activity | A bounded tail of sanitized durable events, with provenance and source sequence retained |
| Transient operation | Optional current blocking/async operation kind, subject, start time, configured timeout if one exists, and pause-request status |

Keep detail text and large artifacts out of the hot snapshot. Fetch complete task criteria, evidence, proposals, and full reports on demand. Initial working limits: up to 1,000 compact task rows in memory, 200 recent activity rows, and paginated older history. Larger plans must remain usable via query pagination rather than silent row omission; measure before changing these limits.

The persisted task state and a display activity label are separate fields. For example, a `SUBMITTED` task can be displayed as “in review,” but the application must not create a new authoritative task state called `REVIEWING`.

### 7.3 Pure query rules

A query may issue bounded `SELECT` statements and read recorded metadata. It must not execute preflight, register specifications, write audit events, migrate schemas, start a model, create a worktree, or reconcile uncertain operations.

Do not reuse all of `project.show`: the inspected dispatcher invokes preflight. Extract verified read-only helpers or write targeted projections, then test them with writes denied. [R4]

Projection uses one short read transaction for related database rows. It starts only when the owner connection is outside any write transaction; it contains no await. Close cursors before returning. SQLite can expose uncommitted changes to the same connection, so single-thread ownership alone is not a substitute for this guard. [P6]

Opening an uninitialized repository must not create a database merely to draw an empty screen. Opening an incompatible schema must report that an explicit initialization/upgrade operation is needed; a repaint cannot invoke `db.connect`’s migration path. Existing initialization/migration behavior is retained behind the appropriate application operation. [R7]

### 7.4 Usage and progress are deliberately conservative

Requirement verification remains the primary measure of project completion. “Every task integrated” is not equivalent to “project complete,” and “worker turn finished” is not “task done.” Display both requirements verified and tasks integrated with their actual denominators. [R1, R2]

Use the existing accounting definition: admission tokens equal inclusive input plus output. Cached input is already part of input; do not add it again. Reasoning tokens must not be added a second time when included in output. Show missing/in-flight accounting as unknown or incomplete, not zero. [R1, R6, R9]

The first UI does not promise a token-by-token live counter. Terminal allocations become visible when the controller records them. Showing current session/turn activity is distinct from asserting that its final usage is already known.

Do not recalculate economic estimates in widgets. A future valuation view must use the existing report’s explicit provenance and immutable valuation input. No market-pricing requests are part of this feature.

## 8. The live observation protocol

### 8.1 Two different event concepts

**Durable execution/domain events** are existing `controller_events` and `audit_events`. They provide history and evidence. **Ephemeral observation notifications** wake the console projection/display. They are not a work queue, a replay log, or an authorization path.

This distinction allows a lightweight emitter without losing durable history. There is no promise that every intermediate snapshot is rendered. There is a requirement that the display converges to the latest available snapshot and that history remains queryable separately.

### 8.2 Change publisher

Inject a small framework-free `ChangeSink` into the relevant service/journal/application objects. Its default implementation is a no-op. Its console implementation records dirty scope flags and schedules a projection on the engine loop.

Use domain-oriented scope names: `PROJECT`, `PREPARATION`, `RUN`, `TASKS`, `SESSIONS`, `REVIEWS`, `USAGE`, `INTERVENTIONS`, and `HISTORY`. The initial full projector accumulates only this bounded set of dirty scopes; it does not keep an unbounded queue of notices or subject IDs. Do not use widget names such as `TASK_TABLE` or `HEADER`.

Define `ChangeScope`, `ChangeNotice`, `ChangeSink`, and the no-op implementation in the lower-level `notifications.py` module. That module must not import `application` or `ui`; application code imports the protocol, not the reverse. The publisher must not run arbitrary UI listeners inline. A concrete bounded mailbox and a fixed scheduling callback are sufficient. Third-party pub/sub libraries, wildcard event routing, subscriber priorities, middleware, and multiple-reader fan-out are unnecessary.

### 8.3 Publication ordering

For a durable transition:

```text
validate -> mutate in existing transaction -> COMMIT succeeds
         -> mark observation scopes dirty
         -> build committed snapshot -> publish snapshot -> repaint
```

Never publish an authoritative state change before commit. A rollback produces no committed transition notification. If an operation has multiple transactions, a later failure does not invalidate earlier commits: request a fresh projection of the actual remaining state and surface the operation error separately.

Keep notification exceptions outside the business operation’s success/failure boundary. Once a transaction commits, a broken display cannot turn that operation into a reported rollback or trigger an automatic retry. Catch observation exceptions narrowly, record an observation health error, retain the last good snapshot, and preserve the mechanical outcome.

Do not install a SQLite update hook, trace hook, `PRAGMA data_version` loop, file watcher, or WAL tailer. Instrument the owning service/journal/application methods explicitly. Where a transaction contains an early return, restructure the method so the post-commit notification is unambiguous.

### 8.4 Required hook coverage

| Origin | Changes requiring observation invalidation |
|---|---|
| Lifecycle | Preparation created/finished/failed; plan materialized; execution run admitted/resumed/finished/paused/failed |
| Journal | Session creation/closure; turn dispatch/acknowledgement/completion/failure/uncertainty; usage reconciliation; target changes; budget grants; final-review state |
| Worker tool service calls | Questions, blockers, checkpoints, submissions, check receipts, evidence registration, follow-ups |
| Verification/integration service calls | Submission/checkpoint outcome, corrections, task integration/failure/uncertainty, requirement verification and project completion |
| User interventions | Answers, blocker resolutions, grants, explicit recovery operations outside the initial UI surface |
| Runtime observation | Safe, minimal current-operation metadata; never convert an uncommitted provider message into an accepted ledger outcome |

Instrument actual committed mutations, not only outer commands. Otherwise a question raised through the worker broker would remain invisible until some unrelated user action happened.

A command’s exception handler also requests a pure refresh after partial progress, outside any active transaction. This is an invalidation hint, not a claim that a failed command succeeded.

For timeline entries missing from the current journal, add compact records using the existing `controller_events` table at the associated state transition. In particular, turn acknowledgement, terminal turn outcome, and session closure should be distinguishable. A replay that makes no state change must not append the same transition again. No new raw provider-history mirror is authorized.

### 8.5 Projection scheduling

Coalesce dirty notices arriving in one engine-loop iteration. A projection runs from committed state, replaces the full compact snapshot, increments its revision, and places it in the latest-value mailbox.

A full compact projection is the initial implementation. Dirty scopes are hints for possible later optimization, not permission to assemble mutually inconsistent old/new sections. If incremental projections are later introduced, their cross-section consistency must be demonstrated.

There is no periodic database task. A one-shot scheduled refresh after a change is not polling. A display-only timer may update elapsed-time labels without querying SQLite.

Before a known long synchronous operation, flush already-dirty committed state and publish minimal “operation in progress” metadata outside a transaction. This prevents a queued projection from being held behind a long `communicate()` call. The operation observation comes from mechanics; the UI does not guess it from a spinner.

If projection fails, record the observation error, retain the last valid snapshot, and retry on the next change or explicit refresh. Do not run a hot retry loop. A metadata notice about observation failure must be publishable without rerunning the failed query.

### 8.6 Snapshot version and atomic attachment

Each host lifetime receives a unique `engine_epoch`. Revisions increase within that epoch. They are not database audit sequences and are not persisted as a new history table.

`open_feed()` must register the observer and seed its initial snapshot as one atomic operation relative to publication. It must not implement “query snapshot, then subscribe.” A transition occurring between those two calls could otherwise be missed permanently.

With the shared mailbox lock:

```text
attach:
    reject a second live observer
    install observer
    seed its pending value with latest snapshot
    schedule at most one wake-up
```

A publication either happens before this critical section and is included in the seed, or after it and replaces the observer’s pending value. No cursor replay is necessary for the snapshot feed.

The consumer accepts only newer revisions in the attached epoch. An old task-detail response uses its own selection/request generation check and must not overwrite a newly selected task. On a new host epoch, bootstrap again; never compare revision numbers across epochs.

### 8.7 Bounded delivery and backpressure

Use a single pending full-snapshot slot. A newer snapshot replaces an unconsumed older one. Maintain at most one queued cross-thread wake-up at a time, so a stalled UI cannot accumulate thousands of callback objects in the main event loop.

The bridge never awaits rendering and never blocks on a full UI queue. Its only synchronization is a short lock protecting references and wake-up state; no query, user callback, I/O, or formatting runs under that lock.

Coalescing is safe because updates are full replacement snapshots. It would not be safe to silently drop required incremental changes such as “add 20 tokens.” Such deltas are not the display contract.

If the UI loop closes while a wake is being scheduled, detach the reader and record delivery failure. Keep the engine’s latest snapshot and durable work intact. Lifecycle policy for a disappearing primary UI is handled separately, not by a queue exception.

### 8.8 History is not the snapshot feed

Use distinct cursors for domain and controller history:

```text
HistoryCursor(audit_sequence, controller_sequence)
```

Both tables allocate sequences independently. Their integer values must never be treated as one global counter. Display a deterministic merged ordering using timestamp, source name, and source sequence, but label that as display ordering—not a proven cross-stream causal order.

Pagination advances only the relevant stream’s cursor past returned records. Preserve each source ID/sequence for deduplication. A dropped intermediate snapshot cannot delete historical events because history is read from durable tables on demand.

Transient busy observations do not masquerade as durable evidence. When unavailable after restart, omit them rather than inventing their past state.

## 9. Lifecycle, pause, and failure semantics

### 9.1 Ownership

The headless lifecycle owner retains the run task, runtime client, service/journal resources, and project lease. Textual receives a client handle only. No widget or screen owns these resources.

Refactor current CLI preparation/start/resume functions into reusable operations. Keep CLI printing and JSON serialization in `cli.py`. Keep existing runtime close/interruption/reconciliation behavior in the extracted lifecycle implementation rather than reproducing it in UI code. [R4]

There must be exactly one owner of the project execution lock per lifecycle. Admission must not create a second run before it establishes ownership. Factor the existing inner `ProjectController.run` locking into a single reusable execution wrapper, or pass a private already-acquired lease into the execution body. Do not acquire the same lock twice through two independent file handles. Retain locking for standalone CLI/controller entry points. [R6, R8]

A UI process cannot attach to a controller already running in a different process. Report that limitation without killing, taking over, or resuming it. The no-IPC design intentionally trades away detached attachment. Out-of-process manual database writes are not an automatically observed feed; using another mutating client concurrently is unsupported. Explicit refresh can reread state, but does not make concurrent control supported.

### 9.2 Safe pause is a request, not an instantaneous state

`request_pause` sets a thread-safe latch immediately and wakes the engine loop when possible. The UI may show “Pause requested.” It cannot show durable `PAUSED` until the lifecycle records the final pause state.

Every new model-session/turn admission checks the latch immediately before durable dispatch intent/provider dispatch. Also check after awaited admission work. If pause races with an already admitted provider request, treat that request as in flight and reconcile it; do not assert that no request could have escaped merely because the button was clicked first on screen.

For async model waits, invoke the existing bounded interruption/inspection flow. Preserve completed results and usage that can be proven; preserve uncertain outcomes when they cannot. For a synchronous service/Git/check operation, do not inject a thread exception or close its connection. Wait for that operation to return or reach its existing timeout/recovery path, then pause before the next admission.

**Known limitation:** a responsive UI does not imply an immediate engine stop. The present blocking check can last until its configured timeout, and some other synchronous operations may lack a finite timeout. The UI must show the known current operation and any known timeout, and say that pause is waiting for a safe point. This specification does not falsely guarantee bounded shutdown for an unbounded existing call. [R5]

Moving long process execution to a cancellable async subprocess adapter is a separate mechanical improvement, not hidden inside the Textual work. It becomes necessary before offering an “interrupt current check immediately” guarantee. It is not necessary merely to keep the display responsive.

### 9.3 Completion and pause races

If work genuinely completes before the stop request is applied, report the completed result. Do not overwrite a completed run as paused to satisfy a button’s expected outcome.

A display request must not set `controller_runs.state` directly. Only the lifecycle after reconciliation records the final run state. A provider turn ending during interruption is still a provider result—not automatic submission acceptance or task integration.

Preparation cancellation preserves its distinct existing lifecycle: an interrupted/failed preparation is not advertised as a resumable execution run unless the underlying application supports that exact operation. The UI must use action availability from mechanics, not a generic “Resume anything that stopped” button.

### 9.4 Closing views versus ending the application

Switching tabs, closing a modal, cancelling a detail read, and unsubscribing a display feed do not stop execution. This follows from separating observation lifetime from work lifetime.

An intentional application exit during execution offers **Pause and exit** or **Keep running here**. After confirmation, the composition root asks the lifecycle to pause, waits for resource cleanup, then closes Textual. There is no “detach and continue after process exit” mode.

If the primary Textual app fails unexpectedly, the outer composition root requests safe pause and performs cleanup. This is an explicit host policy for loss of the controlling interface; it is not exception propagation from a widget into the controller. Restore the terminal and surface the failure through a plain fallback message where possible. Do not silently leave an invisible, spend-bearing run behind.

For ordinary CLI runs, having no display subscriber is normal and must not pause anything. Similarly, a future browser disconnect is not automatically a process shutdown—the web composition root will own that policy.

### 9.5 Signals and hard failure

Route `SIGINT` and `SIGTERM` on the main thread to the same headless stop policy used by the UI. Handle `SIGHUP` where supported as an exit request, without claiming it is available identically on every platform. Do not race multiple shutdown implementations.

A second signal does not grant permission to invent terminal provider results. If the OS kills the process, the durable ledger and existing reconciliation rules are the recovery path. `SIGKILL`, interpreter crashes, power loss, and terminal disappearance cannot be made graceful by an event emitter.

The runtime uses asyncio subprocesses as well as existing synchronous Git/check calls. Exercise actual app-server process creation, pipe I/O, and closure from the engine thread on each supported OS/Python version; moving an existing runtime to another loop/thread is not proven merely by passing a synthetic SQLite test. Keep the engine thread non-daemon. Do not rely on interpreter teardown to close SQLite or provider resources. Never call `thread.join()` synchronously inside a Textual message handler. Normal shutdown awaits host closure asynchronously, after asking the lifecycle to stop.

## 10. Failure matrix

| Failure | Mechanical outcome | Display outcome |
|---|---|---|
| Transaction rolls back | Existing operation error; no committed transition | Keep previous committed values; show operation error |
| Commit succeeds, notification fails | Commit remains successful; no automatic retry | Observation degraded; refresh from current state |
| Intermediate snapshot skipped | No mechanical effect | Newest snapshot replaces old; history remains queryable |
| Projection query fails | Existing execution continues unless its own rules stop it | Keep last good values with an explicit stale/error banner |
| Display closes a screen | No mechanical effect | Cancel that screen’s reads only |
| Display receipt wait cancelled | Admitted operation remains owned | Reattach/read operation status; no silent mutation retry |
| Model turn completes normally | Existing controller handles continuation/review | Turn status updates; task completion is not inferred |
| Pause during blocking check | Wait for safe boundary; no new admission afterwards | “Pause requested; waiting for check,” not “paused” |
| Run completes while pause is requested | Preserve actual completed result | Show completed |
| Primary Textual app crashes | Outer host requests safe pause | Terminal fallback; report failure and final/uncertain state |
| Process crashes | Durable state preserved as far as committed | Next startup shows recorded state with ownership/recovery distinction |
| Old `RUNNING` row on startup | No automatic dispatch | “Recorded running; not owned by this process / recovery required” |
| Another controller owns the project | Reject new control admission | Explain no cross-process attachment; do not take over |
| Input changes after proposal review | Existing stale-preparation failure | Keep approval disabled until a valid exact proposal is reviewed |

## 11. Security and data minimization

Keep orchestrator/worker credentials in the existing application authority boundary. Never serialize them into snapshots, error details, Textual messages, test screenshots, or future browser responses. Binding a client to a project does not authorize arbitrary IDs from another project.

Preserve worker-scoped dynamic tools, read-only reviewer policy, exact required checks, live opt-in, and prohibition of nested model delegation. Adding UI controls is not permission to bypass any of those rules. [R1]

The display receives sanitized known metadata, not raw controller configuration dictionaries or provider payloads. Error payloads use stable codes and allowlisted details. Do not mirror raw model conversations or reasoning into another store.

Terminal text from specifications, model summaries, questions, filenames, and subprocess outputs is untrusted display content. The Textual layer must render it literally and neutralize control sequences/markup rather than interpreting arbitrary escape sequences or links. Mechanics should avoid sending full process output unless a specific artifact query requests it.

The future browser layer will need localhost binding, origin/host validation, a local session capability and CSRF protection for mutations. Those are future transport requirements, not a reason to expose HTTP today.

## 12. Why this design, rather than the alternatives?

| Alternative | Benefit | Reason not selected now |
|---|---|---|
| SQLite/event-table polling | Simple cross-process observation; durable cursor replay | The user explicitly wants a single in-process visual interface; polling is unnecessary for that topology. |
| Generic synchronous emitter callbacks | Very little initial code | A slow/throwing callback enters the execution path; event delivery has no initial-state protocol or backpressure contract. |
| Event-only in-memory state reducer | Minimal reads after startup | Duplicates task/accounting semantics and needs complete, ordered, lossless events; the existing journal is not such an API. |
| Controller inside a Textual worker | Convenient UI integration | Widget/app cancellation can own execution lifetime; mechanical code gains framework dependencies. |
| One shared asyncio loop | No cross-thread bridge | Existing blocking service work can freeze the display; requires a broader nonblocking rewrite first. |
| Separate controller process | Stronger lifecycle/crash isolation | Requires IPC to satisfy the requested push feed and introduces attachment/lifecycle questions outside the stated need. |
| HTTP API first | Ready for browser requests | Premature transport/security surface; the reusable application contract can be built without it. |
| Durable outbox for UI notifications | Recoverable notification delivery | The display needs current state and separately retained history, not exactly-once delivery of each repaint. |

The selected design pays for a small, tested thread bridge and explicit emission hooks. In return, it preserves core ownership, avoids display polling, tolerates a slow observer, and leaves a direct web-adapter extension path.

## 13. Conditional correctness arguments

### 13.1 No uncommitted state is advertised

Assume each durable notification follows a successful commit; projection refuses an open write transaction; and related reads occur in one short read transaction. Then a published authoritative snapshot is derived from committed state. A rollback cannot create a published committed transition because its post-commit step is never reached. A later failed transaction cannot erase an earlier committed fact from the projection.

Demonstrated for the synthetic ledger by P01–P06. Required against Task Ledger: fault injection at every newly instrumented transaction and partial-operation boundary.

### 13.2 No bootstrap lost-update window

Attachment and publication are serialized by the same mailbox lock. For any publication concurrent with attachment, there are two orders. Publication first means the seed contains the new snapshot. Attachment first means publication replaces the subscriber’s pending snapshot. In either case, after the publisher becomes quiescent and the consumer runs, the consumer can obtain the latest value.

P12 exhibits the broken snapshot-then-subscribe sequence; P13 tests both abstract atomic orderings; P15/P17 exercise seeding and cross-thread wake-up. This argument assumes the consumer is eventually scheduled and the feed remains open.

### 13.3 Coalescing cannot double-count or lose current state

Each value is a complete replacement snapshot with an increasing revision. Dropping an intermediate value cannot remove part of the next value. Therefore the display converges without replaying incremental token/task operations. Durable history is not carried only by this channel and is unaffected.

P08–P11 and P16/P22 demonstrate these properties in the prototype. This argument would not apply to a delta stream, which is why deltas are not the initial contract.

### 13.4 Backpressure is bounded

The mailbox stores one pending snapshot and queues at most one wake-up while the consumer loop is stalled. If N changes arrive, storage for pending deliveries remains O(1) in N, plus the size of the latest snapshot. Snapshot size still grows with the bounded project view; this is not a claim of constant memory for arbitrarily large projects.

P16 publishes 10,000 updates without yielding the consumer loop and observes one pending value and one scheduled wake. It is a functional bound check, not a production performance benchmark.

### 13.5 Observation does not authorize execution

All execution transitions occur in existing service/controller operations. No display callback is invoked on their stack. A receipt-wait cancellation cannot cancel an engine-owned job. Removing a display therefore removes observation, not the authority to run. A primary-interface failure can still cause a deliberate safe-pause request through the outer lifecycle policy.

P03/P14/P18/P19/P21/P24 demonstrate pieces of this separation. Required against Task Ledger: identical fake-runtime dispatch and ledger outcomes with a normal, absent, slow, and failing observer, excluding the deliberately requested shutdown case.

### 13.6 Display responsiveness is separated from engine responsiveness

With distinct event-loop threads, blocking the engine thread does not occupy the UI thread’s event loop. P23 demonstrates that a consumer-side loop continues scheduling while the engine thread is intentionally blocked. This does not prove Textual frame rate, prevent GIL contention, or guarantee immediate pause processing. Those remain explicit acceptance/limitation items.

## 14. Verification plan and release gates

### 14.1 Evidence delivered with this specification

`validation/test_contracts.py` ran **26 tests, with zero failures and zero errors** in the supplied environment. The exact Python/platform/SQLite versions and per-test results are in `validation/results.json`; console output is retained in `validation/test-output.txt`.

The experiments use no network calls, no model calls, and no user project modifications. They test a synthetic schema, mailbox, and loop owner. They do not establish that all real Task Ledger mutation sites have been instrumented correctly.

Textual was not installed in the execution environment, and a local repository checkout could not be obtained through the environment’s network. Repository source was read through the GitHub connector. Consequently, no Task Ledger suite, Textual Pilot test, macOS run, or live Codex test is represented as passing here.

### 14.2 Mandatory implementation acceptance cases

| ID | Test and pass criterion |
|---|---|
| M01 | Import and run the headless CLI/application with Textual absent. No conditional import leak. |
| M02 | Existing CLI inputs, outputs, error codes, and live-consent behavior remain compatible. |
| M03 | Existing fake-runtime controller/ledger tests pass; no change in model dispatch decisions attributable to observation. |
| M04 | Run the same fake workload with no observer, a fast observer, a slow observer, and observer failure; compare semantic outcomes and dispatch sequence. |
| M05 | Exercise each hook-coverage row, including worker-broker-originated submissions/questions and direct usage reconciliation. |
| M06 | Inject rollback and post-commit notification failure. No ghost state, false rollback, duplicate retry, or changed ledger outcome. |
| M07 | Query snapshot/detail/history with database writes denied and model/Git mutation calls set to fail. All queries remain pure. |
| M08 | Race startup/attachment/publication repeatedly; after quiescence the consumer obtains the newest snapshot. |
| M09 | Flood changes while the UI is stopped; mailbox and queued wake-up counts remain bounded; durable history remains complete. |
| M10 | Cancel the UI’s read/receipt wait; active execution and accepted mutations are not inadvertently cancelled. |
| M11 | Ensure SQLite/service/runtime objects are created and used on the engine thread; retain owner-thread checking. |
| M12 | Test double start/resume, mismatched request IDs, project mismatch, stale proposal hash, and process-lock contention before dispatch. |
| M13 | Start from each partial-preparation/start failure boundary. Show committed facts and do not automatically replay admission. |
| M14 | Pause during an active model turn, immediately before dispatch, during a synchronous check, during integration, and concurrently with completion. |
| M15 | Prove uncertain turn outcomes remain uncertain; no duplicate model dispatch on recovery. |
| M16 | Unmount a screen, close a modal, and cancel a detail request. No effect on run lifetime. |
| M17 | Terminate the primary app through its normal exit and through an injected display exception. Apply explicit safe-stop policy and clean up resources. |
| M18 | Test SIGINT/SIGTERM and supported SIGHUP on macOS and Linux. No daemon-thread teardown or nested event loop. |
| M19 | Load old recorded `RUNNING` state without live ownership. No automatic model call and no false live indicator. |
| M20 | Verify cached-input/reasoning treatment, missing usage, active allocations, grants, preparation-vs-execution scope, and requirement/task completion distinctions. |
| M21 | Verify JSON serialization and forbidden-field absence for every public snapshot/result/error type. |
| M22 | Test history pagination with overlapping domain/controller sequence numbers and equal timestamps; preserve source identity and cursor advancement. |
| M23 | On-demand report does not become a refresh loop or model call. Provider-detail failure does not break the console. |
| M24 | Exercise the full public contract with a fake non-Textual consumer, including operation-status lookup after a cancelled receipt wait. Reuse the mechanical assertions in Part 2 integration tests. |

### 14.3 Performance acceptance—not measured claims

Use a reproducible synthetic fixture with 1,000 task summaries, 20 current sessions, and 200 recent events. Record hardware, Python version, Textual version, and fixture characteristics.

Target normal commit-to-visible-state latency below 250 ms at the 95th percentile when the engine is not inside a blocking operation. Target individual compact projections below 50 ms at the 95th percentile. These are proposed engineering targets; do not label them measured until the actual implementation is tested.

Demonstrate keyboard/navigation responsiveness during an intentionally blocked engine operation. Demonstrate zero repeating SQLite queries when the system is idle. Count application query executions—not just screen repaints. Measure observation overhead relative to the same fake workload without observation; investigate material regressions rather than setting an arbitrary model-token cost claim. Outcome-equivalence fixtures must allow adequate wall-time margin: extra observation work can change elapsed-time admission near a deadline, so separation does not promise identical timing or identical outcomes for every time-sensitive schedule.

## 15. Implementation sequence: Part 1 only

**First, extract and test the headless lifecycle.** Move loop ownership, admission, stop handling, and resource closure out of CLI-specific code. Preserve the old CLI behavior through wrappers. Keep the existing scheduler/Service rules rather than rewriting them.

**Second, establish pure query contracts.** Add compact snapshots and bounded detail/history queries, with owner-thread and no-write tests. Preserve exact persisted states and accounting provenance.

**Third, instrument committed change boundaries.** Add the no-op sink, explicit notification hooks, and compact missing history transitions. Add fault-injection tests before connecting a display.

**Fourth, add the host and feed.** Implement one engine thread, atomic attachment, latest-value delivery, operation receipts, and lifecycle-safe cancellation. Run the no-observer/slow-observer equivalence tests.

**Part 1 is complete only when it is independently executable and tested without Textual.** A fake consumer must be able to inspect, approve/start, pause/resume, answer a question, and observe committed state through the public interface, within existing product rules. Part 2 may begin earlier for static presentation fixtures, but must not compensate for missing mechanics with widget code.

## 16. Future web extension

A future local ASGI adapter can own an `EngineHost` for the server lifetime, serve current snapshots/detail queries as HTML or JSON, forward mutations to the same operation handlers, and turn `open_feed()` into an SSE stream. SSE provides a standard server-to-browser event channel; mutations can stay ordinary request/response operations. [P7]

The snapshot stream is not durable replay. On browser reconnection, start with the current snapshot. An old `epoch:revision` event ID is a freshness hint, not authority to replay missing task transitions. The durable activity/history query remains separate.

A browser disconnect only releases observation. It must not cancel admitted work or close the host. The future web composition root owns shutdown. Authentication, origin protection, safe serialization, HTML escaping, and local binding belong to that adapter. No UI classes, controller scheduling logic, or database schema need to change solely to add the transport.

## 17. Primary sources

Repository references below are pinned to the inspected commit. They support descriptions of existing behavior, not claims that the proposed additions are already implemented.

- **R1:** [Current controller implementation guide](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/docs/CONTROLLER_IMPLEMENTATION.md).
- **R2:** [Current command registry](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/COMMAND_REGISTRY.md); original attached `Machine Readable Task List.txt` for historical functional framing only.
- **R3:** [Package metadata](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/pyproject.toml).
- **R4:** [CLI lifecycle and dispatcher](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/cli.py), especially `run_project_controller`, `prepare_project_command`, `start_prepared_project`, and `dispatch`.
- **R5:** [Ledger service](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/service.py), especially `audit`, `_execute_check`, `worker_check`, and `reviewer_check`.
- **R6:** [Controller journal](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/controller/journal.py).
- **R7:** [Database setup, schema, and transactions](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/db.py).
- **R8:** [Project controller](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/controller/project.py).
- **R9:** [Deterministic reporting](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/controller/reporting.py).
- **R10:** [Ledger adapter](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/controller/taskledger_adapter.py) and [existing ports](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/controller/ports.py).
- **P1:** [Python 3.11: developing with asyncio](https://docs.python.org/3.11/library/asyncio-dev.html), concurrency, thread-safe scheduling, signals, and blocking work.
- **P2:** [Textual: workers](https://textual.textualize.io/guide/workers/), worker lifetime and cancellation.
- **P3:** [Python 3.11: sqlite3](https://docs.python.org/3.11/library/sqlite3.html), connection ownership and transaction behavior.
- **P4:** [Python 3.11: asyncio queues](https://docs.python.org/3.11/library/asyncio-queue.html), thread-safety and capacity semantics.
- **P5:** [Python 3.11: coroutines and tasks](https://docs.python.org/3.11/library/asyncio-task.html), task ownership, cancellation, and shielding.
- **P6:** [SQLite: isolation](https://www.sqlite.org/isolation.html), transaction/read visibility.
- **P7:** [WHATWG HTML: server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html).

Primary documentation was consulted during this review. It can evolve independently of the pinned repository. Pin and test an appropriate Textual release during implementation rather than copying an unverified “latest version” into this specification.
