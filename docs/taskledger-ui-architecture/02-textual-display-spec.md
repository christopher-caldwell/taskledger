# Task Ledger Textual UI Specification

**Specification:** TL-UI-1
**Status:** Implementation-ready iterative baseline
**Source branch:** `feat/add_terminal_ui`
**Source head at specification time:** `18728b75578ea93b5a6370cc431fafec8c6faeba` (`feat: Ready for UI`)
**Headless dependency:** Accepted Part 1 (`01-headless-mechanics-spec.md` + `HEADLESS_ACCEPTANCE.md`)
**Framework:** Textual `8.2.8`, through the existing optional `tui` extra
**Scope:** Textual display adapter only
**Supersedes for implementation:** the older Part 2 baseline wherever this document is more specific

---

## 1. Purpose

Build Task Ledger's Textual interface as a **local operator console** over the accepted headless application contract.

The UI exists for visual convenience. It is not a second scheduler, not a task-management product, not an alternate ledger, and not another long-running authority separate from `EngineHost`.

The console must make four questions easy to answer:

1. **What is Task Ledger doing right now?**
2. **What requires my attention?**
3. **What happened to each task, model session, review, and integration?**
4. **How much execution budget has been used, and how complete is the accounting?**

It must also make the already-supported human operations convenient without changing their semantics:

- prepare;
- review an immutable preparation;
- approve and start;
- pause;
- resume;
- answer a worker question;
- resolve a blocker;
- extend an existing controller budget;
- inspect task detail, durable activity history, and the deterministic run report;
- exit safely.

The first implementation should optimize for **truthful state, fast comprehension, and safe operation**, not decorative dashboard behavior.

This is intentionally an iterative UI. The implementation should reach useful visual checkpoints early, run against real Task Ledger fixtures, and refine presentation without moving mechanical behavior into Textual.

---

# 2. Source-grounded baseline

This specification assumes the accepted headless implementation that exists on the source branch at the commit above.

The UI may depend on these public application values:

```text
SnapshotVersion
ApplicationError
OperationReceipt
PauseRequestReceipt
Record
ConsoleSnapshot
HistoryCursor
HistoryPage
TaskPage
OperationStatus
```

The UI is designed around the existing headless surface:

```text
open_feed()
refresh_snapshot()

task_detail(task_id)
task_page(offset, limit)
preparation_detail(preparation_id)
history_page(cursor, limit)
run_report(run_id)

prepare_project(request_id, ...)
start_prepared_project(request_id, ...)
resume_run(request_id, ...)

answer_question(request_id, ...)
resolve_blocker(request_id, ...)
extend_budget(request_id, ...)

request_pause(lifecycle_id)
operation_status(operation_id)

close()
```

The current live snapshot contract contains:

```text
ConsoleSnapshot
    schema_version
    version
    projected_at
    project
    preparation
    run
    tasks
    sessions
    usage
    interventions
    activity
    operation
```

The current headless implementation is accepted before this UI work starts. Textual must not compensate for missing or inconvenient mechanics by bypassing that contract.

---

# 3. Non-negotiable boundary

## 3.1 Dependency direction

The UI depends only on the public application contract.

```text
                         Task Ledger mechanics

                         SQLite / Git / Runtime
                                  ^
                                  |
                       controller / service
                                  ^
                                  |
                            EngineHost
                                  ^
                                  |
                    public application values
                                  ^
                                  |
             +--------------------+--------------------+
             |                                         |
        CLI adapter                               Textual UI
```

The `ui` package may import:

- Textual;
- standard-library presentation helpers;
- immutable application contract classes;
- the public `EngineHost` client surface where required by the composition root.

Screens and widgets must not import or directly call:

- `sqlite3`;
- `Service`;
- `Journal`;
- `ProjectController`;
- `Supervisor`;
- `WorkerBroker`;
- Git helpers;
- provider runtime classes;
- database schema helpers;
- CLI dispatch functions.

No UI widget may:

- mutate SQLite;
- execute a model;
- decide worker routing;
- decide review acceptance;
- integrate Git work;
- alter authoritative usage;
- infer a durable state transition and persist it;
- invoke Task Ledger through a subprocess.

If the UI discovers that the headless API does not expose something required for correctness, fix the application contract first. Do not hide missing mechanics inside a widget.

## 3.2 Authority

The durable ledger is authoritative.

Textual may maintain local presentation state for:

- selection;
- focus;
- filters;
- scroll position;
- active tab;
- open modal;
- unsent input;
- currently loaded history pages;
- whether Activity follow mode is enabled.

It must not create a second authoritative state machine.

---

# 4. UX principles

## 4.1 Accuracy over animation

Never use presentation to imply more certainty than the ledger provides.

Examples:

- `RUNNING` is not the same as "this process owns the run";
- a provider turn finishing is not task completion;
- a worker submitting is not acceptance;
- task acceptance is not integration;
- all tasks integrated is not necessarily final requirement verification;
- `Pause requested` is not `PAUSED`;
- known token usage is not necessarily a complete in-flight total;
- a local spinner is not proof that a provider is healthy.

## 4.2 Current state first, evidence one level deeper

The default view should make current state obvious.

Detailed durable evidence should remain available through:

- task detail;
- activity history;
- preparation detail;
- run report.

Do not put full proposal JSON, evidence blobs, or raw execution logs in the constantly refreshing dashboard.

## 4.3 Dense, not noisy

This is an engineering operator console.

Prefer:

- compact tables;
- short labels;
- strong grouping;
- stable columns;
- status badges;
- details on demand.

Avoid:

- oversized cards that waste terminal space;
- repeated explanatory prose once the user knows the application;
- decorative ASCII art;
- raw dictionaries dumped into the main layout;
- colors with no semantic meaning.

## 4.4 Every mutation is explicit

Navigation must never mutate Task Ledger.

A click or keypress that causes spend-bearing work must make that fact obvious.

Prepare, start, and resume require explicit live consent.

Pause should be immediate and does not need an extra confirmation.

## 4.5 Keyboard-first, mouse-capable

Everything important must work from the keyboard.

Mouse support should work naturally for:

- selecting tabs;
- selecting table rows;
- clicking buttons;
- scrolling;
- modal controls.

The UI must not require the mouse.

## 4.6 Preserve the user's place

Live updates must not repeatedly reset:

- active tab;
- selected task;
- table scroll position;
- open detail view;
- activity follow mode;
- form input;
- modal input;
- local filters.

---

# 5. Framework decisions

## 5.1 Textual release

Use the already pinned dependency:

```toml
tui = ["textual==8.2.8"]
```

Do not change the Textual version during the initial UI implementation unless an actual framework defect requires it.

## 5.2 Disable Textual's command palette

Set:

```python
ENABLE_COMMAND_PALETTE = False
```

Task Ledger reserves `Ctrl+P` for **Pause**.

The first UI does not need a second command-discovery surface. Help is provided through `?` and the footer.

## 5.3 Theme

Use Textual's built-in `textual-dark` theme for v1.

Do not build a custom brand theme before the interaction model is proven.

Style with semantic Textual theme variables rather than hard-coded arbitrary colors:

```text
$background
$surface
$panel
$primary
$accent
$success
$warning
$error

$text
$text-muted
$text-success
$text-warning
$text-error
$text-primary
```

This keeps status semantics consistent and leaves room for future theme support.

