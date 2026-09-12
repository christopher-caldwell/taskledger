from __future__ import annotations

from textual.widgets import DataTable, Static

from taskledger.application.contracts import ConsoleSnapshot, Record

from .formatting import (
    integer,
    known_tokens,
    nested,
    optional,
    repository_name,
    safe_text,
    session_activity,
    short_id,
    task_activity,
    token_count,
)


class ConsoleHeader(Static):
    def show_snapshot(self, snapshot: ConsoleSnapshot) -> None:
        project, run = snapshot.project, snapshot.run
        run_state = run.get("state", "NO RUN") if run else "NO RUN"
        if run and run_state == "RUNNING" and not run.get("owned_by_host"):
            run_state = "RECORDED RUNNING"
        line_one = "  ".join((
            "Task Ledger", repository_name(project.get("repository_root")),
            optional(project.get("canonical_branch"), "No branch"), safe_text(run_state),
        ))
        requirements = nested(project.get("progress"), "requirements", default={})
        tasks = nested(project.get("progress"), "tasks", default={})
        tokens = known_tokens(snapshot.usage)
        ownership = "owned by this console" if run and run.get("owned_by_host") else "not locally owned"
        line_two = "  ".join((
            optional(project.get("effective_phase"), "UNKNOWN"),
            f"run {short_id(run.get('id'))}" if run else "no run",
            f"requirements {integer(requirements.get('complete'))}/{integer(requirements.get('total'))}",
            f"tasks {integer(tasks.get('completed'))}/{integer(project.get('task_count'))}",
            f"{token_count(tokens)} known tokens" if tokens is not None else "usage unknown",
            ownership,
        ))
        self.update(f"{line_one}\n{line_two}")


class OverviewView(Static):
    def show_snapshot(self, snapshot: ConsoleSnapshot) -> None:
        project, run = snapshot.project, snapshot.run
        if project.get("effective_phase") == "UNINITIALIZED":
            self.update(
                "[b]Task Ledger is not initialized for this repository.[/b]\n\n"
                f"Repository\n{safe_text(project.get('repository_root', ''))}\n\n"
                "Preparing will initialize Task Ledger and start a bounded planning operation."
            )
            return
        requirements = nested(project.get("progress"), "requirements", default={})
        tasks = nested(project.get("progress"), "tasks", default={})
        tokens = known_tokens(snapshot.usage)
        accounting = snapshot.usage.get("accounting_quality", snapshot.usage.get("accounting_status", "UNKNOWN"))
        run_label = run.get("state", "No run") if run else "No run"
        lines = [
            f"[b]Lifecycle[/b]  {safe_text(run_label)}  •  {optional(project.get('effective_phase'))}",
            f"Run {short_id(run.get('id')) if run else '—'}  •  "
            f"{'owned by this console' if run and run.get('owned_by_host') else 'not locally owned'}",
            "",
            f"[b]Requirements[/b]  {integer(requirements.get('complete'))} / {integer(requirements.get('total'))} complete"
            f"  •  {integer(requirements.get('blocked', 0))} blocked"
            f"  •  {integer(requirements.get('awaiting_final_verification', 0))} awaiting verification",
            f"[b]Tasks[/b]  {integer(tasks.get('completed'))} / {integer(project.get('task_count'))} completed"
            f"  •  {integer(tasks.get('active_implementation', 0))} implementing"
            f"  •  {integer(tasks.get('awaiting_submission_verification', 0))} awaiting review",
            f"[b]Usage[/b]  {token_count(tokens)} known tokens  •  accounting {safe_text(accounting)}",
            "",
            f"[b]Needs attention[/b]  {len(snapshot.interventions) if snapshot.interventions else 'None'}",
        ]
        for item in snapshot.interventions[:3]:
            lines.append(
                f"{safe_text(item.get('kind'))}  {short_id(item.get('scope_id'))}  "
                f"{safe_text(item.get('description', ''))}"
            )
        if snapshot.activity:
            lines.extend(("", "[b]Recent activity[/b]"))
            for event in snapshot.activity[:3]:
                lines.append(
                    f"{optional(event.get('created_at'))}  {safe_text(event.get('event_type'))}  "
                    f"{short_id(event.get('entity_id'))}"
                )
        self.update("\n".join(lines))


class KeyedTable(DataTable):
    column_keys: tuple[str, ...] = ()

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True

    def selected_key(self) -> str | None:
        if not self.row_count:
            return None
        try:
            return str(self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value)
        except Exception:
            return None

    def replace_rows(self, rows: list[tuple[str, tuple[str, ...]]], selected: str | None = None) -> None:
        desired = {key: cells for key, cells in rows}
        existing = {str(key.value) for key in self.rows}
        for key in existing - desired.keys():
            self.remove_row(key)
        for key, cells in rows:
            if key not in existing:
                self.add_row(*cells, key=key)
                continue
            for column, value in zip(self.column_keys, cells):
                self.update_cell(key, column, value)
        if selected and selected in desired:
            self.move_cursor(row=self.get_row_index(selected), animate=False)


