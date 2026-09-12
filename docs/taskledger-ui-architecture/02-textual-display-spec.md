# Task Ledger: Textual display adapter

**Specification:** TL-CONSOLE-1, Part 2 of 2  
**Status:** proposed implementation specification; framework-documented, not yet implemented or tested in Textual  
**Source baseline:** `christopher-caldwell/taskledger`, commit `c1453448436ec8d46d502eb9605876cca026e3fe`  
**Prepared:** September 12, 2026 UTC / September 11, 2026 America/Denver  
**Dependency:** [01-headless-mechanics-spec.md](01-headless-mechanics-spec.md)

## 1. Purpose and boundary

Implement a local, live, keyboard- and mouse-operable Textual console over the headless interface in Part 1. Its purpose is to make existing execution understandable and make existing human interventions convenient. It is not another scheduler, model agent, task-management product, or ledger authority.

Part 1 specifies what an operation means, what current state means, and how changes reach a consumer. Part 2 specifies how those values are displayed and how a person supplies an operation request. The same application must be usable without this adapter, and the same adapter must be testable against a fake application interface.

**Acceptance rule:** no missing mechanical behavior may be implemented inside a widget to make the UI appear finished. A missing headless query, safe-stop path, validation rule, or receipt must be implemented and tested in Part 1 first.

Textual supplies event handling, workers, widgets, and a headless testing interface. Those facilities are suitable for presentation, but their lifetime is not the lifetime of a Task Ledger run. Textual’s documentation explains that workers belong to UI nodes and can be cancelled when those nodes disappear. Therefore a Textual worker must never own the actual controller operation. [T1, T2, T3]

## 2. Deliverable and installation

Add an optional `tui` dependency extra and a documented `taskledger ui` command. These are **proposed additions**, not commands claimed to exist at the inspected baseline.

```text
pip install 'taskledger[tui]'
taskledger ui
```

`taskledger ui` resolves the current repository; `taskledger ui --repo /absolute/path` explicitly selects one. Other flags are added only where they reuse established project selection semantics. Do not accept worker credentials as console authority. Credentials are resolved by the headless application and never passed to widgets.

Launching the console does not imply consent to model work. Opening the screen must not prepare a project, start a run, resume a recorded run, execute a check, or perform recovery. Preparation and execution require explicit user actions and the same live-execution consent required by the current CLI. [R1]

The non-visual installation must continue to import and run without Textual. Import the adapter lazily after selecting the UI command. If the optional dependency is absent, return a clear installation instruction; do not install packages or modify the environment automatically.

Select and pin a compatible Textual release during implementation, record the tested version, and test against TL’s Python support policy. This specification does not invent a currently verified dependency version. The design experiments ran on Python 3.13.5, not on all supported versions, and Textual was not installed in that environment.

There is no browser-serving flag, `textual-serve` dependency, HTTP server, or web implementation in this part. A browser-hosted terminal is also not the same deliverable as the eventual native lightweight web presentation.

## 3. Composition and resource ownership

### 3.1 Process composition

The interactive composition root creates the engine host from Part 1 and the Textual application. Textual runs on the main thread. The engine host owns its separate thread and application resources. The composition root owns final shutdown.

```text
interactive composition root
    |
    +-- EngineHost ----------------------- headless mechanics
    |       |
    |       +-- application + controller + ledger + runtime
    |
    +-- TaskledgerApp -------------------- presentation only
            |
            +-- one snapshot-feed consumer
            +-- screens, forms, dialogs, local formatting
```

Use Textual’s supported asynchronous application entry when composing with an outer lifecycle, rather than calling a nested `asyncio.run` from an event handler. The exact root can be adapted to the tested framework version, but mechanical shutdown must remain outside screen teardown. [T4]

The root translates OS signals into the headless stop request and applies Part 1’s explicit shutdown policy. It must not maintain a second pause algorithm. The engine thread must not install signal handlers itself.

### 3.2 Allowed imports and actions

The `ui` package may depend on Textual, ordinary display utilities, and the application’s public contract/client handle. The composition module may construct the host. Screens and widgets may not import `sqlite3`, `Service`, `Journal`, `ProjectController`, `WorkerBroker`, Git helpers, or the provider runtime.

No widget may execute a subprocess, call the CLI to perform an action, open the ledger database, change a task state, choose the next assignment, interpret a review verdict, or increment an authoritative token total.