## 5.4 CSS location

Move substantial styling out of inline Python.

Use:

```text
src/taskledger/ui/taskledger.tcss
```

Small dynamic style changes may remain in code, but layout and color rules belong in TCSS.

## 5.5 Modal versus full screen

Use a `ModalScreen` for short, bounded decisions:

- start confirmation;
- resume confirmation;
- answer question;
- resolve blocker;
- budget grant;
- exit confirmation;
- help.

Use a pushed full `Screen` for long content:

- preparation form;
- preparation review;
- task detail in non-wide layouts;
- full run report.

The preparation form should not be squeezed into an 80x24 modal.

---

# 6. Application composition

## 6.1 Lifetime ownership

`TaskledgerApp` is the single presentation root.

It receives a public headless client / `EngineHost`.

The app owns:

- exactly one feed reader;
- presentation state;
- active screens;
- replaceable detail-query workers.

The host owns:

- execution;
- mutations;
- SQLite;
- provider runtime;
- lifecycle tasks;
- safe shutdown.

## 6.2 Exactly one feed consumer

Attach exactly once:

```text
TaskledgerApp.on_mount
    -> client.open_feed()
    -> one long-lived UI observation coroutine
```

Do not subscribe per tab.

Do not reopen the feed when changing screens.

Do not separately load a snapshot and then subscribe. The feed already provides an atomic initial value.

## 6.3 UI-side update path

```text
headless publishes snapshot
       |
       v
FeedReader.receive()
       |
       v
TaskledgerApp accepts newer revision
       |
       v
replace App presentation snapshot
       |
       +--> header
       +--> global banner
       +--> active view
       +--> cheap inactive-view cache updates when needed
```

No SQLite polling.

No repeating `refresh_snapshot()` timer.

The only repeating timer permitted is presentation-only work such as updating a visible elapsed-time label.

## 6.4 Snapshot ordering

For the same engine epoch:

```text
incoming.revision <= current.revision
```

must be ignored.

A new engine epoch is a new baseline.

Do not compare revision numbers across epochs.

## 6.5 Do not render every section on every update

A snapshot may change because usage or activity changed while task rows remained identical.

Each view should retain its previous input slice and update only when relevant values differ.

Examples:

```text
Overview
    project + preparation + run + usage + interventions + operation

Tasks
    tasks + project.tasks_truncated + project.task_count

Agents
    sessions + current run identity

Activity
    activity

Usage
    usage + run

Interventions
    interventions + preparation + run + operation
```

Avoid remounting whole screens on each snapshot.

---

# 7. Presentation state

Use a small UI-specific state holder. It is not a domain store.

A reasonable shape is:

```text
UiState
    snapshot

    active_section
    layout_mode

    selected_task_id
    task_detail
    task_detail_generation

    loaded_task_rows
    task_total
    task_filter

    selected_session_id

    recent_activity
    history_records
    history_cursor
    activity_mode
    activity_follow
    unseen_activity_count

    tracked_operation_ids

    last_ui_error
```

It is acceptable for this to be ordinary `TaskledgerApp` fields rather than a formal state class if that remains clearer.

Do not create a Redux-style state framework.

---

# 8. Application shell

## 8.1 Structure

Use:

```text
TaskledgerApp
    ConsoleHeader
    GlobalBannerArea
    TabbedContent
        Overview
        Tasks
        Agents
        Activity
        Usage
        Interventions
    Footer
```

A custom compact header is preferred over Textual's generic `Header` because the vertical space should carry current Task Ledger context.

## 8.2 Wide example

```text
┌ Task Ledger ─ financial-tracker ─ feat/backend ────────────────────────────────┐
│ RUNNING   EXECUTING   run 8f2c…   Requirements 7/9   Tasks 5/8   31.2k known │
├─────────────────────────────────────────────────────────────────────────────────┤
│ F1 Overview  F2 Tasks  F3 Agents  F4 Activity  F5 Usage  F6 Interventions     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│                              active section                                   │
│                                                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Ctrl+P Pause   R Refresh   ? Help   Ctrl+Q Exit                               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 8.3 Header contents

Header line 1:

- `Task Ledger`;
- repository basename;
- canonical branch;
- run state.

Header line 2 where space permits:

- effective phase;
- abbreviated run ID;
- requirement completion;
- completed task count;
- known token subtotal;
- active-lifecycle ownership indicator.

In narrower modes, reduce this progressively.

Never remove the run state.

## 8.4 Global banners

Banners appear between the header/navigation and current content.

Priority order:

1. observation degraded;
2. engine closing;
3. pause requested;
4. active lifecycle operation;
5. stale detail warning.

Do not stack many global banners. Show the highest-severity global banner plus screen-local state where necessary.

Examples:

```text
PAUSE REQUESTED — waiting for the current safe boundary
```

```text
OBSERVATION DEGRADED — showing last confirmed snapshot; press R to refresh
```

```text
PREPARING — Task Creator operation 94a3…
```

---

# 9. Responsive layouts

The UI is designed around terminal geometry, not desktop pixels.

Switch layout mode on Textual `Resize` events by adding/removing CSS classes on the app or root screen.

Do not destroy and reconstruct the screen tree on every resize.

Preserve selection and form contents.

## 9.1 Wide

Definition:

```text
width >= 120
height >= 30
```

Behavior:

- two-pane Tasks;
- two-pane Agents where useful;
- two-pane Interventions;
- Overview may use 2–3 metric columns;
- Activity and Usage use full width;
- task detail remains visible beside selection.

## 9.2 Standard

Definition:

```text
width 80–119
height >= 24
```

This is the canonical minimum fully featured layout.

Behavior:

- one primary pane per tab;
- task/session/intervention detail opens as a pushed screen or modal;
- reduced Overview column count;
- all mutation flows remain available.

Textual's common 80x24 test viewport must be treated as a first-class experience.

## 9.3 Compact

Definition:

```text
width 64–79
or
height 20–23
```

Behavior:

- hide low-value table columns;
- shorten labels;
- use full-screen detail;
- retain all lifecycle controls;
- footer may show fewer bindings;
- `Interventions` may be labeled `Action` if required for space.

Do not silently omit important errors or pause state.

## 9.4 Below minimum

Definition:

```text
width < 64
or
height < 20
```

Show a restricted safe view:

```text
Task Ledger
RUNNING — Pause requested

Terminal is too small for the full console.
Minimum recommended size: 64 x 20.

