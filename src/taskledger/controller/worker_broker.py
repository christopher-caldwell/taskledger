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
        project, _ = service.worker_assignment(self.principal)
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
        return methods[action]()

    def dispatch(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(self.service, "home") or not hasattr(self.service, "con"):
            return self._dispatch(action, data)
        from taskledger.db import connect
        from taskledger.service import Service

        isolated = Service(connect(self.service.home), self.service.home)
        try:
            return self._dispatch(action, data, service=isolated)
        finally:
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