Table sorting, filters, selection, expanded panes, elapsed-time formatting, and dialog state are presentation work. Their state stays local and is not persisted as new domain facts.

### 3.3 App-level observation, not per-screen subscriptions

Exactly one app-level consumer attaches to `open_feed()`. Individual screens receive the latest immutable snapshot through the application’s display state. Switching tabs must not subscribe a second observer or restart the controller.

The application subscribes before enabling controls. The feed’s atomic initial snapshot establishes a baseline; the UI must not separately load a snapshot and then subscribe. That would reintroduce the bootstrap race Part 1 removes.

Only presentation work uses Textual workers. An observation/detail worker may be cancelled when its screen is removed. Use nonfatal error handling for observation and detail-query failures; a failed background read should produce a visible stale/error state, not accidentally exit the app through a worker default. Textual documents worker error handling and node-bound cancellation. [T1]

## 4. Update path and responsiveness

### 4.1 From commit to display

```text
mechanical mutation commits
    -> headless change notice
    -> coherent immutable snapshot
    -> bounded cross-thread mailbox
    -> app-level consumer
    -> display state replacement
    -> update affected widgets
```

The UI does not poll SQLite, poll CLI commands, or parse controller stdout. The in-process feed supplies current state. A display-only timer may recalculate the text of an elapsed clock without calling the engine.

The bridge in Part 1 bounds cross-thread wake-ups. Do not undo that protection by creating an unbounded Textual message or task per update after receiving a snapshot. Maintain a latest pending display snapshot and at most one pending display-update message. A newer value may replace an unrendered older value.

A worker already running in Textual’s loop can apply display state there. A bridge crossing into the UI thread may post a Textual message from adapter code; `post_message` is documented as thread-safe. Mechanical modules must not import Textual to perform that translation. Never synchronously wait for a widget update from the producer thread. [T1, T2]

### 4.2 Revision and selection rules

Discard an older snapshot from the current engine epoch. A new epoch requires a new baseline, not comparison with old revision numbers. Treat a stale or disconnected display as stale; do not extrapolate domain changes while disconnected.

A detail response carries the requested entity ID and the display request generation. For example, after selecting task B, a slower response for task A must not replace B’s pane. This is a display race even with one client and must be tested explicitly.

Preserve selection by stable IDs, not table row positions. If a selected row is filtered out or removed, retain an explicit empty/unavailable detail state rather than showing another task under the old heading.

### 4.3 What responsiveness does and does not mean

Keyboard navigation, scrolling, input, and elapsed-time labels should remain responsive while the engine is inside a blocking check. Actual snapshots and most command receipts may be delayed until the engine reaches another scheduling point.

A pause request has a special thread-safe intent path. Show “Pause requested” promptly after that request is accepted. Do not show “Paused” until the lifecycle confirms its durable outcome. When available, show the current operation and its configured timeout. Never invent a remaining time, cancellation deadline, or claim that a process has already stopped.

An unchanged snapshot is not evidence that the run is dead. “Last state change” and “host owns active execution” are separate facts. A spinner or locally advancing clock is not a heartbeat and must not be presented as proof of provider health.

## 5. Information architecture

Use six main sections: **Overview, Tasks, Agents, Activity, Usage, and Interventions**. Preparation review is a detail flow accessible from Overview and Interventions. The full deterministic run report is an on-demand detail view, not a seventh continuously refreshing dashboard.

The top-level context identifies the repository, confirmed canonical branch, current preparation/run, and recorded lifecycle state. Distinguish “running in this console” from a historical or externally owned recorded `RUNNING` row.

Do not make a task count the main project-completion bar. Requirements verified and tasks integrated are both useful, but they answer different questions. Preserve TL’s existing completion semantics. [R1, R2]

### 5.1 Overview

Display verified requirement coverage, integrated task count, current execution phase, active worker/reviewer count, known token subtotal and accounting quality, and a compact list of required human actions.

Show a neutral initial state for an uninitialized repository. Explain what must be explicitly initialized or prepared; do not create state to remove the empty screen. An incompatible schema must be reported as requiring the explicit upgrade path defined in Part 1.

If a preparation is awaiting approval, make “Review preparation” the main action. If execution is paused, show the recorded reason and the specific available actions. If all tasks are integrated but final requirement review remains, show that phase explicitly rather than “Done.”