Ctrl+P Pause    Ctrl+Q Exit
```

If no lifecycle is active, show repository/run state and Exit.

Do not attempt to cram complex forms into this geometry.

If a form is already open when resized below minimum, preserve its values internally and show the size warning until geometry becomes usable again.

---

# 10. Global keybindings

Required global bindings:

| Key | Action |
|---|---|
| `F1` | Overview |
| `F2` | Tasks |
| `F3` | Agents |
| `F4` | Activity |
| `F5` | Usage |
| `F6` | Interventions |
| `Ctrl+P` | Request pause when eligible |
| `R` | Explicit snapshot refresh |
| `?` | Help |
| `Ctrl+Q` | Guarded exit |
| `Esc` | Close modal / pop detail screen / back |

`R`, `?`, and screen-navigation bindings must not fire while a text editor is consuming text input if Textual binding priority would make that surprising.

The footer should expose relevant bindings.

Screen-specific bindings:

| Screen | Key | Action |
|---|---|---|
| Tasks | `Enter` | Open selected task |
| Tasks | `/` | Focus local task filter |
| Activity | `F` | Toggle follow |
| Activity | `H` | Toggle Recent / History |
| Activity | `L` | Load next history page |
| Usage | `Enter` or button | Open run report |
| Interventions | `Enter` | Open applicable action/detail |

Do not bind a single unmodified letter to a destructive or spend-bearing operation.

---

# 11. Status vocabulary

Presentation labels may be friendlier, but the authoritative raw state should remain visible in detail where useful.

## 11.1 Run

```text
RUNNING
PAUSED
COMPLETED
FAILED
```

`RUNNING` plus `owned_by_host=False` must be visibly distinct:

```text
RECORDED RUNNING — not owned by this console
```

Do not display it as an actively controlled local run.

## 11.2 Preparation

```text
PLANNING            Preparing
AWAITING_APPROVAL   Approval required
APPROVED            Approved
SUPERSEDED          Superseded
FAILED              Failed
```

## 11.3 Turn/session uncertainty

`UNCERTAIN` must use warning/error semantics and retain the literal word `Uncertain`.

Do not relabel it `Failed`.

## 11.4 Operation

Relevant application phases:

```text
ACCEPTED
SUCCEEDED
FAILED
UNKNOWN_OPERATION
```

Transient snapshot operation phases such as:

```text
INITIALIZING
ADMITTING
```

are active application work, not durable run states.

---

# 12. Color and emphasis

Use semantic styling.

Suggested mapping:

| Meaning | Styling |
|---|---|
| selected / primary action | `$primary` / `$text-primary` |
| completed / satisfied | success semantic colors |
| paused / awaiting approval / incomplete accounting | warning semantic colors |
| failed / uncertain / blocking error | error semantic colors |
| informational active state | primary semantic colors |
| inactive IDs / secondary metadata | `$text-muted` |
| panels | `$surface` / `$panel` |

Color is never the only signal.

Always include text such as:

```text
PAUSED
FAILED
UNCERTAIN
APPROVAL REQUIRED
```

---

# 13. Reusable presentation components

Keep this set small.

A reasonable initial component set:

```text
StatusBadge
Metric
KeyValueList
EmptyState
OperationBanner
SectionTitle
```

Do not create one Python file per tiny widget.

A practical organization is:

```text
src/taskledger/ui/
    app.py
    state.py
    formatting.py
    widgets.py
    dialogs.py
    taskledger.tcss

    views/
        overview.py
        tasks.py
        agents.py
        activity.py
        usage.py
        interventions.py
        preparation.py
        report.py
```

If that is too fragmented, related small views may share a module.

The boundary matters more than file count.

---

# 14. Overview

## 14.1 Purpose

Overview answers:

> What is happening, how far along is it, and do I need to do anything?

## 14.2 Wide layout

```text
┌ Lifecycle ───────────────────────────────────────────────────────────────────┐
│ RUNNING   EXECUTING                     started 00:18:42 ago                 │
│ Run 8f2c…                              owned by this console                 │
└─────────────────────────────────────────────────────────────────────────────┘

┌ Requirements ───────┐ ┌ Tasks ─────────────┐ ┌ Usage ──────────────────────┐
│ 7 / 9 complete      │ │ 5 / 8 completed    │ │ 31,240 known / 40,000       │
│ 1 blocked           │ │ 1 submitted        │ │ accounting COMPLETE          │
│ 1 awaiting verify   │ │ 2 active/planned   │ │ +0 token grant               │
└─────────────────────┘ └─────────────────────┘ └──────────────────────────────┘

┌ Needs attention ─────────────────────────────────────────────────────────────┐
│ QUESTION   task abc…   Which migration strategy should I use?    [Answer]    │
│ BLOCKER    task def…   External fixture missing                  [Resolve]   │
└─────────────────────────────────────────────────────────────────────────────┘

┌ Recent activity ─────────────────────────────────────────────────────────────┐
│ 05:41:12Z  TURN_COMPLETED          worker…                                   │
│ 05:41:14Z  SUBMISSION_RECORDED     task…                                     │
│ 05:41:16Z  TURN_DISPATCHED         reviewer…                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 14.3 Requirements metric

Use authoritative values from:

```text
project.progress.requirements.total
project.progress.requirements.complete
project.progress.requirements.remaining
project.progress.requirements.blocked
project.progress.requirements.awaiting_final_verification
```

Do not substitute task completion for requirement completion.

## 14.4 Task metric

Use:

```text
project.progress.tasks.completed
project.progress.tasks.cancelled
project.progress.tasks.active_implementation
project.progress.tasks.awaiting_submission_verification
project.progress.tasks.accepted_awaiting_integration
project.task_count
```

Recommended completion denominator:

```text
task_count - cancelled
```

Label it explicitly as task completion/integration progress, not project completion.

## 14.5 Usage metric

Use the application-supplied known token subtotal and accounting status.

If accounting is incomplete, the number must be labeled `known`.

Do not display unknown as zero.

## 14.6 Current action

Overview should expose valid user actions from:

```text
project.available_actions
preparation state
run state
run.owned_by_host
run.pause_requested
snapshot.operation
```

`available_actions` are presentation hints, not authorization. The backend still validates on click.

### Primary action priority

Highlight at most one primary action:

1. `Review preparation` when approval is required;
2. `Pause` while an owned run is executing;
3. `Resume` when a run is resumable;
4. `Prepare` when no current lifecycle prevents it;
5. `View report` when a run is terminal.

Secondary valid actions may remain available but less prominent.

## 14.7 Uninitialized repository

Show:

```text
Task Ledger is not initialized for this repository.

Repository
/path/to/repository

Preparing this repository will initialize Task Ledger and start a bounded
Task Creator planning operation.

[Prepare]
```

Opening the UI alone must not initialize anything.

---

# 15. Tasks

## 15.1 Table

Use Textual `DataTable`.

Configure:

```text
cursor_type = "row"
zebra_stripes = True
```

Every row key must be the durable task ID.

Never use the row index as task identity.

## 15.2 Wide columns

```text
State
Activity
Wave
Profile
Deps
Blocks
Objective
```

Objective receives remaining width.

Do not show the full task ID as a mandatory column.

## 15.3 Standard columns

```text
State
Activity
Profile
Objective
```

## 15.4 Compact columns

```text
State
Objective
```

## 15.5 Presentation-only activity

Activity is not an authoritative task state.

Derive a conservative display label from the same snapshot:

```text
blocker_count > 0
    -> Blocked

state == PLANNED and assignment_id is None
    -> Waiting

state == PLANNED and assignment_id exists
    -> Queued

state == ASSIGNED
    -> Implementing

state == SUBMITTED
    -> Review

state == ACCEPTED
    -> Integration

state == COMPLETED and integrated
    -> Integrated

state == CANCELLED
    -> Cancelled
```

Do not invent persisted states such as `REVIEWING`.

## 15.6 Updating rows

Prefer keyed incremental updates.

On a new snapshot:

- add new task IDs;
- update changed cells;
- remove rows no longer present where appropriate;
- preserve `selected_task_id`;
- restore the cursor to that row key.

Avoid clearing and rebuilding the full table on every usage or activity change.

