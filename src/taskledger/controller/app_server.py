from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Callable

from .model import (
    RuntimeIdentity,
    RuntimeSession,
    RuntimeTurnHandle,
    RuntimeTurnInspection,
    RuntimeTurnResult,
    SessionRole,
    Usage,
)


class AppServerRuntime:
    """Async JSON RPC client for the local Codex app server stdio transport."""

    BASE_INSTRUCTIONS = (
        "You are a bounded Taskledger execution agent. Perform only the supplied worker, reviewer, "
        "or planning job. The Python controller owns scheduling, continuation, verification, and "
        "integration. Durable Taskledger state, not prose or turn completion, determines workflow state."
    )

    def __init__(self, *, repository_root: str, codex_executable: str | None = None, worker_sockets: dict[str, str] | None = None, worker_tools: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] | None = None):
        self.repository_root = Path(repository_root)
        self.codex_executable = codex_executable or shutil.which("codex")
        if not self.codex_executable:
            raise RuntimeError("Codex executable is not available")
        self.process: asyncio.subprocess.Process | None = None
        self.start_lock = asyncio.Lock()
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.server_tasks: set[asyncio.Task[None]] = set()
        self.next_request_id = 1
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.turn_waiters: dict[str, asyncio.Future[RuntimeTurnResult]] = {}
        self.turn_threads: dict[str, str] = {}
        self.turn_text: dict[str, str] = {}
        self.turn_usage: dict[str, Usage] = {}
        self.turn_usage_events: dict[str, list[dict[str, Any]]] = {}
        self.turn_usage_event_ids: dict[str, set[str]] = {}
        self.session_config: dict[str, dict[str, Any]] = {}
        self.worker_sockets = worker_sockets or {}
        self.worker_tools = worker_tools or {}
        try:
            version = subprocess.run(
                [self.codex_executable, "--version"], check=True, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Codex version identity is unavailable") from exc
        self.protocol_identity = f"{version};app-server-v2-jsonrpc+experimental-dynamic-tools;taskledger-client-0.4.0"

    def session_identity(self, *, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool) -> RuntimeIdentity:
        resolved = self._profile(profile, role)
        sandbox = self._sandbox(subject_id=subject_id, cwd=cwd, writable=writable)
        return RuntimeIdentity(
            model=resolved["model"],
            effort=resolved["effort"],
            agent_config_hash=resolved["config_hash"],
            sandbox=sandbox,
            protocol_identity=self.protocol_identity,
        )

    async def start_session(self, *, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool) -> RuntimeSession:
        await self._ensure_started()
        resolved = self._profile(profile, role)
        identity = self.session_identity(role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=writable)
        sandbox = identity.sandbox
        params = {
            "model": identity.model,
            "cwd": cwd,
            "approvalPolicy": "never",
            "sandbox": "workspace-write" if writable else "read-only",
            "serviceName": "taskledger_controller",
            "baseInstructions": self.BASE_INSTRUCTIONS,
        }
        if resolved.get("developer_instructions"):
            params["developerInstructions"] = resolved["developer_instructions"]
        if writable and subject_id in self.worker_tools:
            params["dynamicTools"] = self._worker_tool_specs()
        response = await self._request("thread/start", params)
        thread = response["thread"]
        thread_id = thread["id"]
        self.session_config[thread_id] = {
            **identity.as_dict(), "cwd": cwd, "sandboxPolicy": sandbox, "subject_id": subject_id,
            "writable": writable, "developerInstructions": resolved.get("developer_instructions"),
        }
        return RuntimeSession(thread_id, identity)

    async def resume_session(self, *, thread_id: str, role: SessionRole, profile: str, subject_id: str, cwd: str | None, writable: bool) -> RuntimeSession:
        await self._ensure_started()
        identity = self.session_identity(role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=writable)
        resolved = self._profile(profile, role)
        if thread_id not in self.session_config:
            self.session_config[thread_id] = {
                **identity.as_dict(), "cwd": cwd, "sandboxPolicy": identity.sandbox, "subject_id": subject_id,
                "writable": writable, "developerInstructions": resolved.get("developer_instructions"),
            }
        params: dict[str, Any] = {"threadId": thread_id}
        config = self.session_config.get(thread_id)
        if config:
            params.update({
                "model": config["model"], "cwd": cwd,
                "sandbox": "workspace-write" if writable else "read-only",
                "baseInstructions": self.BASE_INSTRUCTIONS,
            })
            if config.get("developerInstructions"):
                params["developerInstructions"] = config["developerInstructions"]
        response = await self._request("thread/resume", params)
        if response["thread"]["id"] != thread_id:
            raise RuntimeError("Codex resumed a different thread")
        return RuntimeSession(thread_id, identity)

    async def start_turn(self, *, thread_id: str, prompt: str, output_schema: dict[str, Any] | None = None) -> RuntimeTurnHandle:
        await self._ensure_started()
        params: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]}
        config = self.session_config.get(thread_id)
        if config:
            params.update({"model": config["model"], "effort": config["effort"], "cwd": config["cwd"], "sandboxPolicy": config["sandboxPolicy"]})
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

    def usage_events(self, handle: RuntimeTurnHandle) -> tuple[dict[str, Any], ...]:
        return tuple(self.turn_usage_events.get(handle.turn_id, ()))

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
        for task in self.server_tasks:
            task.cancel()
        tasks = [task for task in (self.reader_task, self.stderr_task) if task is not None] + list(self.server_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _ensure_started(self) -> None:
        if self.process and self.process.returncode is None:
            return
        async with self.start_lock:
            if self.process and self.process.returncode is None:
                return
            self.process = await asyncio.create_subprocess_exec(
                self.codex_executable, "app-server", "--stdio", "--strict-config",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            self.reader_task = asyncio.create_task(self._read_messages())
            self.stderr_task = asyncio.create_task(self._drain_stderr())
            await self._request("initialize", {
                "clientInfo": {"name": "taskledger_controller", "title": "Taskledger Controller", "version": "0.4.0"},
                "capabilities": {"experimentalApi": True},
            })
            await self._notify("initialized", {})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Codex app server is not running")
        if self.reader_task and self.reader_task.done():
            try:
                error = self.reader_task.exception()
            except asyncio.CancelledError:
                error = None
            raise RuntimeError(f"Codex app server response reader stopped: {error or 'connection closed'}")
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
        failure: BaseException | None = None
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" in message and "method" in message:
                    if message["method"] == "item/tool/call":
                        task = asyncio.create_task(self._handle_dynamic_tool_call(message))
                        self.server_tasks.add(task)
                        task.add_done_callback(self._server_task_done)
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
                    turn_id = params.get("turnId")
                    token_usage = params.get("tokenUsage") or params.get("usage") or {}
                    if turn_id:
                        usage = self._usage(token_usage)
                        raw = json.dumps(params, sort_keys=True, separators=(",", ":"))
                        event_id = hashlib.sha256(raw.encode()).hexdigest()
                        seen = self.turn_usage_event_ids.setdefault(turn_id, set())
                        if event_id in seen:
                            continue
                        seen.add(event_id)
                        self.turn_usage[turn_id] = self.turn_usage.get(turn_id, Usage()) + usage
                        self.turn_usage_events.setdefault(turn_id, []).append({
                            "event_id": event_id,
                            "usage": usage.__dict__,
                            "raw": params,
                        })
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
        except BaseException as exc:
            failure = exc
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self._fail_pending(RuntimeError(
                f"Codex app server connection closed{': ' + str(failure) if failure else ''}"
            ))

    def _server_task_done(self, task: asyncio.Task[None]) -> None:
        self.server_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._fail_pending(RuntimeError(f"Codex app server tool response failed: {error}"))

    def _fail_pending(self, error: RuntimeError) -> None:
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(error)
        for future in list(self.turn_waiters.values()):
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
            result = await asyncio.to_thread(handler, action, params.get("arguments") or {})
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
        text = {"type": "string", "minLength": 1}
        nullable_text = {"type": ["string", "null"]}
        evidence = {
            "type": "object",
            "properties": {
                "label": text,
                "details": text,
                "command": nullable_text,
                "exit_code": {"type": ["integer", "null"]},
                "artifact_path": nullable_text,
                "receipt_id": nullable_text,
                "artifact_id": nullable_text,
            },
            "required": ["label", "details"],
            "additionalProperties": False,
        }
        risk = {
            "type": "object",
            "properties": {"description": text, "blocking": {"type": "boolean"}, "blocker_category": nullable_text},
            "required": ["description", "blocking", "blocker_category"],
            "additionalProperties": False,
        }
        question = {
            "type": "object",
            "properties": {"body": text, "blocking": {"type": "boolean"}, "blocker_category": nullable_text},
            "required": ["body", "blocking", "blocker_category"],
            "additionalProperties": False,
        }
        schemas = {
            "context": {"type": "object", "properties": {"if_none_match": nullable_text, "if_dynamic_none_match": nullable_text}, "additionalProperties": False},
            "check": {"type": "object", "properties": {"command": text, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}}, "required": ["command"], "additionalProperties": False},
            "checkpoint": {"type": "object", "properties": {"summary": text, "evidence": {"type": "array", "items": evidence}}, "required": ["summary", "evidence"], "additionalProperties": False},
            "artifact-register": {"type": "object", "properties": {"path": text}, "required": ["path"], "additionalProperties": False},
            "question": {"type": "object", "properties": {"body": text, "blocking": {"type": "boolean"}}, "required": ["body", "blocking"], "additionalProperties": False},
            "blocker": {"type": "object", "properties": {"category": text, "scope_type": {"type": "string", "enum": ["TASK", "ASSIGNMENT"]}, "scope_id": nullable_text, "description": text}, "required": ["category", "scope_type", "description"], "additionalProperties": False},
            "follow-up": {"type": "object", "properties": {"body": text}, "required": ["body"], "additionalProperties": False},
            "submit": {
                "type": "object",
                "properties": {
                    "summary": text,
                    "evidence": {"type": "array", "minItems": 1, "items": evidence},
                    "risks": {"type": "array", "items": risk},
                    "unresolved_questions": {"type": "array", "items": question},
                    "follow_up_work": {"type": "array", "items": {"type": "object", "properties": {"body": text}, "required": ["body"], "additionalProperties": False}},
                },
                "required": ["summary", "evidence", "risks", "unresolved_questions", "follow_up_work"],
                "additionalProperties": False,
            },
        }
        return [{"type": "function", "name": "taskledger_" + action.replace("-", "_"), "description": description, "inputSchema": schemas[action]} for action, description in descriptions.items()]

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
        raw_usage = turn.get("usage")
        usage = self._usage(raw_usage) if isinstance(raw_usage, dict) else self.turn_usage.get(handle.turn_id)
        return RuntimeTurnResult(handle, structured, text, usage or Usage(), usage_missing=usage is None)

    @staticmethod
    def _usage(value: dict[str, Any]) -> Usage:
        last = value.get("last") or value.get("lastUsage") or value
        details = last.get("inputTokensDetails") or last.get("input_tokens_details") or {}
        output_details = last.get("outputTokensDetails") or last.get("output_tokens_details") or {}
        return Usage(
            int(last.get("inputTokens", last.get("input_tokens", 0)) or 0),
            int(last.get("cachedInputTokens", details.get("cached_tokens", 0)) or 0),
            int(last.get("outputTokens", last.get("output_tokens", 0)) or 0),
            int(last.get("reasoningTokens", last.get("reasoningOutputTokens", output_details.get("reasoning_tokens", 0))) or 0),
        )

    def _profile(self, profile: str, role: SessionRole) -> dict[str, str]:
        filenames = []
        if role in {SessionRole.REVIEWER, SessionRole.REQUIREMENT_REVIEWER}:
            filenames.append("taskledger-reviewer.toml")
        elif role == SessionRole.TASK_CREATOR:
            filenames.append("taskledger-task-creator.toml")
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
                    raw = path.read_bytes()
                    data = tomllib.loads(raw.decode())
                    model, effort = data.get("model"), data.get("model_reasoning_effort")
                    if isinstance(model, str) and isinstance(effort, str):
                        result = {"model": model, "effort": effort, "config_hash": hashlib.sha256(raw).hexdigest()}
                        if isinstance(data.get("developer_instructions"), str):
                            result["developer_instructions"] = data["developer_instructions"]
                        return result
        raise RuntimeError(f"Codex profile configuration is missing for {profile}")

    def _sandbox(self, *, subject_id: str, cwd: str | None, writable: bool) -> dict[str, Any]:
        if not writable:
            return {"type": "readOnly", "networkAccess": False}
        writable_roots = self._worker_writable_roots(cwd)
        if subject_id in self.worker_sockets:
            writable_roots.append(str(Path(self.worker_sockets[subject_id]).parent))
        return {"type": "workspaceWrite", "writableRoots": writable_roots, "networkAccess": False}

    @staticmethod
    def _error_text(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("message") or value.get("code") or value)
        return str(value or "unknown Codex error")
