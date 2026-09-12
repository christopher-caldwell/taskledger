from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static, TabbedContent, TabPane

from taskledger.application.contracts import HistoryCursor

from .formatting import optional, safe_text, short_id
from .screens import (
    BudgetDialog, ConfirmDialog, MessageDialog, PreparationReviewScreen, PrepareScreen,
    RunReportScreen, TaskDetailScreen, TextMutationDialog, receipt_error, render_value, request_id,
)
from .widgets import ActivityTable, AgentsTable, ConsoleHeader, InterventionsTable, OverviewView, TasksTable, UsageView


class ExitDialog(ModalScreen[bool]):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("[b]Task Ledger is still executing.[/b]", classes="dialog-title")
            yield Static("Exiting will request a safe pause and wait for Task Ledger to reach a safe boundary before closing.")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Keep Running", id="keep-running")
                yield Button("Pause and Exit", id="pause-exit", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "pause-exit")


class HelpDialog(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog help-dialog"):
            yield Static("[b]Task Ledger keys[/b]", classes="dialog-title")
            yield Static("F1–F6  Switch section\nCtrl+P  Request pause\nR       Refresh snapshot\n?       Help\nCtrl+Q  Exit")
            yield Button("Close", id="close-help", variant="primary")

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)


class TaskledgerApp(App):
    CSS_PATH = "taskledger.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("f1", "tab('overview')", "Overview"),
        ("f2", "tab('tasks')", "Tasks"),
        ("f3", "tab('agents')", "Agents"),
        ("f4", "tab('activity')", "Activity"),
        ("f5", "tab('usage')", "Usage"),
        ("f6", "tab('interventions')", "Interventions"),
        ("ctrl+p", "pause", "Pause"),
        ("r", "refresh", "Refresh"),
        ("question_mark", "help", "Help"),
        ("ctrl+q", "quit_guarded", "Exit"),
    ]

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.reader = None
        self.snapshot = None
        self._observer: asyncio.Task | None = None
        self._slices: dict[str, object] = {}
        self.selected_task_id: str | None = None
        self.pause_requested_lifecycle: str | None = None
        self.observation_error: str | None = None
        self.engine_closing = False
        self.layout_mode = "standard"
        self._loaded_tasks = ()
        self._task_total = 0
        self._detail_generation = 0
        self._history_cursor = HistoryCursor()
        self._history_records = ()
        self.activity_mode = "recent"
        self._tracked_operations: dict[str, str] = {}
        self._visible_operation_id: str | None = None

    def compose(self) -> ComposeResult:
        yield ConsoleHeader("Task Ledger\nLoading snapshot…", id="console-header")
        yield Static("", id="global-banner")
        yield Static(
            "Task Ledger\n\nTerminal is too small for the full console.\n"
            "Minimum recommended size: 64 x 20.\n\nCtrl+P Pause    Ctrl+Q Exit",
            id="minimum",
        )
        with TabbedContent(id="sections"):
            with TabPane("Overview", id="overview"):
                with Horizontal(classes="action-bar"):
                    yield Button("Prepare", id="prepare-overview")
                    yield Button("Review preparation", id="review-overview")
                    yield Button("Resume", id="resume-overview")
                yield OverviewView("Loading…", id="overview-body", classes="pane")
            with TabPane("Tasks", id="tasks"):
                with Horizontal(classes="action-bar"):
                    yield Static("", id="tasks-note", classes="section-note")
                    yield Button("Load 200 more", id="load-more-tasks")
                with Horizontal(id="tasks-master-detail"):
                    yield TasksTable(id="tasks-table")
                    yield Static("Select a task to inspect it.", id="task-inline-detail", markup=False)
            with TabPane("Agents", id="agents"):
                yield AgentsTable(id="agents-table")
            with TabPane("Activity", id="activity"):
                with Horizontal(classes="action-bar"):
                    yield Button("History", id="activity-mode")
                    yield Button("Load more", id="load-more-activity", disabled=True)
                yield ActivityTable(id="activity-table")
            with TabPane("Usage", id="usage"):
                with Horizontal(classes="action-bar"):
                    yield Button("Open run report", id="report-usage")
                    yield Button("Extend budget", id="budget-usage")
                yield UsageView("Loading…", id="usage-body", classes="pane")
            with TabPane("Interventions", id="interventions"):
                yield Static("", id="interventions-empty", classes="section-note")
                yield InterventionsTable(id="interventions-table")
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = "textual-dark"
        self._set_layout_mode(self.size.width, self.size.height)
        self.reader = self.client.open_feed()
        self._observer = asyncio.create_task(self._observe())

    async def _observe(self) -> None:
        try:
            while True:
                self._accept_snapshot(await self.reader.receive())
        except (StopAsyncIteration, asyncio.CancelledError):
            pass
        except Exception as exc:
            self.observation_error = safe_text(exc)
            self._update_banner()

    def _accept_snapshot(self, snapshot) -> bool:
        if self.snapshot and snapshot.version.engine_epoch == self.snapshot.version.engine_epoch and snapshot.version.revision <= self.snapshot.version.revision:
            return False
        self.snapshot = snapshot
        self.observation_error = None
        if snapshot.run is None or snapshot.run.get("state") != "RUNNING":
            self.pause_requested_lifecycle = None
        self._render(snapshot)
        self._update_open_screen(snapshot)
        self._reconcile_operations(snapshot)
        return True

    # Retained as a small compatibility seam for existing adapters/tests.
    def _render(self, snapshot) -> None:
        if snapshot is not self.snapshot:
            self._accept_snapshot(snapshot)
            return
        header_slice = (snapshot.project, snapshot.run, snapshot.usage)
        if self._slices.get("header") != header_slice:
            self.query_one(ConsoleHeader).show_snapshot(snapshot)
            self._slices["header"] = header_slice
        overview_slice = (snapshot.project, snapshot.preparation, snapshot.run, snapshot.usage, snapshot.interventions, snapshot.activity[:3], snapshot.operation)
        if self._slices.get("overview") != overview_slice:
            self.query_one(OverviewView).show_snapshot(snapshot)
            self._slices["overview"] = overview_slice
        task_slice = (
            snapshot.tasks,
            snapshot.project.get("task_count"),
            snapshot.project.get("tasks_truncated"),
        )
        if self._slices.get("tasks") != task_slice:
            table = self.query_one(TasksTable)
            selected = self.selected_task_id or table.selected_key()
            previous = {str(item.get("id")): item for item in self._loaded_tasks}
            for item in snapshot.tasks: previous[str(item.get("id"))] = item
            # A complete snapshot replaces locally paged state; a truncated one updates it.
            self._loaded_tasks = tuple(previous.values()) if snapshot.project.get("tasks_truncated") else snapshot.tasks
            self._task_total = int(snapshot.project.get("task_count", len(self._loaded_tasks)))
            table.show_tasks(self._loaded_tasks, selected)
            task_ids = {str(task.get("id")) for task in self._loaded_tasks}
            if selected in task_ids:
                self.selected_task_id = selected
            elif snapshot.tasks:
                self.selected_task_id = str(snapshot.tasks[0].get("id"))
            shown, total = len(self._loaded_tasks), self._task_total
            note = f"Showing {shown:,} of {total:,} tasks" if shown < total else ""
            self.query_one("#tasks-note", Static).update(note)
            self.query_one("#load-more-tasks", Button).disabled = shown >= total
            self._slices["tasks"] = task_slice
        run_id = snapshot.run.get("id") if snapshot.run else None
        session_slice = (snapshot.sessions, run_id)
        if self._slices.get("sessions") != session_slice:
            self.query_one(AgentsTable).show_sessions(snapshot.sessions, run_id)
            self._slices["sessions"] = session_slice
        if self.activity_mode == "recent" and self._slices.get("activity") != snapshot.activity:
            self.query_one(ActivityTable).show_activity(snapshot.activity)
            self._slices["activity"] = snapshot.activity
        usage_slice = (snapshot.usage, snapshot.sessions)
        if self._slices.get("usage") != usage_slice:
            self.query_one(UsageView).show_usage(snapshot.usage, snapshot.sessions)
            self._slices["usage"] = usage_slice
        if self._slices.get("interventions") != snapshot.interventions:
            self.query_one(InterventionsTable).show_interventions(snapshot.interventions)
            self.query_one("#interventions-empty", Static).update("" if snapshot.interventions else "No human intervention required.")
            self._slices["interventions"] = snapshot.interventions
        self._update_banner()
        self._update_actions()

    def _available(self, code: str) -> bool:
        if not self.snapshot: return False
        if code == "PREPARE" and self.snapshot.project.get("effective_phase") == "UNINITIALIZED":
            return True
        for action in self.snapshot.project.get("available_actions", ()):
            values = dict(action) if isinstance(action, tuple) else action
            if values.get("code") == code: return bool(values.get("enabled"))
        return False

    def _update_actions(self) -> None:
        if not self.is_mounted or not self.snapshot: return
        prep = self.snapshot.preparation
        run = self.snapshot.run
        self.query_one("#prepare-overview", Button).disabled = not self._available("PREPARE")
        self.query_one("#review-overview", Button).disabled = not bool(prep and prep.get("state") == "AWAITING_APPROVAL")
        self.query_one("#resume-overview", Button).disabled = not self._available("RESUME")
        self.query_one("#report-usage", Button).disabled = run is None
        self.query_one("#budget-usage", Button).disabled = run is None

    def _update_open_screen(self, snapshot) -> None:
        screen = self.screen
        if isinstance(screen, PreparationReviewScreen): screen.update_snapshot(snapshot)

    def track_operation(self, operation_id: str, label: str) -> None:
        self._tracked_operations[operation_id] = label
        self._visible_operation_id = operation_id
        self._update_banner()

    def _reconcile_operations(self, snapshot) -> None:
        visible = snapshot.operation.get("operation_id") if snapshot.operation else None
        if visible: self._visible_operation_id = str(visible)
        for operation_id in tuple(self._tracked_operations):
            if operation_id != visible:
                asyncio.create_task(self._resolve_operation(operation_id))
                self._tracked_operations.pop(operation_id, None)

    async def _resolve_operation(self, operation_id: str) -> None:
        try:
            status = await self.client.operation_status(operation_id)
            phase = status.phase.replace("_", " ").title()
            if status.phase == "FAILED":
                message = getattr(status.error, "message", None) or "Operation failed."
                self.notify(f"{phase}: {safe_text(message)}", severity="error", timeout=8)
            elif status.phase == "UNKNOWN_OPERATION":
                self.notify("Operation outcome is no longer retained; durable project state is authoritative.", timeout=8)
            else: self.notify(f"Operation {phase.lower()}.")
        except Exception as exc: self.notify(f"Unable to read operation outcome: {safe_text(exc)}", severity="warning")

    def _update_banner(self) -> None:
        if not self.is_mounted:
            return
        banner = ""
        if self.observation_error or (self.snapshot and self.snapshot.project.get("cache_health") == "DEGRADED"):
            detail = self.observation_error or self.snapshot.project.get("observation_error", "unknown error")
            banner = f"OBSERVATION DEGRADED — showing last confirmed snapshot; press R to refresh ({safe_text(detail)})"
        elif self.engine_closing:
            banner = "WAITING — Task Ledger is reaching a safe shutdown point…"
        elif self.pause_requested_lifecycle or (self.snapshot and self.snapshot.run and self.snapshot.run.get("pause_requested")):
            banner = "PAUSE REQUESTED — waiting for the current safe boundary"
        elif self.snapshot and self.snapshot.operation:
            operation = self.snapshot.operation
            banner = f"{optional(operation.get('phase'))} — operation {short_id(operation.get('operation_id'))}"
        elif self._visible_operation_id and self._visible_operation_id in self._tracked_operations:
            banner = f"ADMITTED — {self._tracked_operations[self._visible_operation_id]}"
        widget = self.query_one("#global-banner", Static)
        widget.update(banner)
        widget.set_class(bool(banner), "visible")

    def on_data_table_row_highlighted(self, event) -> None:
        if event.data_table.id == "tasks-table":
            self.selected_task_id = str(event.row_key.value)
            if self.layout_mode == "wide": self._load_inline_detail(self.selected_task_id)

    def on_data_table_row_selected(self, event) -> None:
        identity = str(event.row_key.value)
        if event.data_table.id == "tasks-table":
            if self.layout_mode == "wide": self._load_inline_detail(identity)
            else: self.push_screen(TaskDetailScreen(self.client, identity))
        elif event.data_table.id == "interventions-table": self._open_intervention(identity)

    def _load_inline_detail(self, task_id: str) -> None:
        self._detail_generation += 1
        generation = self._detail_generation
        asyncio.create_task(self._finish_inline_detail(task_id, generation))

    async def _finish_inline_detail(self, task_id: str, generation: int) -> None:
        detail = self.query_one("#task-inline-detail", Static)
        detail.update("Loading…")
        try: value = await self.client.task_detail(task_id)
        except Exception as exc: value = f"Unable to load task: {safe_text(exc)}"
        if generation == self._detail_generation and task_id == self.selected_task_id:
            detail.update(render_value(value))

    def on_resize(self, event: Resize) -> None:
        self._set_layout_mode(event.size.width, event.size.height)

    def _set_layout_mode(self, width: int, height: int) -> None:
        if width < 64 or height < 20:
            mode = "below-minimum"
        elif width < 80 or height < 24:
            mode = "compact"
        elif width >= 120 and height >= 30:
            mode = "wide"
        else:
            mode = "standard"
        if mode != self.layout_mode or not self.has_class(mode):
            self.remove_class("wide", "standard", "compact", "below-minimum")
            self.add_class(mode)
            self.layout_mode = mode

    def action_tab(self, tab: str) -> None:
        if self.layout_mode != "below-minimum":
            self.query_one("#sections", TabbedContent).active = tab

    async def action_refresh(self) -> None:
        try:
            snapshot = await self.client.refresh_snapshot()
            if snapshot is not None:
                self._accept_snapshot(snapshot)
        except Exception as exc:
            self.observation_error = safe_text(exc)
            self._update_banner()

    def _pause_target(self) -> str | None:
        if self.snapshot and self.snapshot.operation:
            lifecycle_id = self.snapshot.operation.get("lifecycle_id")
            if lifecycle_id:
                return str(lifecycle_id)
        if self.snapshot and self.snapshot.run:
            run = self.snapshot.run
            if run.get("state") == "RUNNING" and run.get("owned_by_host"):
                return str(run.get("id"))
        return None

    async def action_pause(self) -> None:
        target = self._pause_target()
        if not target:
            self.notify("No running lifecycle is owned by this console.")
            return
        try:
            receipt = await self.client.request_pause(target)
            if receipt.status in {"REQUESTED", "ALREADY_REQUESTED"}:
                self.pause_requested_lifecycle = target
                self._update_banner()
            elif receipt.status == "NOT_RUNNING":
                self.notify("Lifecycle is no longer running.")
                await self.action_refresh()
        except Exception as exc:
            self.notify(f"Pause failed: {safe_text(exc)}", severity="error")

    async def action_quit_guarded(self) -> None:
        if self._pause_target():
            self.push_screen(ExitDialog(), self._finish_guarded_exit)
        else:
            self.exit()

    async def _finish_guarded_exit(self, should_exit: bool | None) -> None:
        if not should_exit:
            return
        await self.action_pause()
        self.engine_closing = True
        self._update_banner()
        self.exit()

    def action_help(self) -> None:
        self.push_screen(HelpDialog())

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "prepare-overview": self.push_screen(PrepareScreen(self.client))
        elif button_id == "review-overview": self.open_preparation_review()
        elif button_id == "resume-overview": self.open_resume()
        elif button_id == "load-more-tasks": await self.load_more_tasks()
        elif button_id == "activity-mode": await self.toggle_activity_mode()
        elif button_id == "load-more-activity": await self.load_history()
        elif button_id == "report-usage" and self.snapshot and self.snapshot.run:
            self.push_screen(RunReportScreen(self.client, str(self.snapshot.run.get("id"))))
        elif button_id == "budget-usage" and self.snapshot and self.snapshot.run:
            self.push_screen(BudgetDialog(self.client, str(self.snapshot.run.get("id"))))

    async def load_more_tasks(self) -> None:
        try:
            page = await self.client.task_page(len(self._loaded_tasks), 200)
            existing = {str(item.get("id")): item for item in self._loaded_tasks}
            for item in page.records: existing[str(item.get("id"))] = item
            self._loaded_tasks = tuple(existing.values()); self._task_total = page.total
            self.query_one(TasksTable).show_tasks(self._loaded_tasks, self.selected_task_id)
            self.query_one("#tasks-note", Static).update(f"Showing {len(self._loaded_tasks):,} of {page.total:,} tasks")
            self.query_one("#load-more-tasks", Button).disabled = page.next_offset is None
        except Exception as exc: self.notify(f"Unable to load tasks: {safe_text(exc)}", severity="error")

    async def toggle_activity_mode(self) -> None:
        if self.activity_mode == "recent":
            self.activity_mode = "history"; self._history_cursor = HistoryCursor(); self._history_records = ()
            self.query_one("#activity-mode", Button).label = "Recent"
            self.query_one("#load-more-activity", Button).disabled = False
            await self.load_history()
        else:
            self.activity_mode = "recent"; self.query_one("#activity-mode", Button).label = "History"
            self.query_one("#load-more-activity", Button).disabled = True
            if self.snapshot: self.query_one(ActivityTable).show_activity(self.snapshot.activity)

    async def load_history(self) -> None:
        if self.activity_mode != "history": return
        try:
            previous_cursor = self._history_cursor
            page = await self.client.history_page(self._history_cursor, 200)
            seen = {(item.get("source"), item.get("sequence")) for item in self._history_records}
            self._history_records += tuple(item for item in page.records if (item.get("source"), item.get("sequence")) not in seen)
            self._history_cursor = page.cursor
            self.query_one(ActivityTable).show_activity(self._history_records)
            self.query_one("#load-more-activity", Button).disabled = not page.records or page.cursor == previous_cursor
        except Exception as exc: self.notify(f"Unable to load history: {safe_text(exc)}", severity="error")

    def open_preparation_review(self) -> None:
        if self.snapshot and self.snapshot.preparation:
            self.push_screen(PreparationReviewScreen(self.client, str(self.snapshot.preparation.get("id")), self.snapshot))

    def open_resume(self) -> None:
        if not self.snapshot or not self.snapshot.run: return
        run = self.snapshot.run
        body = (f"Run: {safe_text(run.get('id'))}\nState: {safe_text(run.get('state'))}\n"
                f"Pause reason: {safe_text(run.get('pause_reason'))}\nPause detail: {safe_text(run.get('pause_detail'))}")
        self.push_screen(ConfirmDialog("Resume run", body, "Resume"), self._finish_resume)

    async def _finish_resume(self, confirmed: bool | None) -> None:
        if not confirmed or not self.snapshot or not self.snapshot.run: return
        receipt = await self.client.resume_run(request_id("resume"), run_id=self.snapshot.run.get("id"), live=True)
        if receipt.disposition == "REJECTED": self.push_screen(MessageDialog("Resume rejected", receipt_error(receipt))); return
        self.track_operation(receipt.operation_id, "Resuming"); self.action_tab("overview")

    def _open_intervention(self, identity: str) -> None:
        if not self.snapshot: return
        item = next((value for value in self.snapshot.interventions if str(value.get("id")) == identity), None)
        if not item: return
        kind = item.get("kind")
        if kind in {"QUESTION", "BLOCKER"}: self.push_screen(TextMutationDialog(self.client, item))
        elif kind == "PREPARATION_APPROVAL": self.open_preparation_review()
        elif kind == "UNCERTAIN_TURN": self.push_screen(MessageDialog("Recovery required", safe_text(item.get("description", ""))))

    async def on_unmount(self) -> None:
        if self.reader:
            self.reader.close()
        if self._observer:
            self._observer.cancel()
            await asyncio.gather(self._observer, return_exceptions=True)


async def run_ui(client):
    app = TaskledgerApp(client)
    try:
        await app.run_async()
    finally:
        await client.close()