## 15.7 Pagination

The compact snapshot may contain only the first 1,000 task summaries.

If:

```text
project.tasks_truncated == True
```

show:

```text
Showing 1,000 of N tasks
```

Support explicit loading through:

```text
task_page(offset, limit)
```

Suggested page size:

```text
200
```

Local filtering applies only to loaded task rows.

If not every task is loaded, the filter area must say something equivalent to:

```text
Filter applies to 1,200 loaded tasks of 1,842 total
```

Do not present a local filter as a project-wide search.

## 15.8 Detail layout

### Wide

Display detail beside the table.

Suggested split:

```text
42% table
58% detail
```

### Standard / compact

`Enter` pushes a full `TaskDetailScreen`.

## 15.9 Detail query race

Use a generation counter:

```text
selection generation 41 -> task A request
selection generation 42 -> task B request

A returns after B
A must be discarded
```

An exclusive Textual worker is appropriate for replaceable detail reads.

Still verify the generation and task ID before rendering.

## 15.10 Task detail contents

Header:

```text
objective
authoritative state
task ID
revision
implementation scope
```

Sections:

### Requirements

- linked requirement IDs.

### Dependencies

- dependency task IDs.

### Acceptance criteria

Ordered list.

### Required checks

Ordered exact commands.

### Assignments

For each:

- assignment ID;
- attempt;
- profile;
- execution mode;
- state;
- activated/closed time.

### Checkpoints

- label;
- state;
- summary;
- submitted/resolved times;
- corrections/notes when present.

### Submissions

- sequence;
- state;
- summary;
- commit;
- submitted/resolved time.

### Independent verification

- outcome;
- criterion results;
- behavior-match flag;
- evidence-present flag;
- blocking-issues flag;
- corrections;
- notes.

### Execution receipts

- role;
- command;
- status;
- exit code;
- times;
- artifact ID.

Do not inline full output artifacts.

### Evidence

- provenance kind;
- original path;
- stored path where safe;
- SHA;
- size;
- source revision.

### Integration attempts

- state;
- before OID;
- accepted OID;
- after OID;
- times.

Use `Collapsible` sections so the detail pane is not a wall of text.

## 15.11 V1 task actions

Task detail is read-only.

Do not add:

- manual completion;
- task editing;
- reviewer acceptance;
- rerouting;
- wave changes;
- retry buttons.

---

# 16. Agents

## 16.1 Purpose

Show semantic model sessions without confusing session existence with active execution.

## 16.2 Table

Rows are keyed by session ID.

Recommended wide columns:

```text
Role
State
Turn
Turns
Profile
Model
Effort
Subject
```

Standard:

```text
Role
State
Turn
Profile
Subject
```

Compact:

```text
Role
Turn
Subject
```

## 16.3 Ordering

Default order:

1. active sessions first;
2. uncertain sessions;
3. closed sessions;
4. newest first within group.

## 16.4 Current-run filtering

Default to sessions belonging to the current run when there is one.

Offer a local toggle:

```text
Current run | All recorded
```

This is presentation filtering only.

## 16.5 Semantics

Distinguish:

```text
Session ACTIVE + latest turn RUNNING
    Active turn

Session ACTIVE + latest turn COMPLETED/FAILED
    Retained / between turns

Session CLOSED
    Closed

Session UNCERTAIN
    Uncertain
```

Do not animate an active session with no active turn as if tokens are being spent.

## 16.6 Session detail

No new headless session-detail query is required for v1.

Use summary information already present in the snapshot:

- session ID;
- run ID;
- role;
- profile;
- subject ID;
- external thread ID;
- state;
- model;
- effort;
- latest turn state;
- turn count;
- created/closed timestamps.

There is no provider transcript view in v1.

---

# 17. Activity

## 17.1 Two modes

Use two explicit modes:

```text
Recent
History
```

This avoids pretending the current forward cursor API is reverse pagination.

## 17.2 Recent mode

Seed from:

```text
snapshot.activity
```

Use a `DataTable` keyed by:

```text
(source, sequence)
```

Recommended columns:

```text
Time
Source
Event
Entity
```

Keep raw source identity.

Recommended order: chronological top-to-bottom with newest at the bottom, because follow mode then behaves like a conventional live log.

## 17.3 Merging live updates

For each snapshot activity record:

- compute `(source, sequence)`;
- skip if already present;
- insert in deterministic timestamp/source/sequence order.

Do not append duplicates merely because every snapshot repeats recent history.

Bound Recent UI memory to a practical local limit such as:

```text
500 rows
```

Removing older rows from Recent mode does not delete durable history.

## 17.4 Follow mode

Default:

```text
follow = True
```

While follow is enabled and the user has not manually scrolled away, keep newest events visible.

If the user scrolls upward:

- turn follow off;
- do not yank the viewport back;
- show:

```text
7 newer events
```

`F` re-enables follow and moves to the newest event.

## 17.5 History mode

History mode uses:

```text
history_page(HistoryCursor(), limit)
```

to walk durable history from the beginning.

Suggested page size:

```text
200
```

`L` loads the next page.

Maintain the returned independent audit/controller cursor.

Do not create one synthetic global sequence.

Display both source and source sequence in detail.

When a page returns no new rows, show:

```text
End of recorded history
```

## 17.6 Activity detail

Selecting an activity row may show:

- timestamp;
- source;
- source sequence;
- event type;
- entity type;
- entity ID.

Do not invent event payloads that the application query did not expose.

## 17.7 Exclusions

Activity v1 does not show:

- raw provider reasoning;
- raw model transcript;
- streaming assistant prose;
- raw check output;
- raw JSON-RPC traffic.

---

# 18. Usage

## 18.1 Purpose

Show budget and accounting truth, not estimates disguised as accounting.

## 18.2 Primary usage panel

Display:

```text
Known tokens
Accounting status
Terminal turns
Completed turns
Missing-usage turns
Unresolved turns
```

If a known token subtotal is available:

```text
31,240 known tokens
```

If accounting is incomplete:

```text
31,240 known tokens
2 active/unreconciled turns
```

Never show `31,240 total` when it is only the known subtotal.

## 18.3 Token breakdown

Table:

```text
Input
Cached input
Non-cached input
Cache write input
Output
Reasoning
```

Respect the accounting model already supplied by the headless layer.

Do not sum reasoning again into a total if the application does not.

## 18.4 Token budget

Read the configured token limit from application-supplied configuration and add audited `TOKENS` grants.

Display:

```text
Known admission usage     31,240
Base limit                40,000
Additional grants          5,000
Effective allowance       45,000
```

A progress bar is appropriate when a denominator is known.

If known usage exceeds allowance:

- keep the numeric overshoot visible;
- clamp only the visual bar.

Label this:

```text
Admission budget
```

not:

```text
Billing cap
```

## 18.5 Other limits

Display a compact Limits table.

Possible rows:

```text
Worker concurrency
Reviewer concurrency
Worker turns total
Reviewer turns total
Task Creator turns
Worker turns / assignment
Reviewer turns / submission
Final reviewer turns
Elapsed seconds
Turn timeout
Reviewer token reserve
Max inflight targets
```

Only render fields supplied by the current configuration.

## 18.6 Grants

Table:

```text
Kind
Additional amount
```

The current compact snapshot may provide aggregated grants by kind rather than individual grant history. Display only what is supplied.

