from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import tempfile
from pathlib import Path
from typing import Any

from taskledger.core import LedgerError, canonical


def broker_socket_path(home: Path, assignment_id: str) -> Path:
    """Return a deterministic path below macOS's short AF_UNIX path limit."""
    digest = hashlib.sha256(f"{home.resolve()}\0{assignment_id}".encode()).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"taskledger-{digest}" / "worker.sock"


class WorkerBroker:
    """Expose only assignment scoped worker service calls over a local socket."""

    def __init__(self, service, principal, socket_path: Path):
        self.service = service
        self.principal = principal
        self.socket_path = socket_path
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.socket_path.unlink(missing_ok=True)
        try:
            self.socket_path.parent.rmdir()
        except OSError:
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {"action", "data"} or not isinstance(request["data"], dict):
                raise LedgerError("INVALID_REQUEST", "Worker broker request is invalid.")
            result = await asyncio.to_thread(self.dispatch, request["action"], request["data"])
            response = {"ok": True, "data": result}
        except LedgerError as exc:
            response = {"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        except Exception as exc:
            response = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Worker broker request failed.", "details": {"reason": exc.__class__.__name__}}}
        writer.write((canonical(response) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _dispatch(self, action: str, data: dict[str, Any], *, service=None) -> dict[str, Any]:
        service = service or self.service
        project, assignment = service.worker_assignment(self.principal)
        methods = {
            "context": lambda: service.worker_context(self.principal, data),
            "check": lambda: service.worker_check(self.principal, data),
            "checkpoint": lambda: service.worker_checkpoint(self.principal, data),
            "artifact-register": lambda: service.register_artifact(project, self.principal, data, worker=True),
            "question": lambda: service.worker_question(self.principal, data),
            "blocker": lambda: service.blocker_create(project, self.principal, data, worker=True),
            "follow-up": lambda: service.worker_followup(self.principal, data),
            "submit": lambda: service.worker_submit(self.principal, data),
        }
        if action not in methods:
            raise LedgerError("AUTHORIZATION_DENIED", "Worker broker action is not allowed.")
        if hasattr(service, "con") and "task_id" in assignment.keys():
            task = service.con.execute("SELECT state FROM tasks WHERE id=?", (assignment["task_id"],)).fetchone()
            if task and task["state"] == "SUBMITTED":
                raise LedgerError(
                    "ASSIGNMENT_ALREADY_SUBMITTED",
                    "This assignment already has a durable submission. Stop immediately; do not run another command or worker operation.",
                    details={"terminal": True, "retryable": False, "allowed_actions": []},
                )
        if action == "submit":
            self._validate_submit(data)
        elif action == "checkpoint":
            self._validate_checkpoint(data)
            progress = service.checkpoint_progress(assignment)
            if progress.get("pending"):
                raise LedgerError(
                    "CHECKPOINT_ALREADY_PENDING", "A checkpoint is already awaiting review. Stop this turn.",
                    details={"terminal": True, "retryable": False, "allowed_actions": []},
                )
            if not progress.get("next"):
                raise LedgerError(
                    "CHECKPOINT_NOT_AVAILABLE",
                    "No checkpoint is available for this assignment. Use the exact required checks and submit when complete.",
                    details={"terminal": False, "retryable": False, "allowed_actions": ["check", "submit"]},
                )
        result = methods[action]()
        if action == "submit" and result.get("state") in {"PENDING", "BLOCKED"}:
            return {
                **result,
                "_terminal": "SUBMITTED",
                "instruction": "Submission recorded durably. Stop immediately; do not run another command or worker operation.",
            }
        if action == "check":
            return {
                **result,
                "evidence_commit_oid": result.get("source_revision"),
                "worktree_clean_after_evidence_commit_is_expected": True,
                "instruction": "Use this receipt_id in submission evidence. Do not rerun an already successful current check.",
            }
        return result

    @staticmethod
    def _fields(value: Any, path: str, *, allowed: set[str], required: set[str]) -> None:
        if not isinstance(value, dict):
            raise LedgerError(
                "INVALID_REQUEST", f"{path} must be an object.",
                details={"field_path": path, "expected_fields": sorted(required), "retryable": True},
            )
        missing, extra = sorted(required - set(value)), sorted(set(value) - allowed)
        if missing or extra:
            field = f"{path}.{missing[0]}" if missing else f"{path}.{extra[0]}"
            raise LedgerError(
                "INVALID_REQUEST", f"Invalid worker tool payload at {field}.",
                details={
                    "field_path": field, "missing_fields": missing, "unexpected_fields": extra,
                    "expected_fields": sorted(required), "retryable": True,
                    "instruction": "Correct only the named fields and retry once.",
                },
            )

    @classmethod
    def _validate_checkpoint(cls, data: dict[str, Any]) -> None:
        cls._fields(data, "checkpoint", allowed={"summary", "evidence"}, required={"summary", "evidence"})
        if isinstance(data.get("evidence"), list):
            for index, item in enumerate(data["evidence"]):
                cls._fields(
                    item, f"checkpoint.evidence[{index}]",
                    allowed={"label", "details", "receipt_id"}, required={"label", "details"},
                )

    @classmethod
    def _validate_submit(cls, data: dict[str, Any]) -> None:
        cls._fields(
            data, "submit",
            allowed={"summary", "evidence", "risks", "unresolved_questions", "follow_up_work"},
            required={"summary", "evidence", "risks", "unresolved_questions", "follow_up_work"},
        )
        collections = (
            ("evidence", {"label", "details", "command", "exit_code", "artifact_path", "receipt_id", "artifact_id"}, {"label", "details"}),
            ("risks", {"description", "blocking", "blocker_category"}, {"description", "blocking", "blocker_category"}),
            ("unresolved_questions", {"body", "blocking", "blocker_category"}, {"body", "blocking", "blocker_category"}),
            ("follow_up_work", {"body"}, {"body"}),
        )
        for name, allowed, required in collections:
            values = data.get(name)
            if not isinstance(values, list):
                raise LedgerError(
                    "INVALID_REQUEST", f"submit.{name} must be an array.",
                    details={"field_path": f"submit.{name}", "retryable": True},
                )
            for index, item in enumerate(values):
                cls._fields(item, f"submit.{name}[{index}]", allowed=allowed, required=required)

    def dispatch(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(self.service, "home") or not hasattr(self.service, "con"):
            return self._dispatch(action, data)
        from taskledger.db import connect
        from taskledger.notifications import bind_change_sink, change_sink_for, unbind_change_sink
        from taskledger.service import Service

        isolated = Service(connect(self.service.home), self.service.home)
        sink = change_sink_for(self.service.con)
        try:
            if sink is not None:
                bind_change_sink(isolated.con, sink)
            return self._dispatch(action, data, service=isolated)
        finally:
            if sink is not None:
                unbind_change_sink(isolated.con)
            isolated.con.close()


def request(socket_path: str, action: str, data: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(socket_path)
        client.sendall((canonical({"action": action, "data": data}) + "\n").encode())
        chunks = bytearray()
        while not chunks.endswith(b"\n"):
            part = client.recv(65536)
            if not part:
                break
            chunks.extend(part)
    return json.loads(chunks)