class TasksTable(KeyedTable):
    column_keys = ("state", "activity", "wave", "profile", "deps", "blocks", "objective")

    def on_mount(self) -> None:
        super().on_mount()
        for label, key in zip(("State", "Activity", "Wave", "Profile", "Deps", "Blocks", "Objective"), self.column_keys):
            self.add_column(label, key=key)

    def show_tasks(self, tasks: tuple[Record, ...], selected: str | None = None) -> None:
        rows = []
        for task in tasks:
            rows.append((str(task.get("id")), (
                optional(task.get("state")), task_activity(task), optional(task.get("wave")),
                optional(task.get("worker_profile")), integer(task.get("dependency_count", 0)),
                integer(task.get("blocker_count", 0)), safe_text(task.get("objective", "")),
            )))
        self.replace_rows(rows, selected)


class AgentsTable(KeyedTable):
    column_keys = ("role", "state", "turn", "turns", "profile", "model", "effort", "subject")

    def on_mount(self) -> None:
        super().on_mount()
        for label, key in zip(("Role", "State", "Turn", "Turns", "Profile", "Model", "Effort", "Subject"), self.column_keys):
            self.add_column(label, key=key)

    def show_sessions(self, sessions: tuple[Record, ...], run_id: str | None) -> None:
        current = tuple(item for item in sessions if not run_id or item.get("run_id") == run_id)
        order = {"Active turn": 0, "Retained": 1, "Uncertain": 2, "Closed": 3}
        ordered = sorted(current, key=lambda item: (
            order.get(session_activity(item), 4), str(item.get("created_at", "")),
        ))
        rows = []
        for item in ordered:
            rows.append((str(item.get("id")), (
                optional(item.get("role")), session_activity(item), optional(item.get("latest_turn_state"), "Idle"),
                integer(item.get("turn_count", 0)), optional(item.get("profile")), optional(item.get("model")),
                optional(item.get("effort")), short_id(item.get("subject_id")),
            )))
        self.clear()
        for key, cells in rows:
            self.add_row(*cells, key=key)


class ActivityTable(KeyedTable):
    column_keys = ("time", "source", "event", "entity", "id")

    def on_mount(self) -> None:
        super().on_mount()
        for label, key in zip(("Time", "Source", "Event", "Entity", "ID"), self.column_keys):
            self.add_column(label, key=key)

    def show_activity(self, activity: tuple[Record, ...]) -> None:
        seen: set[tuple[object, object]] = set()
        rows = []
        for item in activity:
            identity = (item.get("source"), item.get("sequence"))
            if identity in seen:
                continue
            seen.add(identity)
            rows.append((f"{identity[0]}:{identity[1]}", (
                optional(item.get("created_at")), optional(item.get("source")), optional(item.get("event_type")),
                optional(item.get("entity_type")), short_id(item.get("entity_id")),
            )))
        self.clear()
        for key, cells in rows:
            self.add_row(*cells, key=key)


class InterventionsTable(KeyedTable):
    column_keys = ("kind", "scope", "created", "description")

    def on_mount(self) -> None:
        super().on_mount()
        for label, key in zip(("Kind", "Scope", "Created", "Description"), self.column_keys):
            self.add_column(label, key=key)

    def show_interventions(self, interventions: tuple[Record, ...]) -> None:
        rows = []
        for item in interventions:
            rows.append((str(item.get("id")), (
                optional(item.get("kind")), short_id(item.get("scope_id")), optional(item.get("created_at")),
                safe_text(item.get("description", "")),
            )))
        self.replace_rows(rows)


class UsageView(Static):
    def show_usage(self, usage: Record, sessions: tuple[Record, ...]) -> None:
        known = known_tokens(usage)
        accounting = usage.get("accounting_quality", usage.get("accounting_status", "UNKNOWN"))
        input_tokens = usage.get("input_tokens")
        cached_input_tokens = usage.get("cached_input_tokens")
        non_cached_input_tokens = None
        if (
            isinstance(input_tokens, (int, float))
            and not isinstance(input_tokens, bool)
            and isinstance(cached_input_tokens, (int, float))
            and not isinstance(cached_input_tokens, bool)
        ):
            non_cached_input_tokens = input_tokens - cached_input_tokens
        fields = (
            ("Known tokens", integer(known)), ("Accounting status", optional(accounting)),
            ("Terminal turns", integer(usage.get("terminal_turns"))),
            ("Completed turns", integer(usage.get("completed_turns"))),
            ("Missing-usage turns", integer(usage.get("missing_usage_turns"))),
            ("Unresolved turns", integer(usage.get("unresolved_turn_count"))),
            ("Input", integer(input_tokens)),
            ("Cached input", integer(cached_input_tokens)),
            ("Non-cached input", integer(non_cached_input_tokens)),
            ("Output", integer(usage.get("output_tokens"))),
            ("Reasoning", integer(usage.get("reasoning_tokens"))),
        )
        text = "\n".join(f"[b]{label}[/b]  {value}" for label, value in fields)
        if known is not None and str(accounting).upper() not in {"COMPLETE", "EXACT"}:
            text = f"[b]{integer(known)} known tokens[/b] — accounting is incomplete\n\n{text}"
        self.update(text)