## 18.7 Role turn counts

From coherent snapshot sessions, display observed session turn counts by role as descriptive data.

Do not label them exact grant-consumption counters unless the headless report supplies that exact semantic.

## 18.8 Run report

Provide:

```text
[Open full run report]
```

only when a run ID exists.

Fetching the report is on demand.

Do not refresh it on every snapshot.

The Report screen should render major report groups with `Collapsible` sections and compact tables where structure is known.

Expected major categories from the current report include:

- economics / token usage;
- scheduler timing / waits;
- routing and worker behavior;
- reviews / verification;
- integration / reliability;
- quality / accounting;
- context efficiency where present.

Unknown future fields should fall back to a safe pretty key/value renderer rather than crash the screen.

`R` on the Report screen explicitly re-fetches the report.

---

# 19. Interventions

## 19.1 Table

Rows keyed by intervention ID.

Columns:

```text
Kind
Scope
Created
Description
```

Kinds currently expected:

```text
QUESTION
BLOCKER
UNCERTAIN_TURN
PREPARATION_APPROVAL
```

## 19.2 Actions by kind

### QUESTION

Action:

```text
Answer
```

Open `AnswerQuestionDialog`.

V1 payload:

```json
{
  "question_id": "...",
  "answer": "..."
}
```

Do **not** automatically resolve a linked blocker through the optional combined service behavior.

Reason: the compact intervention projection does not expose the question/blocker linkage, and keeping Answer and Resolve as separate explicit operations is clearer.

### BLOCKER

Action:

```text
Resolve
```

Payload:

```json
{
  "blocker_id": "...",
  "resolution": "..."
}
```

### PREPARATION_APPROVAL

Action:

```text
Review preparation
```

Push `PreparationReviewScreen`.

### UNCERTAIN_TURN

Read-only in v1.

Display:

```text
Recovery required
```

Do not invent a generic Retry button. The public visual application contract does not expose arbitrary recovery mutations.

## 19.3 Empty state

Use:

```text
No human intervention required.
```

Do not make this a large celebratory screen.

---

# 20. Prepare workflow

## 20.1 Entry points

Prepare is available from:

- Overview primary action;
- the uninitialized repository state.

## 20.2 Screen

Use a full `PrepareScreen`.

Do not use a small modal.

## 20.3 Basic fields

### Specification path

Required `Input`.

Example:

```text
docs/spec.md
```

Do not assume `spec.md` exists unless the user enters it or later product behavior explicitly supplies a default.

### Live consent

Required checkbox:

```text
Start live Task Creator planning
```

The Submit button remains disabled until checked.

## 20.4 Limits

Put limits in a `Collapsible` titled:

```text
Execution limits
```

Prepopulate the current parser defaults:

```text
max_initial_planner_turns           2
max_total_worker_turns            100
max_total_reviewer_turns           50
max_total_task_creator_turns        6
max_worker_turns_per_assignment    20
max_reviewer_turns_per_submission   2
max_final_reviewer_turns            2
max_total_tokens                40000
turn_timeout_seconds             1800
max_elapsed_seconds              3600
max_workers                          2
max_reviewers                        1
reviewer_token_reserve               0
max_inflight_targets             blank / None
```

Use normal `Input` widgets with numeric validation.

Do not hide the token limit.

## 20.5 Advanced preflight

Use another `Collapsible`:

```text
Advanced preflight
```

### Local inputs

Multiline `TextArea`, JSON array, default:

```json
[]
```

### Services

Multiline `TextArea`, JSON array, default:

```json
[]
```

This is intentionally technical. V1 does not need a graphical builder for arbitrary preflight structures.

Validate that each parses to a JSON array before submission.

## 20.6 Run group

Optional advanced field:

```text
run_group_id
```

Do not show it prominently.

It exists mainly for continued preparation-attempt lineage.

## 20.7 Local validation

Before submit:

- spec path non-empty;
- live consent checked;
- integer fields parse as integers;
- positive fields > 0;
- reviewer reserve >= 0;
- reviewer reserve <= total tokens;
- max inflight blank or positive integer;
- local inputs valid JSON array;
- services valid JSON array.

Backend validation remains authoritative.

## 20.8 Submission

Generate one request ID.

Call:

```text
prepare_project(request_id, ...)
```

Do not put the lifecycle operation itself inside a cancel-propagating Textual worker.

A short UI task may await the admission receipt, but engine ownership remains independent.

### ACCEPTED

Show:

```text
Preparation admitted
```

Return to Overview.

The global operation banner shows continuing planning.

### REJECTED

Remain on the Prepare screen.

Preserve every entered value.

Display the application error above the form.

Do not generate another request automatically.

## 20.9 Preparation completion

The app learns through live snapshots.

Possible user-facing outcomes:

```text
Approval required
Failed
```

Never chain automatically into start.

---

# 21. Preparation review

## 21.1 Entry

Available when:

```text
preparation.state == AWAITING_APPROVAL
```

or through the matching intervention.

## 21.2 Data load

Use:

```text
preparation_detail(preparation_id)
```

This is a replaceable detail read and may use an exclusive UI worker.

## 21.3 Identity block

Always visible:

```text
Preparation ID
Proposal hash
Specification hash
Canonical branch
Starting commit
Planning run ID
Created
Updated
```

Use full hashes in a scrollable/selectable detail area where possible.

In compact summaries, abbreviate but never submit an abbreviated hash.

## 21.4 Proposal sections

Render the immutable proposal in human-readable sections.

### Summary

Counts:

```text
Requirements
Tasks
Execution waves
Routine tasks
Complex tasks
Assumptions
```

### Requirements

For each:

```text
ref
statement
details
implementation_required
source locators
source excerpts when present
```

### Tasks

For each:

```text
ref
objective
implementation scope
acceptance criteria
required checks
requirement refs
dependency refs
```

### Execution policy

Table:

```text
Task
Wave
Profile
Parallel
Write surfaces
```

### Assumptions

Ordered list.

### Ambiguities

Display if present.

An `AWAITING_APPROVAL` proposal should normally have no unresolved ambiguity, but the UI displays what is actually stored.

### Attempts

Display preparation-attempt lineage supplied by the detail query.

### Configuration

Display stored run configuration in a collapsed technical section.

Do not allow editing of any stored preparation value.

## 21.5 Staleness while open

The screen keeps the exact loaded:

```text
preparation_id
proposal_hash
```

On every new snapshot, compare against the current preparation summary.

If the preparation is no longer:

```text
same ID
same proposal hash
AWAITING_APPROVAL
```

show:

```text
This preparation is no longer current.
```

Disable Approve & Start.

Do not silently reload into a different preparation and leave the action active.

## 21.6 Approve & Start

Button:

```text
Approve & Start
```

opens a confirmation modal.

Confirmation displays:

```text
Canonical branch
Starting commit
Proposal hash
Token admission budget
Worker/reviewer concurrency
```

Require a checkbox:

```text
Start live execution using exactly this preparation
```

Submit:

```text
start_prepared_project(
    request_id,
    preparation_id=<exact loaded ID>,
    approve_proposal_hash=<exact full stored hash>,
    live=True,
)
```

Never reconstruct the plan from displayed rows.

### ACCEPTED

Close the confirmation.

Navigate to Overview.

Show the admitted run ID.