A terminal run does not force the application to close. Keep final status and reports visible until the user exits.

### 5.2 Tasks and details

The compact table shows objective, authoritative task state, activity, wave, selected worker profile, and blockers or dependency status. Keep state and activity distinct: “SUBMITTED / In review” is a legitimate combination; a worker turn ending does not change the task to completed.

Opening a task fetches its complete current revision, implementation scope, associated requirements, dependencies, acceptance criteria, assignment details, submitted commit, recorded check results, review outcome, and evidence references through `task_detail`.

Do not preload every artifact or full proposal into the table. Pagination for plans larger than the headless compact limit must clearly state that more rows exist. Filtering a loaded page must not be represented as a search of the entire project. Add a bounded query with explicit filter semantics if project-wide search is required.

A task pane is read-only in v1. No task editing, drag-and-drop scheduling, manual completion, or “accept submission” button is added.

### 5.3 Agents and turns

Show role, profile, resolved model/effort, related task or review subject, session state, turn state/count, elapsed time, and known recorded usage. Present initial planner, worker, submission reviewer, and final reviewer as distinct roles where the application supplies them.

A retained session is not necessarily executing a turn. An idle thread must not appear as an active worker consuming tokens. A finished model turn is not a successful submission. A failed or uncertain turn remains failed or uncertain even if output exists.

Show IDs in copyable detail fields, not as unreadably wide mandatory table columns. There is no UI for directly resuming arbitrary provider threads or changing their model during a run.

### 5.4 Activity

The activity view displays the headless layer’s sanitized durable event records. Each row retains source identity, source sequence, timestamp, related entity, and a concise factual description.

The live tail is bounded. Older records load through `history_page` on explicit demand. Do not append the same event again merely because a new complete snapshot contains it; deduplicate by source plus source sequence.

Keep the user’s scroll position while reading older entries. Automatically follow new activity only while the user is at the end or explicitly enables follow mode. Display a count of newer available entries rather than repeatedly moving the viewport.

Domain and controller events have independent sequence numbers. The merged display order is not a single proven causal log. No UI-generated number may conceal that distinction.

The first implementation does not stream model prose, provider reasoning, raw command output, or a complete conversation transcript. Existing persisted evidence may be opened by an explicitly supported safe detail query; raw terminal escape sequences must never be executed.

### 5.5 Usage

Show the known recorded token subtotal, inclusive input, cached-input subset, output, and the accounting-quality indication. Clearly identify active or missing allocations. “Unknown” is not zero, and a finalized subtotal is not necessarily the current in-flight total.

Budget displays come from application values: configured limit, additional grants, consumed value, and gate status. A progress bar may display a filled maximum visually, but the numeric amount must still show an overshoot. Token admission limits must not be mislabeled hard billing caps.

Keep preparation-run and execution-run scope explicit. A combined lifecycle figure may be displayed only if the headless query deliberately supplies and labels that aggregation; the UI must not independently sum possibly overlapping reports.

Do not show dollars, credits, savings percentages, or a token-per-task efficiency conclusion unless the existing report provides the necessary declared valuation and comparison provenance. The default console needs none of these estimates. [R1, R3]

### 5.6 Interventions and preparation review

Group open questions, blockers, preparation approval, and uncertainty/recovery conditions. Identify the related run/task and the action actually available. Unknown or unsupported recovery states remain visible and read-only rather than receiving a generic “Fix” action.

An answer or blocker resolution never implicitly resumes execution. A budget grant increases the existing allowance; it does not erase prior consumption or silently choose a different model. The user must explicitly resume after resolving the conditions of a paused run when resumption is needed.

The preparation detail displays the immutable proposal: requirements, tasks, criteria, dependencies, routing/waves, declared surfaces, assumptions, repository branch/OID, configuration, and proposal fingerprint. Long sections can be expanded or paged without editing them. Approval submits the exact displayed preparation ID and stored hash, never a reconstructed or partially displayed plan.

## 6. User action flows

### 6.1 Prepare

Use a reviewed run-configuration file or existing configuration values supported by Part 1. A lightweight file-path input is sufficient; a new plan-authoring or configuration wizard is not required.

