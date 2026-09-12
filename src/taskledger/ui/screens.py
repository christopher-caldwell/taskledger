from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from taskledger.application.contracts import Record

from .formatting import safe_text


def request_id(prefix: str) -> str:
    return f"ui-{prefix}-{uuid.uuid4()}"


def receipt_error(receipt) -> str:
    error = getattr(receipt, "error", None)
    return safe_text(getattr(error, "message", None) or getattr(receipt, "disposition", "Request rejected"))


def plain(value):
    if isinstance(value, Record):
        return {key: plain(item) for key, item in value.fields}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        # Projected mappings are tuples of key/value pairs.
        if all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value):
            return {key: plain(item) for key, item in value}
        return [plain(item) for item in value]
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def render_value(value, indent: int = 0) -> str:
    value = plain(value)
    if isinstance(value, dict):
        if not value:
            return "—"
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                nested = render_value(item, indent + 2)
                lines.append(f"{' ' * indent}{safe_text(key)}:\n{nested}")
            else:
                lines.append(f"{' ' * indent}{safe_text(key)}: {safe_text(item) if item is not None else '—'}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "—"
        lines = []
        for item in value:
            rendered = render_value(item, indent + 2)
            lines.append(f"{' ' * indent}• {rendered.lstrip()}")
        return "\n".join(lines)
    return safe_text(value) if value is not None else "—"


class MessageDialog(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, title: str, body: str):
        super().__init__()
        self.title, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.title, classes="dialog-title")
            yield Static(self.body)
            yield Button("Close", id="close", variant="primary")

    def action_dismiss(self) -> None: self.dismiss(None)
    def on_button_pressed(self, _event: Button.Pressed) -> None: self.dismiss(None)


class RecordScreen(Screen):
    BINDINGS = [("escape", "close", "Back"), ("r", "refresh", "Refresh")]
    screen_title = "Detail"

    def __init__(self, client, identity: str):
        super().__init__()
        self.client, self.identity = client, identity

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-screen"):
            with Horizontal(classes="screen-toolbar"):
                yield Label(self.screen_title, classes="screen-title")
                yield Button("Refresh", id="refresh-detail")
                yield Button("Back", id="close-detail")
            with VerticalScroll():
                yield Static("Loading…", id="detail-content", markup=False)

    async def fetch(self): raise NotImplementedError

    async def on_mount(self) -> None: await self.action_refresh()

    async def action_refresh(self) -> None:
        try:
            value = await self.fetch()
            self.query_one("#detail-content", Static).update(render_value(value))
        except Exception as exc:
            self.query_one("#detail-content", Static).update(f"Unable to load detail: {safe_text(exc)}")

    def action_close(self) -> None: self.app.pop_screen()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-detail": self.action_close()
        elif event.button.id == "refresh-detail": await self.action_refresh()


class TaskDetailScreen(RecordScreen):
    screen_title = "Task detail"
    async def fetch(self): return await self.client.task_detail(self.identity)


class RunReportScreen(RecordScreen):
    screen_title = "Run report"
    async def fetch(self): return await self.client.run_report(self.identity)


LIMIT_DEFAULTS = {
    "max_initial_planner_turns": 2,
    "max_total_worker_turns": 100,
    "max_total_reviewer_turns": 50,
    "max_total_task_creator_turns": 6,
    "max_worker_turns_per_assignment": 20,
    "max_reviewer_turns_per_submission": 2,
    "max_final_reviewer_turns": 2,
    "max_total_tokens": 40000,
    "turn_timeout_seconds": 1800,
    "max_elapsed_seconds": 3600,
    "max_workers": 2,
    "max_reviewers": 1,
    "max_inflight_targets": "",
    "reviewer_token_reserve": 0,
}