### REJECTED

Keep the preparation review screen open.

Display the application error.

If stale, disable the start button.

---

# 22. Resume

## 22.1 Entry

Resume appears when backend action eligibility allows it and a current project/assignment run is paused or otherwise resumable.

## 22.2 Confirmation

Modal displays:

```text
Run ID
Recorded state
Pause reason
Pause detail
```

Required checkbox:

```text
Resume live execution
```

Call:

```text
resume_run(request_id, run_id=..., live=True)
```

### ACCEPTED

Close modal.

Navigate to Overview.

Do not immediately display "Running successfully" merely because admission succeeded.

The authoritative snapshot determines actual state after reconciliation.

### REJECTED

Remain in the current screen and show the error.

---

# 23. Pause

## 23.1 No confirmation dialog

Pause is a safety/control action.

`Ctrl+P` or a Pause button should request it immediately when an owned lifecycle exists.

## 23.2 Lifecycle target

Resolve target conservatively:

1. if `snapshot.operation` exposes an active lifecycle ID for preparation, use that;
2. otherwise if the current run is `RUNNING` and `owned_by_host`, use the run ID;
3. otherwise Pause is unavailable.

Never send pause for a historical/external recorded run merely because its row says `RUNNING`.

## 23.3 Receipt handling

### REQUESTED

Immediately show:

```text
Pause requested — waiting for safe point
```

### ALREADY_REQUESTED

Keep the same state.

### NOT_RUNNING

Show a short notice and request one explicit refresh.

Do not interpret it as an error requiring automatic retry.

## 23.4 Authoritative completion

Only a snapshot can change the visible durable state to:

```text
PAUSED
COMPLETED
FAILED
```

If completion wins the race, show `COMPLETED`.

---

# 24. Answer question

Use `ModalScreen`.

Contents:

```text
Question
<full question body>

Answer
<TextArea>

[Cancel] [Answer]
```

Submit only:

```text
question_id
answer
```

Do not auto-resolve blockers in v1.

On `APPLIED`:

- dismiss modal;
- show a short success notification;
- wait for the next snapshot.

On `REJECTED`:

- keep modal open;
- preserve answer text;
- display error.

---

# 25. Resolve blocker

Use `ModalScreen`.

Contents:

```text
Blocker
<description>

Resolution
<TextArea>

[Cancel] [Resolve]
```

Submit:

```text
blocker_id
resolution
```

Same receipt behavior as question answer.

Resolving a blocker does not resume execution.

---

# 26. Budget grant

## 26.1 Entry

Available from:

- Usage;
- Overview when budget exhaustion is the pause reason.

## 26.2 Modal

### Kind

Textual `Select` with values:

```text
WORKER_TURNS
REVIEWER_TURNS
TASK_CREATOR_TURNS
TOKENS
ELAPSED_SECONDS
```

Friendly labels:

```text
Worker turns
Reviewer turns
Task Creator turns
Tokens
Elapsed seconds
```

### Amount

Positive integer `Input`.

Update suffix/help based on selected unit.

### Reason

Required `Input` or short `TextArea`.

## 26.3 Submit

```text
extend_budget(
    request_id,
    run_id=current_run_id,
    kind=...,
    amount=...,
    reason=...,
)
```

On applied:

- close;
- show success;
- do not resume automatically.

---

# 27. Operation-state handling

## 27.1 Immediate receipts

### APPLIED

Short mutations:

```text
answer_question
resolve_blocker
extend_budget
```

may return `APPLIED`.

Show success and wait for the next snapshot.

### REJECTED

Display the `ApplicationError`.

Do not repeat automatically.

### ACCEPTED

A long lifecycle operation is now engine-owned.

Track its operation ID locally.

## 27.2 Event-driven terminal status lookup

Do not create a repeating operation-status poll.

When an accepted operation is locally tracked:

1. render active state from `snapshot.operation`;
2. when a newer snapshot indicates that operation is no longer active or related durable lifecycle state changed, call `operation_status(id)` once;
3. if still `ACCEPTED`, retain tracking until another relevant snapshot;
4. if `SUCCEEDED` or `FAILED`, present result/error and stop tracking.

## 27.3 Unknown operation

If:

```text
UNKNOWN_OPERATION
```

show:

```text
The prior operation result is no longer retained.
Current durable Task Ledger state is authoritative.
Do not retry automatically.
```

## 27.4 Request IDs

Generate UUID request IDs in the UI.

A form submission retains its request ID until the admission call returns.

Do not generate a new ID because the user changed tabs while waiting.

---

# 28. Exit behavior

## 28.1 Idle

If no owned preparation/execution lifecycle is active:

`Ctrl+Q` may exit directly unless there is unsent form input.

The composition root then performs host closure through `run_ui(... finally: client.close())`.

## 28.2 Active lifecycle

Show modal:

```text
Task Ledger is still executing.

Exiting will request a safe pause and wait for Task Ledger to reach a safe
boundary before closing.

[Keep Running] [Pause and Exit]
```

`Keep Running` means cancel the exit request and remain in the current process.

There is no detach-and-continue-after-process-exit mode.

## 28.3 Pause and Exit

1. call `request_pause()` where applicable;
2. display closing state;
3. allow `EngineHost.close()` to perform actual safe shutdown;
4. do not implement duplicate interruption/reconciliation in Textual.

If shutdown remains at a safe boundary longer than expected, show:

```text
Waiting for Task Ledger to reach a safe shutdown point…
```

Do not force-kill the engine thread.

## 28.4 Unsent form state

If a form contains unsent edits, global exit should confirm discarding UI input even if there is no active lifecycle.

Changing tabs should not discard a pushed form because pushed forms are separate screens.

## 28.5 Fatal display error

The composition root's `finally: client.close()` is the safety path.

Do not try to catch every fatal rendering exception and reproduce shutdown semantics inside widgets.

Where possible, restore the terminal and surface a plain fallback error after safe host closure.

---

# 29. Observation degradation

The snapshot may expose:

```text
project.cache_health
project.observation_error
```

When degraded:

- keep the last good snapshot visible;
- show an error banner;
- display `projected_at`;
- enable explicit Refresh;
- do not clear the screen;
- do not infer new state locally.

`R` calls:

```text
refresh_snapshot()
```

once.

No retry timer.

---

# 30. Formatting utilities

Centralize formatting helpers for:

- status labels;
- short IDs;
- integer separators;
- token counts;
- durations;
- UTC timestamps;
- boolean yes/no;
- optional values;
- safe untrusted text;
- nested `Record` / tuple-pair conversion for known application fields.

Do not format the same state independently in six screens.

## 30.1 IDs

Table:

```text
8f2c91…
```

Detail:

```text
8f2c91f0-...
```

Never submit an abbreviated ID back to the application.

## 30.2 Numbers

Use separators:

```text
31,240
1,842
```

Token shorthand may be used as secondary display:

```text
31.2k
```

Exact value remains available in detail.

## 30.3 Time

Default activity timestamp:

```text
HH:MM:SSZ
```

Detail:

```text
YYYY-MM-DD HH:MM:SSZ
```

Use UTC consistently because durable timestamps are UTC.

Elapsed values may be calculated locally for display.

Clearly label locally ticking elapsed time as elapsed display, not provider heartbeat.

---

# 31. Literal text safety