Before submission, identify the repository/specification and state that preparation invokes the bounded Task Creator. Require explicit live consent. On admission, show preparation-in-progress using returned identities; it must stop at `AWAITING_APPROVAL` or the recorded failure. Never chain automatically into start.

### 6.2 Approve and start

The confirmation dialog identifies the canonical branch, starting commit, preparation ID/hash, and declared limits. Its label is “Approve this preparation and start,” not “Accept task.”

Once clicked, mark the request pending and submit the unchanged identity. The application repeats all checks. On `PREPARATION_STALE`, show the reason, invalidate the visible approval action, and reload details on demand. Do not silently reprepare, update the hash, or resubmit consent for a different proposal.

### 6.3 Resume

Show the selected run and pause reason. Resume requires explicit live consent and backend eligibility. The receipt means resumption was admitted; reconciliation may still discover uncertainty and leave the run paused. Describe that result rather than presenting the click as proof that model work restarted.

### 6.4 Pause

Send `request_pause` for the currently owned lifecycle, including its run or preparation-operation identity. A pending pause is visible and repeated requests are idempotent. Keep navigation available.

The display changes to `PAUSED` only from an authoritative snapshot. If completion wins the race, show the actual terminal result. Never overwrite a completed run with a local pending-pause overlay.

For an initial preparation, show the cancellation outcome defined by the headless lifecycle; do not invent a resumable preparation state absent from the current preparation workflow.

### 6.5 Answer, resolve, and grant

Each form is bound to the stable entity and its expected current state. Apply simple local field checks for usability, but rely on backend validation. Preserve the user’s unsent text when refreshing unrelated state.

After submission, display pending, applied, or rejected separately. If the entity was already resolved, show a stale-state conflict and the current value. If the connection is lost before the outcome is known, show “Outcome not confirmed; refresh before trying again,” not an automatic retry button.

Budget amount inputs accept the existing grant kinds and positive integral amounts, with a required reason. The field’s unit must change with the selected kind. Do not use a single unlabeled amount for tokens, turns, and elapsed seconds.

### 6.6 Mutation waiting and navigation

Disabling a pending button is a convenience, not duplicate protection. Generate a request ID and rely on Part 1’s operation registry/admission guards as well.

Once admitted, an operation belongs to the engine. Closing a form or changing screens can stop waiting for its result without cancelling it. A subsequently mounted screen recovers the current state from the feed and operation receipt, not by repeating the mutation.

Use cancellation/exclusive-worker behavior only for replaceable reads such as task details. Do not put a spend-bearing start/resume operation in a Textual worker whose cancellation propagates into mechanical execution.

## 7. Exit, failure, and unavailable state

### 7.1 Explicit application exit

When no preparation or run is actively owned, Exit closes observation and requests host resource closure. A recorded historical `RUNNING` row without this host’s ownership does not authorize a takeover or a provider interrupt.

When an owned operation is active, show two choices: **Keep console open** and **Pause and exit**. There is no “detach and keep running after this process exits” option. The one-process design does not provide that behavior.

Pause and exit requests mechanical shutdown and keeps the display available while it proceeds. Show the current stop request and last known operation. Finish application exit only after the headless lifecycle closes resources or reports the explicit uncertain/error outcome. Do not block the UI loop with a thread join.

A blocking engine operation may delay clean shutdown. The UI must say that accurately. Do not force-stop a Python thread, silently terminate child processes outside the lifecycle, or claim there is a guaranteed short deadline.

### 7.2 Display failure

A failed row/detail renderer should isolate the affected panel where possible and show its error without changing execution. A feed failure shows stale state and offers explicit resynchronization. Never fabricate fresh data to make the panel look healthy.

An unrecoverable failure of the primary Textual application is handled by the outer composition root: request the headless safe-stop policy, restore terminal state, and report the run identity and known outcome. This is an explicit application-lifetime policy, not an exception flowing directly from a widget into the scheduler.

The headless engine remains valid without a display. The UI-failure stop policy applies to this interactive command, not to every possible observer disappearance in future adapters.

## 8. Layout, keyboard behavior, and display safety

### 8.1 Geometry

At 120 columns by 36 rows or larger, allow a compact summary region plus a master/detail split. At 80 by 24, use one principal pane with detail/modal navigation. Below that size, use a compact warning and retain essential stop/exit controls; do not crash or hide a pending shutdown behind an oversized modal.