class PrepareScreen(Screen):
    BINDINGS = [("escape", "close", "Cancel")]

    def __init__(self, client):
        super().__init__(); self.client = client

    def compose(self) -> ComposeResult:
        with Vertical(id="form-screen"):
            with Horizontal(classes="screen-toolbar"):
                yield Label("Prepare project", classes="screen-title")
                yield Button("Cancel", id="cancel")
            with VerticalScroll(id="form-scroll"):
                yield Label("Specification path")
                yield Input(id="spec-path", placeholder="path/to/spec.md")
                yield Checkbox("I consent to live provider execution", id="prepare-live")
                yield Label("Execution limits", classes="form-heading")
                for name, value in LIMIT_DEFAULTS.items():
                    yield Label(name)
                    yield Input(str(value), id=f"limit-{name}", type="integer")
                yield Label("Advanced preflight", classes="form-heading")
                yield Label("local_inputs (JSON array)")
                yield TextArea("[]", id="local-inputs")
                yield Label("services (JSON array)")
                yield TextArea("[]", id="services")
                yield Label("Optional run group ID")
                yield Input(id="run-group-id")
                yield Static("", id="form-error", classes="form-error", markup=False)
                yield Button("Prepare", id="submit-prepare", variant="primary")

    def action_close(self): self.app.pop_screen()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel": return self.action_close()
        if event.button.id != "submit-prepare": return
        error = self.query_one("#form-error", Static)
        try:
            spec_path = self.query_one("#spec-path", Input).value.strip()
            if not spec_path: raise ValueError("Specification path is required.")
            if not self.query_one("#prepare-live", Checkbox).value: raise ValueError("Live consent is required.")
            limits = {}
            for name in LIMIT_DEFAULTS:
                raw = self.query_one(f"#limit-{name}", Input).value
                if name == "max_inflight_targets" and not raw.strip():
                    continue
                value = int(raw)
                if value <= 0 and name != "reviewer_token_reserve": raise ValueError(f"{name} must be positive.")
                if value < 0: raise ValueError(f"{name} cannot be negative.")
                limits[name] = value
            if limits["reviewer_token_reserve"] > limits["max_total_tokens"]:
                raise ValueError("reviewer_token_reserve cannot exceed max_total_tokens.")
            local_inputs = json.loads(self.query_one("#local-inputs", TextArea).text)
            services = json.loads(self.query_one("#services", TextArea).text)
            if not isinstance(local_inputs, list) or not isinstance(services, list):
                raise ValueError("local_inputs and services must be JSON arrays.")
            payload = {"spec_path": spec_path, "live": True, "limits": limits,
                       "preflight": {"local_inputs": local_inputs, "services": services}}
            run_group = self.query_one("#run-group-id", Input).value.strip()
            if run_group: payload["run_group_id"] = run_group
            receipt = await self.client.prepare_project(request_id("prepare"), **payload)
            if receipt.disposition == "REJECTED": raise ValueError(receipt_error(receipt))
            self.app.track_operation(receipt.operation_id, "Preparing")
            self.app.pop_screen(); self.app.action_tab("overview")
        except Exception as exc:
            error.update(safe_text(exc))


