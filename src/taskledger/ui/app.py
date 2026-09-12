from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static, TabbedContent, TabPane

from .formatting import optional, safe_text, short_id
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
                yield OverviewView("Loading…", id="overview-body", classes="pane")
            with TabPane("Tasks", id="tasks"):
                yield Static("", id="tasks-note", classes="section-note")
                yield TasksTable(id="tasks-table")
            with TabPane("Agents", id="agents"):
                yield AgentsTable(id="agents-table")
            with TabPane("Activity", id="activity"):
                yield ActivityTable(id="activity-table")
            with TabPane("Usage", id="usage"):
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
            table.show_tasks(snapshot.tasks, selected)
            task_ids = {str(task.get("id")) for task in snapshot.tasks}
            if selected in task_ids:
                self.selected_task_id = selected
            elif snapshot.tasks:
                self.selected_task_id = str(snapshot.tasks[0].get("id"))
            shown, total = len(snapshot.tasks), snapshot.project.get("task_count", len(snapshot.tasks))
            note = f"Showing {shown:,} of {int(total):,} tasks" if snapshot.project.get("tasks_truncated") else ""
            self.query_one("#tasks-note", Static).update(note)
            self._slices["tasks"] = task_slice
        run_id = snapshot.run.get("id") if snapshot.run else None
        session_slice = (snapshot.sessions, run_id)
        if self._slices.get("sessions") != session_slice:
            self.query_one(AgentsTable).show_sessions(snapshot.sessions, run_id)
            self._slices["sessions"] = session_slice
        if self._slices.get("activity") != snapshot.activity:
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
        widget = self.query_one("#global-banner", Static)
        widget.update(banner)
        widget.set_class(bool(banner), "visible")

    def on_data_table_row_highlighted(self, event) -> None:
        if event.data_table.id == "tasks-table":
            self.selected_task_id = str(event.row_key.value)

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