These sizes are proposed layout test fixtures, not a claim that a particular terminal has been tested. Resize must preserve focus, selection, form content, and actual operation state.

### 8.2 Keyboard and mouse

Use F1–F6 to select the six main sections, Tab/Shift+Tab to move focus, arrows/Page Up/Page Down for navigation, Enter for the selected item’s default action, and Escape to close a detail/dialog without implying mechanical cancellation.

Use Ctrl+P for the pause-request action and Ctrl+Q for the guarded exit flow. Bind these intentionally at the application level, including when an input has focus, and verify behavior in supported terminals. Avoid unmodified `q` as a universal quit key because it is ordinary form input.

Preserve CLI-consistent SIGINT/SIGTERM behavior through the composition root. A terminal producing a signal rather than a key event must reach the same headless pause/shutdown policy. Do not rely on two competing Textual and engine signal handlers.

Mouse selection, scroll, and buttons should be available, but every consequential action must also be keyboard-accessible. Double-clicks must not bypass pending-state protection or backend admission.

### 8.3 Presentation style and safety

Use text labels and icons together, not color alone, to distinguish state. Keep colors semantic and restrained: active, completed, attention, and error must be understandable in monochrome. This is not a brand-theme project.

Render repository names, objectives, questions, errors, and event descriptions as literal text by default. Escape framework markup and neutralize terminal control/ANSI/OSC sequences. Do not turn provider output into active hyperlinks, shell commands, or control characters. Copy operations must be explicit.

Truncation in tables is allowed only when full safe text is available in a detail view. Preserve stable IDs and distinguish omitted data, unavailable data, and actual empty data.

Tokens, credentials, raw principal records, environment variables, and unrestricted provider payloads must never enter presentation state. A last-good snapshot retained during a display error still follows the same data-minimization rules.

## 9. Verification and completion criteria

### 9.1 What has and has not been demonstrated

The accompanying experiments exercise the headless publication/concurrency contracts described in Part 1. They demonstrate specific behavior using a synthetic ledger and actual standard-library primitives. They do **not** instantiate this Textual application, test terminal compatibility, or exercise Task Ledger’s real runtime from a background thread.

Textual’s `run_test` and Pilot facilities support headless UI interaction testing. Use them for the actual adapter acceptance suite rather than treating a screenshot or a passing import as proof of correct behavior. [T3]

### 9.2 Required Textual acceptance cases

| ID | Required case and observable assertion |
|---|---|
| T01 | Run the app against a fake headless client. No import or use of SQLite, ledger service, controller, Git, or provider runtime occurs in a screen/widget. |
| T02 | Launch in an existing project and in an uninitialized directory. Zero model calls and zero unintended state writes occur merely from launch. |
| T03 | Atomic feed attachment displays the initial state without waiting for the next change. Navigation never creates another observer. |
| T04 | Apply a 10,000-update burst and a slow-render scenario. Latest state is eventually shown; pending messages/tasks do not grow with burst size. |
| T05 | Repeated complete snapshots do not duplicate events, increment usage, or mark a worker turn as task completion. |
| T06 | Select A, then B; return B’s details before A’s. The pane remains bound to B. |
| T07 | Reorder/filter/update task rows. Selection and focus remain tied to entity IDs, not row numbers. |
| T08 | Exercise prepare with missing/invalid configuration and without live consent. No unauthorized model operation occurs. |
| T09 | Approve an immutable preparation, then inject a stale backend result. No automatic reprepare, changed-hash start, or retry occurs. |
| T10 | Double-click Start/Resume and repeat a keyboard activation. Only one admission is issued/accepted; actual backend guards are verified in Part 1. |
| T11 | Close a mutation dialog or navigate away while awaiting its receipt. The admitted engine operation remains owned and is not cancelled. |
| T12 | Submit an answer, blocker resolution, or budget grant. Its applied result does not implicitly resume a paused run. |
| T13 | Keep the engine deliberately blocked. Navigation and text input still function; Pause shows requested, not paused. |
| T14 | Race pause with run completion. A terminal authoritative result wins over the local pending request. |
| T15 | Close a screen, cancel a detail worker, and fail an observation read. None directly calls lifecycle cancellation. |
| T16 | Trigger explicit Pause and exit and an unrecoverable application exception. The composition root invokes the headless shutdown policy and does not abruptly kill the engine thread. |
| T17 | Display recorded RUNNING state without host ownership, unknown usage, missing allocations, all-tasks-integrated-but-final-review-pending, and a failed preparation. Labels remain truthful. |
| T18 | Render at 120×36, 80×24, and below minimum, including resizing during a modal and active run. Essential controls and unsent input remain available. |
| T19 | Exercise F1–F6, focus traversal, Escape, pause/exit shortcuts, mouse actions, and input-field behavior. No text entry triggers unintended actions. |
| T20 | Render malicious markup, ANSI/OSC sequences, long IDs, Unicode, and sanitized errors. No control sequence executes, secret field appears, or UI crash occurs. |
| T21 | Read historical activity while new events arrive. Scroll position stays stable, duplicates are absent, and independent source cursors are preserved. |
| T22 | Attempt a report and explicit refresh while observation is stale or the engine is busy. Errors are visible; no timer starts polling and no model is dispatched. |
| T23 | Build/install without the extra and with the selected extra. The CLI remains usable without Textual; missing-extra guidance is accurate. |
| T24 | Run representative real ledger/fake-runtime workflows through the UI and compare their mechanical outcomes with the same headless workflows. |