class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, confirm_label: str):
        super().__init__(); self.title, self.body, self.confirm_label = title, body, confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog confirm-dialog"):
            yield Static(self.title, classes="dialog-title")
            yield Static(self.body, markup=False)
            yield Checkbox("I consent to live provider execution", id="confirm-live")
            yield Static("", id="confirm-error", classes="form-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="primary")

    def action_cancel(self): self.dismiss(False)
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel": self.dismiss(False)
        elif event.button.id == "confirm":
            if not self.query_one("#confirm-live", Checkbox).value:
                self.query_one("#confirm-error", Static).update("Live consent is required.")
            else: self.dismiss(True)


class PreparationReviewScreen(Screen):
    BINDINGS = [("escape", "close", "Back")]

    def __init__(self, client, preparation_id: str, snapshot):
        super().__init__(); self.client, self.preparation_id, self.snapshot = client, preparation_id, snapshot
        self.detail = None; self.loaded_hash = None; self.stale = False

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-screen"):
            with Horizontal(classes="screen-toolbar"):
                yield Label("Preparation review", classes="screen-title")
                yield Button("Approve & Start", id="approve-start", variant="success")
                yield Button("Back", id="back")
            yield Static("", id="stale-warning", classes="form-error")
            with VerticalScroll(): yield Static("Loading…", id="preparation-content", markup=False)

    async def on_mount(self):
        try:
            self.detail = await self.client.preparation_detail(self.preparation_id)
            self.loaded_hash = self.detail.get("proposal_hash")
            self.query_one("#preparation-content", Static).update(render_value(self.detail))
            self.update_snapshot(self.snapshot)
        except Exception as exc: self.query_one("#preparation-content", Static).update(safe_text(exc))

    def update_snapshot(self, snapshot):
        self.snapshot = snapshot
        current = snapshot.preparation
        self.stale = not bool(current and current.get("id") == self.preparation_id and
                              current.get("state") == "AWAITING_APPROVAL" and
                              (self.loaded_hash is None or current.get("proposal_hash") == self.loaded_hash))
        self.query_one("#stale-warning", Static).update("This preparation is no longer current; approval is disabled." if self.stale else "")
        self.query_one("#approve-start", Button).disabled = self.stale or self.loaded_hash is None

    def action_close(self): self.app.pop_screen()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "back": return self.action_close()
        if event.button.id != "approve-start" or self.stale or not self.detail: return
        configuration = plain(self.detail.get("run_configuration_json"))
        execution = configuration.get("execution_limits", {}) if isinstance(configuration, dict) else {}
        supervisor = execution.get("supervisor", {}) if isinstance(execution, dict) else {}
        body = "\n".join((
            f"Branch: {safe_text(self.detail.get('canonical_branch'))}",
            f"Starting commit: {safe_text(self.detail.get('starting_oid'))}",
            f"Proposal hash: {safe_text(self.loaded_hash)}",
            f"Token budget: {safe_text(supervisor.get('max_total_tokens', '—'))}",
            f"Worker concurrency: {safe_text(execution.get('max_workers', '—'))}",
            f"Reviewer concurrency: {safe_text(execution.get('max_reviewers', '—'))}",
        ))
        self.app.push_screen(ConfirmDialog("Approve exact preparation", body, "Approve & Start"), self._confirmed)

    async def _confirmed(self, confirmed: bool | None):
        if not confirmed or self.stale: return
        receipt = await self.client.start_prepared_project(
            request_id("start"), preparation_id=self.preparation_id,
            approve_proposal_hash=self.loaded_hash, live=True,
        )
        if receipt.disposition == "REJECTED":
            self.app.push_screen(MessageDialog("Start rejected", receipt_error(receipt))); return
        self.app.track_operation(receipt.operation_id, "Starting")
        self.app.pop_screen(); self.app.action_tab("overview")


class TextMutationDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, client, item: Record):
        super().__init__(); self.client, self.item = client, item

    def compose(self) -> ComposeResult:
        kind = self.item.get("kind")
        with Vertical(classes="dialog intervention-dialog"):
            yield Static("Answer question" if kind == "QUESTION" else "Resolve blocker", classes="dialog-title")
            yield Static(safe_text(self.item.get("description", "")), markup=False)
            yield TextArea(id="intervention-text")
            yield Static("", id="intervention-error", classes="form-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Submit", id="submit", variant="primary")

    def action_cancel(self): self.dismiss(False)
    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel": return self.dismiss(False)
        if event.button.id != "submit": return
        value = self.query_one("#intervention-text", TextArea).text.strip()
        if not value:
            self.query_one("#intervention-error", Static).update("A response is required."); return
        if self.item.get("kind") == "QUESTION":
            receipt = await self.client.answer_question(request_id("answer"), question_id=self.item.get("id"), answer=value)
        else:
            receipt = await self.client.resolve_blocker(request_id("resolve"), blocker_id=self.item.get("id"), resolution=value)
        if receipt.disposition == "REJECTED":
            self.query_one("#intervention-error", Static).update(receipt_error(receipt)); return
        self.dismiss(True)


class BudgetDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    KINDS = ("WORKER_TURNS", "REVIEWER_TURNS", "TASK_CREATOR_TURNS", "TOKENS", "ELAPSED_SECONDS")

    def __init__(self, client, run_id: str): super().__init__(); self.client, self.run_id = client, run_id

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog budget-dialog"):
            yield Static("Extend budget", classes="dialog-title")
            yield Select(((kind.replace("_", " ").title(), kind) for kind in self.KINDS), id="budget-kind", value=self.KINDS[0])
            yield Input(id="budget-amount", type="integer", placeholder="Amount")
            yield Input(id="budget-reason", placeholder="Reason")
            yield Static("", id="budget-error", classes="form-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Grant", id="grant", variant="primary")

    def action_cancel(self): self.dismiss(False)
    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel": return self.dismiss(False)
        if event.button.id != "grant": return
        try:
            amount = int(self.query_one("#budget-amount", Input).value)
            reason = self.query_one("#budget-reason", Input).value.strip()
            if amount <= 0: raise ValueError("Amount must be positive.")
            if not reason: raise ValueError("Reason is required.")
            receipt = await self.client.extend_budget(request_id("budget"), run_id=self.run_id,
                kind=self.query_one("#budget-kind", Select).value, amount=amount, reason=reason)
            if receipt.disposition == "REJECTED": raise ValueError(receipt_error(receipt))
            self.dismiss(True)
        except Exception as exc: self.query_one("#budget-error", Static).update(safe_text(exc))
