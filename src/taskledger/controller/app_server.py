from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Callable

from .model import (
    RuntimeSession,
    RuntimeTurnHandle,
    RuntimeTurnInspection,
    RuntimeTurnResult,
    SessionRole,
    Usage,
)


class AppServerRuntime:
    """Async JSON RPC client for the local Codex app server stdio transport."""

    def __init__(self, *, repository_root: str, codex_executable: str | None = None, worker_sockets: dict[str, str] | None = None, worker_tools: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] | None = None):
        self.repository_root = Path(repository_root)
        self.codex_executable = codex_executable or shutil.which("codex")
        if not self.codex_executable:
            raise RuntimeError("Codex executable is not available")
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.next_request_id = 1
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.turn_waiters: dict[str, asyncio.Future[RuntimeTurnResult]] = {}
        self.turn_threads: dict[str, str] = {}
        self.turn_text: dict[str, str] = {}
        self.thread_usage: dict[str, Usage] = {}
        self.session_config: dict[str, dict[str, Any]] = {}
        self.worker_sockets = worker_sockets or {}
        self.worker_tools = worker_tools or {}

    async def start_session(self, *, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool) -> RuntimeSession:
        await self._ensure_started()
        resolved = self._profile(profile, role)
        sandbox: dict[str, Any]
        if writable:
            writable_roots = self._worker_writable_roots(cwd)
            if subject_id in self.worker_sockets:
                writable_roots.append(str(Path(self.worker_sockets[subject_id]).parent))
            sandbox = {
                "type": "workspaceWrite",
                "writableRoots": writable_roots,
                "networkAccess": False,
            }
        else:
            sandbox = {"type": "readOnly", "networkAccess": False}
        response = await self._request("thread/start", {
            "model": resolved["model"],
            "cwd": cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": sandbox,
            "serviceName": "taskledger_controller",
        })
        thread = response["thread"]
        thread_id = thread["id"]
        self.session_config[thread_id] = {**resolved, "cwd": cwd, "sandboxPolicy": sandbox, "subject_id": subject_id, "writable": writable}
        return RuntimeSession(thread_id)

    async def resume_session(self, *, thread_id: str, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool) -> RuntimeSession:
        await self._ensure_started()
        if thread_id not in self.session_config:
            resolved = self._profile(profile, role)
            if writable:
                writable_roots = self._worker_writable_roots(cwd)
                if subject_id in self.worker_sockets:
                    writable_roots.append(str(Path(self.worker_sockets[subject_id]).parent))
                sandbox = {"type": "workspaceWrite", "writableRoots": writable_roots, "networkAccess": False}
            else:
                sandbox = {"type": "readOnly", "networkAccess": False}
            self.session_config[thread_id] = {**resolved, "cwd": cwd, "sandboxPolicy": sandbox, "subject_id": subject_id, "writable": writable}
        params: dict[str, Any] = {"threadId": thread_id}
        config = self.session_config.get(thread_id)
        if config:
            params.update({"model": config["model"], "cwd": cwd, "sandboxPolicy": config["sandboxPolicy"]})
        response = await self._request("thread/resume", params)
        if response["thread"]["id"] != thread_id:
            raise RuntimeError("Codex resumed a different thread")
        return RuntimeSession(thread_id)

    async def start_turn(self, *, thread_id: str, prompt: str, output_schema: dict[str, Any] | None = None) -> RuntimeTurnHandle:
        await self._ensure_started()
        params: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]}
        config = self.session_config.get(thread_id)
        if config:
            params.update({"model": config["model"], "effort": config["effort"], "cwd": config["cwd"], "sandboxPolicy": config["sandboxPolicy"]})
            if config.get("writable") and config.get("subject_id") in self.worker_tools:
                params["dynamicTools"] = self._worker_tool_specs()
        if output_schema is not None:
            params["outputSchema"] = output_schema
        response = await self._request("turn/start", params)
        turn_id = response["turn"]["id"]
        self.turn_threads[turn_id] = thread_id
        self.turn_waiters.setdefault(turn_id, asyncio.get_running_loop().create_future())
        return RuntimeTurnHandle(thread_id, turn_id)

    async def wait_turn(self, handle: RuntimeTurnHandle) -> RuntimeTurnResult:
        waiter = self.turn_waiters.setdefault(handle.turn_id, asyncio.get_running_loop().create_future())
        return await waiter

    async def inspect_turn(self, handle: RuntimeTurnHandle) -> RuntimeTurnInspection:
        await self._ensure_started()
        try:
            response = await self._request("thread/read", {"threadId": handle.thread_id, "includeTurns": True})
        except Exception as exc:
            return RuntimeTurnInspection("UNKNOWN", error=str(exc))
        for turn in response.get("thread", {}).get("turns", []):
            if turn.get("id") != handle.turn_id:
                continue
            status = turn.get("status")
            if status in {"inProgress", "running"}:
                return RuntimeTurnInspection("RUNNING")
            if status in {"completed", "interrupted"}:
                return RuntimeTurnInspection("COMPLETED", self._turn_result(handle, turn))
            if status in {"failed", "error"}:
                return RuntimeTurnInspection("FAILED", error=self._error_text(turn.get("error")))
            return RuntimeTurnInspection("UNKNOWN", error=f"unknown Codex turn status {status}")
        return RuntimeTurnInspection("UNKNOWN", error="Codex thread did not contain the recorded turn")

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader_task:
            self.reader_task.cancel()
        if self.stderr_task:
            self.stderr_task.cancel()

    async def _ensure_started(self) -> None:
        if self.process and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            self.codex_executable, "app-server", "--stdio", "--strict-config",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.reader_task = asyncio.create_task(self._read_messages())
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await self._request("initialize", {"clientInfo": {"name": "taskledger_controller", "title": "Taskledger Controller", "version": "0.1.0"}})
        await self._notify("initialized", {})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Codex app server is not running")
        request_id = self.next_request_id
        self.next_request_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.process.stdin.write((json.dumps({"method": method, "id": request_id, "params": params}, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()
        return await future

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Codex app server is not running")
        self.process.stdin.write((json.dumps({"method": method, "params": params}, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()

    async def _read_messages(self) -> None:
        assert self.process and self.process.stdout
        while line := await self.process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message and "method" in message:
                if message["method"] == "item/tool/call":
                    await self._handle_dynamic_tool_call(message)
                else:
                    await self._deny_server_request(message)
                continue
            if "id" in message and (future := self.pending.pop(message["id"], None)) is not None:
                if "error" in message:
                    future.set_exception(RuntimeError(self._error_text(message["error"])))
                else:
                    future.set_result(message.get("result", {}))
                continue
            method, params = message.get("method"), message.get("params", {})
            if method == "item/completed":
                item = params.get("item", {})
                turn_id = params.get("turnId")
                if turn_id and item.get("type") in {"agentMessage", "agent_message"}:
                    self.turn_text[turn_id] = item.get("text", "")
            elif method == "thread/tokenUsage/updated":
                thread_id = params.get("threadId")
                if thread_id:
                    self.thread_usage[thread_id] = self._usage(params.get("tokenUsage") or params.get("usage") or {})
            elif method == "turn/completed":
                turn = params.get("turn", {})
                turn_id = turn.get("id") or params.get("turnId")
                if turn_id:
                    thread_id = params.get("threadId") or self.turn_threads.get(turn_id, "")
                    handle = RuntimeTurnHandle(thread_id, turn_id)
                    waiter = self.turn_waiters.setdefault(turn_id, asyncio.get_running_loop().create_future())
                    if not waiter.done():
                        if turn.get("status") in {"failed", "error"}:
                            waiter.set_exception(RuntimeError(self._error_text(turn.get("error"))))
                        else:
                            waiter.set_result(self._turn_result(handle, turn))
            elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/tool/requestUserInput"}:
                turn_id = params.get("turnId")
                if turn_id and (waiter := self.turn_waiters.get(turn_id)) and not waiter.done():
                    waiter.set_exception(RuntimeError(f"Codex requested unattended interaction: {method}"))
        error = RuntimeError("Codex app server connection closed")
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(error)

    async def _deny_server_request(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            return
        response = {
            "id": message["id"],
            "error": {"code": -32001, "message": f"Taskledger controller does not approve unattended request {message.get('method')}"},
        }
        self.process.stdin.write((json.dumps(response, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()

    async def _handle_dynamic_tool_call(self, message: dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        params = message.get("params", {})
        config = self.session_config.get(params.get("threadId"), {})
        handler = self.worker_tools.get(config.get("subject_id"))
        tool = params.get("tool", "")
        action = tool.removeprefix("taskledger_").replace("_", "-")
        try:
            if handler is None or not tool.startswith("taskledger_"):
                raise RuntimeError("dynamic worker tool is not authorized for this thread")
            result = handler(action, params.get("arguments") or {})
            payload = {"contentItems": [{"type": "inputText", "text": json.dumps(result, separators=(",", ":"))}], "success": True}
        except Exception as exc:
            payload = {"contentItems": [{"type": "inputText", "text": str(exc)}], "success": False}
        self.process.stdin.write((json.dumps({"id": message["id"], "result": payload}, separators=(",", ":")) + "\n").encode())
        await self.process.stdin.drain()

    @staticmethod
    def _worker_tool_specs() -> list[dict[str, Any]]:
        descriptions = {
            "context": "Load current assignment-scoped Taskledger context.",
            "check": "Run and record an assignment-scoped check.",
            "checkpoint": "Record a durable assignment checkpoint.",
            "artifact-register": "Register assignment evidence by relative path.",
            "question": "Ask an assignment-scoped worker question.",
            "blocker": "Create an assignment-scoped blocker.",
            "follow-up": "Propose follow-up work.",
            "submit": "Submit the completed assignment for independent review.",
        }
        return [{"type": "function", "name": "taskledger_" + action.replace("-", "_"), "description": description, "inputSchema": {"type": "object", "additionalProperties": True}} for action, description in descriptions.items()]

    @staticmethod
    def _worker_writable_roots(cwd: str | None) -> list[str]:
        if not cwd:
            raise RuntimeError("writable worker session requires a cwd")
        roots = [str(Path(cwd).resolve())]
        try:
            git_dir = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-dir"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            common_dir = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("worker cwd is not a usable Git worktree") from exc
        common = Path(common_dir)
        roots.extend([
            str(Path(git_dir)),
            str(common / "objects"),
            str(common / "refs" / "heads" / "taskledger"),
            str(common / "logs" / "refs" / "heads" / "taskledger"),
        ])
        return list(dict.fromkeys(roots))

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        while await self.process.stderr.readline():
            pass

    def _turn_result(self, handle: RuntimeTurnHandle, turn: dict[str, Any]) -> RuntimeTurnResult:
        text = self.turn_text.get(handle.turn_id)
        if text is None:
            for item in reversed(turn.get("items", [])):
                if item.get("type") in {"agentMessage", "agent_message"}:
                    text = item.get("text")
                    break
        structured = None
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    structured = parsed
            except json.JSONDecodeError:
                pass
        usage = self._usage(turn.get("usage", {}))
        if usage == Usage():
            usage = self.thread_usage.get(handle.thread_id, Usage())
        return RuntimeTurnResult(handle, structured, text, usage)

    @staticmethod
    def _usage(value: dict[str, Any]) -> Usage:
        last = value.get("last") or value.get("lastUsage") or value
        details = last.get("inputTokensDetails") or last.get("input_tokens_details") or {}
        output_details = last.get("outputTokensDetails") or last.get("output_tokens_details") or {}
        return Usage(
            int(last.get("inputTokens", last.get("input_tokens", 0)) or 0),
            int(last.get("cachedInputTokens", details.get("cached_tokens", 0)) or 0),
            int(last.get("outputTokens", last.get("output_tokens", 0)) or 0),
            int(last.get("reasoningTokens", output_details.get("reasoning_tokens", 0)) or 0),
        )

    def _profile(self, profile: str, role: SessionRole) -> dict[str, str]:
        filenames = []
        if role == SessionRole.REVIEWER:
            filenames.append("taskledger-reviewer.toml")
        else:
            filenames.append(f"taskledger-worker-{profile}.toml")
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        roots = [
            self.repository_root / ".codex" / "agents",
            codex_home / "agents",
            codex_home / "skills" / "taskledger" / "assets",
            Path(__file__).parents[3] / "skills" / "taskledger" / "assets",
        ]
        for root in roots:
            for filename in filenames:
                path = root / filename
                if path.is_file():
                    data = tomllib.loads(path.read_text())
                    model, effort = data.get("model"), data.get("model_reasoning_effort")
                    if isinstance(model, str) and isinstance(effort, str):
                        return {"model": model, "effort": effort}
        raise RuntimeError(f"Codex profile configuration is missing for {profile}")

    @staticmethod
    def _error_text(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("message") or value.get("code") or value)
        return str(value or "unknown Codex error")