Unit/UI tests with fakes are necessary but not sufficient. T24 must reuse actual Task Ledger integration fixtures and fake model runtime to establish that the adapter invokes the real application boundary. Follow Part 1’s separate opt-in policy for any provider-specific live verification; no live call is required merely to test a widget.

### 9.3 Release evidence

Record the exact Task Ledger commit, Python/Textual versions, OS/terminal combinations, test commands, failures/skips, and measured fixture sizes. Use the latency targets in Part 1 as targets to test, not performance claims to repeat.

Check package-level import boundaries in automation. Scan both direct imports and code that shells out to `taskledger`; absence of `sqlite3` alone does not prove separation. Pair static checks with fake-client tests that expose only the public contract.

Part 2 is complete when every mandatory case passes or an explicitly accepted limitation is documented, the Part 1 mechanics remain independently tested, and the existing CLI regression suite remains green. A visually convincing dashboard is not sufficient completion evidence.

## 10. Implementation order and web reuse

Build the Textual shell against immutable fixture snapshots first. Then connect its app-level observer and read-only detail queries. Add the existing human-intervention forms, exact preparation approval, and lifecycle controls only after the corresponding headless acceptance tests pass. Add adversarial rendering and cancellation tests before polishing layout.

Keep the eventual web replacement boundary explicit:

| Reused unchanged | Replaced or added for a future web adapter |
|---|---|
| Query values and error semantics | HTTP/HTML serialization and escaping |
| Preparation/start/resume/intervention handlers | Browser forms and confirmation presentation |
| Engine host and execution ownership | Server application startup/shutdown integration |
| Immutable snapshot feed | SSE delivery and browser reconnection bootstrap |
| Durable history/report queries | Browser tables, history navigation, and visual reports |
| Mechanical acceptance tests | Transport security and web UI tests |

The future browser UI is not a reason to introduce web concepts into current widgets or core code. It is a reason to keep those widgets expendable while preserving the tested application mechanics underneath.

## 11. Primary sources

**T1:** [Textual worker guide](https://textual.textualize.io/guide/workers/), especially worker lifetime, cancellation, errors, threading, and `post_message`.

**T2:** [Textual events and messages](https://textual.textualize.io/guide/events/).

**T3:** [Textual testing guide](https://textual.textualize.io/guide/testing/), including `App.run_test` and Pilot.

**T4:** [Textual App API](https://textual.textualize.io/api/app/), including asynchronous application lifecycle APIs.

**R1:** [Task Ledger controller implementation guide, pinned baseline](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/docs/CONTROLLER_IMPLEMENTATION.md).

**R2:** [Task Ledger command registry, pinned baseline](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/COMMAND_REGISTRY.md).

**R3:** [Task Ledger reporting implementation, pinned baseline](https://github.com/christopher-caldwell/taskledger/blob/c1453448436ec8d46d502eb9605876cca026e3fe/src/taskledger/controller/reporting.py).

Framework documentation was consulted for API behavior. The exact framework version and supported-terminal behavior remain implementation verification work, not facts inferred from this document.