All repository/model/user-generated strings are untrusted presentation data.

Examples:

- task objective;
- question body;
- blocker description;
- model summary;
- corrections;
- notes;
- file path;
- check command.

Render literally.

Do not allow them to inject:

- Rich/Textual markup;
- ANSI control codes;
- OSC links/titles;
- terminal control characters.

Prefer Textual/Rich APIs with markup disabled when available.

Keep the existing `safe_text` behavior as defense in depth.

Do not sanitize mechanical data before it reaches the UI contract; presentation escaping stays in the UI.

---

# 32. Focus behavior

## 32.1 Startup

Default focus:

```text
Overview primary content
```

not an invisible control.

## 32.2 Tables

Arrow keys move `DataTable` selection.

`Enter` opens detail/action.

## 32.3 Modals

When a modal opens:

- focus the first meaningful input;
- `Esc` cancels unless an operation has already been admitted;
- Tab cycles within modal controls;
- modal bindings take precedence over app navigation.

## 32.4 Forms and live refresh

Snapshot updates must not steal focus from:

- `Input`;
- `TextArea`;
- `Select`;
- focused Button.

Do not remount the whole screen merely to update status.

---

# 33. Help

`?` opens a simple modal.

Sections:

```text
Navigation
Run control
Current screen
```

Show only bindings that actually exist.

Do not make Help a substitute for good labels.

---

# 34. Implementation organization

Recommended, not mandatory:

```text
src/taskledger/ui/
    app.py
    state.py
    formatting.py
    widgets.py
    dialogs.py
    taskledger.tcss

    views/
        overview.py
        tasks.py
        agents.py
        activity.py
        usage.py
        interventions.py
        preparation.py
        report.py
```

Keep mechanics out.

Do not create:

```text
ui/repositories.py
ui/services.py
ui/controller.py
```

The application contract already exists.

---

# 35. Iterative implementation checkpoints

The UI should be built and tested iteratively.

Do not attempt all presentation polish before the first usable build.

## Checkpoint A — live read-only console

Implement:

1. external TCSS + semantic theme;
2. custom header;
3. six-tab shell;
4. exactly one snapshot observer;
5. Overview;
6. Tasks summary table;
7. Agents summary table;
8. basic Recent Activity;
9. Usage summary;
10. Interventions summary;
11. global Pause;
12. safe Exit;
13. wide + standard layouts;
14. Pilot tests for live updates, navigation, and stable selection.

No complex forms yet.

### A is successful when

A real deterministic fake-runtime Task Ledger run can be watched live and the operator can visually understand:

- run state;
- current task states;
- active model roles;
- recent controller activity;
- known usage;
- whether intervention is required.

At this checkpoint, Pause and safe Exit must work.

Then run the UI manually and collect usability feedback before proceeding.

## Checkpoint B — detail navigation

Add:

- wide task master/detail;
- standard/compact task full screen;
- task detail race protection;
- task paging;
- session detail;
- Activity History mode;
- run report;
- resize modes.

Test navigation heavily.

## Checkpoint C — lifecycle operations

Add:

- Prepare screen;
- preparation review;
- Approve & Start;
- Resume;
- operation banner/status tracking;
- question answer;
- blocker resolution;
- budget grant;
- guarded exit modal.

## Checkpoint D — polish and acceptance

Add/refine:

- compact layout;
- below-minimum state;
- focus preservation;
- unsent-input preservation;
- error/stale states;
- literal-text adversarial tests;
- keyboard/mouse parity;
- final Textual Pilot acceptance matrix.

---

# 36. Textual-specific implementation guidance

## 36.1 DataTable stable keys

Use durable IDs as `DataTable` row keys.

This allows selection to remain attached to the same entity even when rows move or are updated.

## 36.2 Replaceable detail workers

Use Textual workers only for replaceable reads:

```text
task_detail
preparation_detail
run_report
history page load
```

`exclusive=True` is appropriate when a newer task/detail request makes the prior result irrelevant.

Still retain an explicit request-generation check.

## 36.3 Mechanical operations

Do not use a cancel-propagating Textual worker as the owner of:

```text
prepare
start
resume
```

A UI coroutine may await the short admission receipt.

Once `ACCEPTED`, engine ownership is independent.

## 36.4 ModalScreen

Use `ModalScreen` for confirmations and bounded forms whose input should temporarily take precedence over the underlying screen.

## 36.5 Headless Textual testing

Use:

```python
async with app.run_test(size=(width, height)) as pilot:
    ...
```

Interact with:

- key presses;
- clicks;
- resize;
- focus;
- pushed screens;
- table selection.

Do not use screenshots as the primary proof of behavior.

---

# 37. Acceptance matrix

The UI is not complete until the following T01–T24 cases pass.

## T01 — Optional dependency boundary

With Textual absent:

- non-UI CLI imports and works;
- invoking `taskledger ui` returns the existing clear optional-dependency error.

With Textual installed:

- UI starts.

## T02 — UI/mechanics import boundary

Automated import/static test proves screens/widgets do not import:

- `sqlite3`;
- `Service`;
- `Journal`;
- `ProjectController`;
- `Supervisor`;
- provider runtime;
- Git helpers.

## T03 — Single feed subscription

Open app and switch through every tab repeatedly.

Assert:

```text
open_feed attach count == 1
```

No tab mounts a second observer.

## T04 — Snapshot ordering

Feed revisions:

```text
10
12
11
13
```

UI renders:

```text
10
12
13
```

and ignores 11.

A new epoch resets the baseline.

## T05 — Overview semantics

Fixture includes:

- incomplete requirements;
- completed tasks;
- active sessions;
- usage;
- intervention.

Assert displayed counts match application values exactly.

Task completion must not substitute for requirement completion.

## T06 — Uninitialized repository

Opening UI:

- does not create `.taskledger`;
- shows Uninitialized state;
- exposes Prepare;
- no model call occurs.

## T07 — Task stable selection

Select task B.

Publish updates that reorder/change task A.

Assert B remains selected by durable ID.

## T08 — Stale task-detail rejection

Request task A detail.

Select B before A returns.

Return B then A.

Assert B remains displayed.

## T09 — Task paging honesty

Fixture contains >1,000 tasks.

Assert:

- truncation message shown;
- next page loads;
- local filter says loaded versus total row count;
- no claim of project-wide filtering before all rows are loaded.

## T10 — Agent semantics

Fixture has:

- active session with running turn;
- active retained session with completed turn;
- closed session;
- uncertain session.

Assert four states are visibly distinct.

## T11 — Recent activity deduplication

Repeat the same snapshot activity records across many revisions.

Assert each `(source, sequence)` renders once.

## T12 — Activity follow behavior

While following:

- new event remains visible.

After user scrolls away:

- new event does not move viewport;
- newer count increases.

Re-enable follow:

- viewport returns to newest.

## T13 — History cursor behavior

Load multiple history pages containing overlapping numeric sequence values from audit and controller sources.

Assert:

- source identity preserved;
- no cross-source dedupe collision;
- cursor advances correctly.

## T14 — Usage accounting semantics

Test:

- complete usage;
- unresolved active allocation;
- missing usage;
- grants;
- overshoot.

Assert UI never labels an incomplete known subtotal as a definitive total.

## T15 — Preparation exact review

Load preparation detail.

Assert visible:

- exact ID;
- exact proposal hash;
- branch;
- starting commit;
- requirements;
- tasks;
- execution policy;
- assumptions;
- attempts.

No editable proposal controls.

## T16 — Preparation stale while open

Open preparation review.

Publish snapshot where preparation ID/hash/state changes.

Assert:

- start disabled;
- stale warning visible;
- old hash never replaced silently for submission.

## T17 — Prepare form

Pilot fills fields and submits.

Assert:

- local invalid numeric/JSON input prevents submit;
- live consent required;
- accepted operation uses one request ID;
- rejected operation preserves form values;
- app itself creates model work only through the headless prepare call.

## T18 — Approve/start and resume

Using fake headless client:

- confirm exact start;
- receive ACCEPTED;
- navigate Overview;
- show operation state;
- reach prepared fixture outcome;
- resume through explicit live consent.

No automatic start after preparation.

## T19 — Pause truthfulness

Request pause.

Assert immediately:

```text
Pause requested
```

Do not show `PAUSED` until authoritative snapshot says PAUSED.

If completion arrives instead, show COMPLETED.

## T20 — Human-intervention forms

Test:

- question answer;
- blocker resolution;
- token budget grant.

Assert payload IDs and values are exact.

Resolving/answering does not invoke Resume.

## T21 — Operation-wait cancellation

Admit lifecycle operation.

Navigate away or dismiss waiting presentation.

Assert:

- mechanical operation remains active;
- later snapshot/status recovers operation state;
- no duplicate mutation submitted.

## T22 — Guarded exit and fatal-display cleanup

Test:

- idle exit;
- active lifecycle Pause and Exit;
- Keep Running cancels exit;
- injected display exception still reaches composition-root host close;
- terminal/host cleanup follows the headless contract.

## T23 — Responsive / focus / input / mouse

Use Pilot at least at:

```text
140 x 40
100 x 30
80 x 24
70 x 22
55 x 18
```

Assert:

- correct layout class;
- selection retained after resize;
- form text retained;
- minimum warning below minimum size;
- keyboard navigation works;
- representative button/row mouse clicks work;
- live snapshot does not steal focus.

## T24 — Full visual workflow equivalence

Use deterministic accepted fake-runtime mechanics.

Drive through Textual:

```text
prepare
review
approve/start
observe worker
observe reviewer
pause
intervene
resume
complete
view task detail
view activity
view usage/report
exit
```

Compare durable/mechanical outcome to the accepted non-Textual public-contract fixture.

Enabling the UI must not add model turns, change routing, or change review/integration decisions.

---

# 38. Manual usability checks

Automated tests establish correctness, not whether the console is pleasant.

At each implementation checkpoint, manually verify the following.

## A. Scan test

Within roughly five seconds of opening Overview, can the operator answer:

- Is it running?
- What phase?
- Is human action required?
- Are agents active?
- How much usage is known?
- How far through requirements?

If not, simplify.

## B. Task investigation

From Tasks:

- find a non-completed task;
- understand why it is not complete;
- inspect criteria/check/review/integration evidence.

Target: no need to use raw CLI for normal investigation.

## C. Pause confidence

Start a fake run and press Pause.

The interface should make it obvious that:

- the request was received;
- Task Ledger may still be finishing a safe boundary;
- it is not yet durably paused.

## D. Failure clarity

Inject:

- stale preparation;
- invalid budget;
- uncertain turn;
- observation degradation.

The operator should understand what happened without a traceback.

## E. Resize

Resize repeatedly while:

- task selected;
- task detail loading;
- Prepare form contains edits;
- modal is open.

No state loss.

---

# 39. Performance expectations

The headless projection is already designed to be fast.

The Textual layer must not become the bottleneck.

At the accepted representative fixture:

```text
~1,000 task summaries
20 sessions
200 recent events
```

target:

- keyboard action visual response under 100 ms when not waiting on an engine query;
- snapshot rendering under 100 ms p95 on the reference development machine;
- no unbounded UI queue;
- no repeated database queries while idle;
- no full task-detail loads unless selected;
- no full run report unless requested.

Measure actual sluggishness before optimizing.

Do not prematurely build custom virtualization beyond Textual's existing table/scroll support.

---

# 40. Explicitly not in v1

Do not add the following without a new product decision:

- browser UI;
- remote access;
- multi-client support;
- authentication UI;
- task editing;
- requirement editing;
- task creation;
- manual reviewer verdicts;
- manual integration controls;
- arbitrary Git actions;
- provider transcript streaming;
- provider reasoning display;
- raw token-event stream;
- model/profile changes during a run;
- configuration persistence separate from existing Task Ledger configuration;
- custom visual themes;
- charts requiring historical aggregation not supplied by the application;
- cost/dollar estimates;
- notifications outside the terminal;
- detach-and-continue-on-exit.

---

# 41. Definition of done

The Textual UI is ready to merge when:

1. all six sections are functional;
2. one feed subscription drives live state;
3. no UI component owns mechanical execution;
4. Overview is accurate and useful;
5. task detail is inspectable;
6. Activity Recent and History modes work;
7. Usage and accounting uncertainty are truthful;
8. preparation can be created, reviewed, and exactly approved;
9. pause/resume behavior is truthful;
10. question/blocker/budget interventions work;
11. guarded exit is safe;
12. wide, standard, compact, and below-minimum layouts preserve state;
13. keyboard and representative mouse paths work;
14. literal untrusted text cannot execute terminal markup/control behavior;
15. T01–T24 pass;
16. existing non-live Task Ledger regression still passes;
17. enabling the UI does not add or alter model dispatches;
18. `IMPLEMENTATION_STATUS.md` is updated with actual UI acceptance evidence.

---

# 42. Recommended first implementation pass

Do **not** begin with every form.

Implement Checkpoint A first:

```text
1. external TCSS + semantic theme
2. custom header
3. tab shell
4. single snapshot observer
5. Overview
6. Tasks summary table
7. Agents summary table
8. Recent Activity
9. Usage summary
10. Interventions summary
11. global Pause
12. safe Exit
13. wide + standard layouts
14. Pilot tests for live updates / navigation / stable selection
```

Then run the UI against the deterministic fake-runtime controller fixture and inspect it manually.

The likely design questions after that pass will be much easier to answer from a working console:

- Is Overview too dense or not dense enough?
- Are six tabs the right grouping?
- Does task state need stronger visual hierarchy?
- Is the header using the right information?
- Is Activity useful enough to keep prominent?
- Do Agents and Activity deserve separate tabs?
- Are Usage details too technical for the default screen?
- Which information belongs beside the Tasks table versus inside detail?

Those are appropriate iterative UI decisions.

They should be answered from actual use rather than expanding the mechanical architecture again.

---

# 43. Framework rationale

The selected Textual primitives fit the required interaction model:

- `DataTable` stable row keys fit Task Ledger's durable-ID selection model.
- `ModalScreen` fits bounded confirmations and interventions whose input must take precedence over the underlying screen.
- Textual workers with exclusive replaceable work fit detail reads where stale results should be cancelled.
- `App.run_test()` and `Pilot` fit keyboard, mouse, focus, resize, and viewport acceptance tests.
- Textual semantic theme variables let the UI express primary/success/warning/error states without baking a bespoke palette into v1.

These framework facilities are presentation tools only. None changes the ownership rules in this specification.
