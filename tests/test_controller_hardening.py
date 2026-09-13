from __future__ import annotations

import asyncio
import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.fakes import FakeLedger, FakeRuntime, TurnScript, accepted_verdict
from taskledger.controller.journal import Journal
from taskledger.controller.model import (
    DispatchReason,
    PromptPacket,
    RuntimeTurnHandle,
    RuntimeTurnResult,
    SessionRole,
    Usage,
    UsagePrecision,
)
from taskledger.controller.profiles import ConfigurationConflict, ProfileResolver
from taskledger.controller.project import ProjectController, ProjectControllerConfig
from taskledger.controller.reporting import controller_report
from taskledger.controller.supervisor import Supervisor, SupervisorConfig
from taskledger.controller.taskledger_adapter import TaskledgerLedgerAdapter
from taskledger.controller.worker_broker import WorkerBroker
from taskledger.core import LedgerError, now
from taskledger.db import connect, transaction


class StubAppServer(AppServerRuntime):
    def __init__(self, repository_root: str):
        super().__init__(repository_root=repository_root)
        self.requests = []

    async def _ensure_started(self):
        return None

    async def _request(self, method, params):
        self.requests.append((method, params))
        if method == "thread/start":
            if "unknown_codex_field" in params.get("config", {}):
                raise RuntimeError("unknown configuration field")
            return {"thread": {"id": "thread"}, "model": params["model"], "reasoningEffort": params["config"]["model_reasoning_effort"]}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        raise AssertionError(method)


class MiniService:
    def __init__(self, con):
        self.con = con
        self.dynamic = {
            "correction_packet": None,
            "checkpoint_progress": {"approved_count": 0, "total": 0},
        }

    def context(self, assignment):
        return assignment["context_json"]

    def correction_packet(self, project, assignment):
        return self.dynamic["correction_packet"]

    def checkpoint_progress(self, assignment):
        return self.dynamic["checkpoint_progress"]


class ControllerHardeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "ledger"
        self.con = connect(self.home)
        stamp = now()
        with transaction(self.con):
            self.con.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("p", str(self.root), str(self.root / ".git"), "main", stamp, "ACTIVE", stamp, None, None, stamp, stamp))
        self.journal = Journal(self.con, self.home)

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def role_file(self, body: str, *, user: bool = False):
        base = self.root / ("user" if user else ".codex") / "agents"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "taskledger-worker-routine.toml"
        path.write_text(body)
        return path

    def resolver(self):
        return ProfileResolver(self.root, codex_home=self.root / "user")

    def mini_adapter(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("""
          CREATE TABLE assignments(id TEXT PRIMARY KEY,project_id TEXT,context_json TEXT,worktree_path TEXT);
          CREATE TABLE worker_questions(id TEXT,assignment_id TEXT,body TEXT,is_blocking INTEGER,state TEXT,answer TEXT,asked_at TEXT);
          INSERT INTO assignments VALUES('a','p','{"assignment":{"id":"a"},"task":{"objective":"build"}}','/tmp');
        """)
        service = MiniService(con)
        return con, service, TaskledgerLedgerAdapter(service, {"id": "p"}, {})

    def test_worker_broker_validation_names_exact_submit_field(self):
        with self.assertRaises(LedgerError) as raised:
            WorkerBroker._validate_submit({
                "summary": "done", "evidence": [{"label": "check", "details": "passed"}],
                "risks": [], "unresolved_questions": [
                    {"description": "none", "blocking": False, "blocker_category": None}
                ], "follow_up_work": [],
            })
        self.assertEqual(raised.exception.details["field_path"], "submit.unresolved_questions[0].body")
        self.assertEqual(raised.exception.details["unexpected_fields"], ["description"])

    def test_worker_broker_rejects_checkpoint_without_plan_before_service_call(self):
        class BrokerService:
            def __init__(inner):
                inner.con = sqlite3.connect(":memory:")
                inner.con.row_factory = sqlite3.Row
                inner.con.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT)")
                inner.con.execute("INSERT INTO tasks VALUES('t','ASSIGNED')")
                inner.checkpoint_calls = 0
            def worker_assignment(inner, _principal):
                return {"id": "p"}, {"id": "a", "task_id": "t"}
            def checkpoint_progress(inner, _assignment):
                return {"pending": None, "next": None}
            def worker_checkpoint(inner, _principal, _data):
                inner.checkpoint_calls += 1
                return {}
        service = BrokerService()
        try:
            broker = WorkerBroker(service, object(), self.root / "worker.sock")
            with self.assertRaises(LedgerError) as raised:
                broker._dispatch("checkpoint", {"summary": "x", "evidence": []})
            self.assertEqual(raised.exception.code, "CHECKPOINT_NOT_AVAILABLE")
            self.assertFalse(raised.exception.details["retryable"])
            self.assertEqual(service.checkpoint_calls, 0)
        finally:
            service.con.close()

    def test_worker_broker_submit_is_terminal_and_latched_by_durable_state(self):
        class BrokerService:
            def __init__(inner):
                inner.con = sqlite3.connect(":memory:")
                inner.con.row_factory = sqlite3.Row
                inner.con.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT)")
                inner.con.execute("INSERT INTO tasks VALUES('t','ASSIGNED')")
                inner.check_calls = 0
            def worker_assignment(inner, _principal):
                return {"id": "p"}, {"id": "a", "task_id": "t"}
            def worker_submit(inner, _principal, _data):
                inner.con.execute("UPDATE tasks SET state='SUBMITTED' WHERE id='t'")
                return {"submission_id": "s", "state": "PENDING"}
            def worker_check(inner, _principal, _data):
                inner.check_calls += 1
                return {"receipt_id": "r", "source_revision": "head"}
        service = BrokerService()
        try:
            broker = WorkerBroker(service, object(), self.root / "worker.sock")
            result = broker._dispatch("submit", {
                "summary": "done", "evidence": [{"label": "x", "details": "y"}],
                "risks": [], "unresolved_questions": [], "follow_up_work": [],
            })
            self.assertEqual(result["_terminal"], "SUBMITTED")
            with self.assertRaises(LedgerError) as raised:
                broker._dispatch("check", {"command": "test"})
            self.assertEqual(raised.exception.code, "ASSIGNMENT_ALREADY_SUBMITTED")
            self.assertEqual(service.check_calls, 0)
        finally:
            service.con.close()

    async def test_dynamic_submit_interrupts_provider_turn_as_successful_terminal(self):
        class Stdin:
            def __init__(inner): inner.lines = []
            def write(inner, value): inner.lines.append(value)
            async def drain(inner): return None
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        stdin = Stdin()
        runtime.process = type("Process", (), {"stdin": stdin})()
        runtime.session_config["thread"] = {"subject_id": "assignment"}
        runtime.worker_tools["assignment"] = lambda action, data: {
            "submission_id": "submission", "state": "PENDING", "_terminal": "SUBMITTED"
        }
        runtime.turn_threads["turn"] = "thread"
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            await runtime._handle_dynamic_tool_call({
                "id": 7, "params": {"threadId": "thread", "turnId": "turn", "tool": "taskledger_submit", "arguments": {}},
            })
            await asyncio.sleep(0)
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_SUBMISSION_RECORDED")
        request.assert_awaited_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})
        response = json.loads(stdin.lines[0])
        self.assertTrue(response["result"]["success"])
        self.assertIn('"_terminal":"SUBMITTED"', response["result"]["contentItems"][0]["text"])

    async def test_repeated_dynamic_tool_calls_trip_turn_guard(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.turn_threads["turn"] = "thread"
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            for _ in range(runtime.MAX_REPEAT_DYNAMIC_TOOL_CALLS_PER_TURN):
                runtime._admit_dynamic_tool_call("turn", "context", {})
            with self.assertRaises(LedgerError) as raised:
                runtime._admit_dynamic_tool_call("turn", "context", {})
            await asyncio.sleep(0)
        self.assertEqual(raised.exception.code, "REPEATED_TOOL_CALL")
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_REPEATED_TOOL_CALL_LIMIT")

    async def test_total_dynamic_tool_limit_allows_limit_and_rejects_next(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.turn_threads["turn"] = "thread"
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            for number in range(runtime.MAX_DYNAMIC_TOOL_CALLS_PER_TURN):
                runtime._admit_dynamic_tool_call("turn", "context", {"attempt": number})
            self.assertNotIn("turn", runtime.turn_interrupt_reasons)
            with self.assertRaises(LedgerError) as raised:
                runtime._admit_dynamic_tool_call("turn", "context", {"attempt": "overflow"})
            await asyncio.sleep(0)
        self.assertEqual(raised.exception.code, "TURN_TOOL_LIMIT")
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_DYNAMIC_TOOL_CALL_LIMIT")
        request.assert_awaited_once_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})

    async def test_dynamic_guard_total_limit_takes_precedence_and_interrupts_once(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.turn_threads["turn"] = "thread"
        signature = "context:" + json.dumps({}, separators=(",", ":"), sort_keys=True)
        runtime.turn_dynamic_tool_calls["turn"] = runtime.MAX_DYNAMIC_TOOL_CALLS_PER_TURN
        runtime.turn_tool_signatures["turn"] = Counter({
            signature: runtime.MAX_REPEAT_DYNAMIC_TOOL_CALLS_PER_TURN,
        })
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            with self.assertRaises(LedgerError) as raised:
                runtime._admit_dynamic_tool_call("turn", "context", {})
            runtime._schedule_guard_interrupt("turn", "TASKLEDGER_REPEATED_TOOL_CALL_LIMIT")
            await asyncio.sleep(0)
        self.assertEqual(raised.exception.code, "TURN_TOOL_LIMIT")
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_DYNAMIC_TOOL_CALL_LIMIT")
        request.assert_awaited_once()

    async def test_failed_dynamic_tool_limit_interrupts_on_eighth_failure(self):
        class Stdin:
            def __init__(inner): inner.lines = []
            def write(inner, value): inner.lines.append(value)
            async def drain(inner): return None

        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.process = type("Process", (), {"stdin": Stdin()})()
        runtime.session_config["thread"] = {"subject_id": "assignment"}
        runtime.turn_threads["turn"] = "thread"
        calls = []
        def fail(_action, data):
            calls.append(data["attempt"])
            raise LedgerError("INVALID_REQUEST", "deliberate failure")
        runtime.worker_tools["assignment"] = fail
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            for number in range(runtime.MAX_FAILED_DYNAMIC_TOOL_CALLS_PER_TURN - 1):
                await runtime._handle_dynamic_tool_call({
                    "id": number, "params": {"threadId": "thread", "turnId": "turn",
                    "tool": "taskledger_context", "arguments": {"attempt": number}},
                })
            self.assertNotIn("turn", runtime.turn_interrupt_reasons)
            await runtime._handle_dynamic_tool_call({
                "id": 99, "params": {"threadId": "thread", "turnId": "turn",
                "tool": "taskledger_context", "arguments": {"attempt": 99}},
            })
            await asyncio.sleep(0)
        self.assertEqual(len(calls), runtime.MAX_FAILED_DYNAMIC_TOOL_CALLS_PER_TURN)
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_FAILED_TOOL_CALL_LIMIT")
        request.assert_awaited_once_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})

    async def test_terminal_latched_tool_call_does_not_dispatch_or_inflate_failures(self):
        class Stdin:
            def __init__(inner): inner.lines = []
            def write(inner, value): inner.lines.append(value)
            async def drain(inner): return None

        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.process = type("Process", (), {"stdin": Stdin()})()
        runtime.session_config["thread"] = {"subject_id": "assignment"}
        runtime.turn_threads["turn"] = "thread"
        runtime.turn_interrupt_reasons["turn"] = "TASKLEDGER_SUBMISSION_RECORDED"
        handler = Mock()
        runtime.worker_tools["assignment"] = handler
        await runtime._handle_dynamic_tool_call({
            "id": 1, "params": {"threadId": "thread", "turnId": "turn",
            "tool": "taskledger_context", "arguments": {}},
        })
        handler.assert_not_called()
        self.assertEqual(runtime.turn_failed_dynamic_tool_calls["turn"], 0)
        response = json.loads(runtime.process.stdin.lines[0])
        self.assertEqual(json.loads(response["result"]["contentItems"][0]["text"])["error"]["code"], "TURN_TERMINAL")

    async def test_command_limit_allows_limit_and_interrupts_on_next(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.turn_threads["turn"] = "thread"
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            for _ in range(runtime.MAX_COMMAND_EXECUTIONS_PER_TURN):
                runtime._record_command_execution("turn")
            self.assertNotIn("turn", runtime.turn_interrupt_reasons)
            runtime._record_command_execution("turn")
            await asyncio.sleep(0)
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_COMMAND_EXECUTION_LIMIT")
        request.assert_awaited_once_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})

    async def test_provider_token_guard_interrupts_one_runaway_turn(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.turn_threads["turn"] = "thread"
        runtime.turn_usage_before["turn"] = Usage(100, 50, 10)
        runtime.thread_usage["thread"] = Usage(
            100 + runtime.MAX_PROVIDER_TOKENS_PER_TURN, 50, 11,
        )
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            runtime._enforce_provider_token_guard("thread", "turn")
            await asyncio.sleep(0)
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_PROVIDER_TOKEN_LIMIT")
        request.assert_awaited_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})

    async def test_provider_token_guard_allows_exact_limit_and_stops_limit_plus_one(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        runtime.turn_threads["turn"] = "thread"
        runtime.turn_usage_before["turn"] = Usage(100, 50, 10)
        request = AsyncMock(return_value={})
        with patch.object(runtime, "_request", new=request):
            runtime.thread_usage["thread"] = Usage(
                100 + runtime.MAX_PROVIDER_TOKENS_PER_TURN, 50, 10,
            )
            runtime._enforce_provider_token_guard("thread", "turn")
            self.assertNotIn("turn", runtime.turn_interrupt_reasons)
            runtime.thread_usage["thread"] = Usage(
                101 + runtime.MAX_PROVIDER_TOKENS_PER_TURN, 50, 10,
            )
            runtime._enforce_provider_token_guard("thread", "turn")
            await asyncio.sleep(0)
        self.assertEqual(runtime.turn_interrupt_reasons["turn"], "TASKLEDGER_PROVIDER_TOKEN_LIMIT")
        request.assert_awaited_once_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})

    async def test_submission_guard_interruption_completes_provider_turn(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        handle = RuntimeTurnHandle("thread", "turn")
        runtime.turn_interrupt_reasons["turn"] = "TASKLEDGER_SUBMISSION_RECORDED"
        runtime.turn_usage_events["turn"] = asyncio.Event();runtime.turn_usage_events["turn"].set()
        runtime.turn_waiters["turn"] = asyncio.get_running_loop().create_future()
        await runtime._finish_turn_after_usage(handle, {"id": "turn", "status": "interrupted"})
        result = await runtime.wait_turn(handle)
        self.assertEqual(result.handle, handle)

    async def test_supervisor_classifies_provider_turn_guard_as_budget_pause(self):
        ledger = FakeLedger()
        runtime = FakeRuntime(
            worker_turns=[TurnScript(failure="TASKLEDGER_PROVIDER_TOKEN_LIMIT")], reviewer_turns=[],
        )
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        result = await Supervisor(
            project_id="p", run_id=run, ledger=ledger, runtime=runtime,
            journal=self.journal, config=SupervisorConfig(),
        ).run_assignment("a1")
        self.assertEqual(result.pause_reason.value, "BUDGET_EXHAUSTED")
        self.assertEqual(runtime.turns_started, 1)

    @unittest.skipUnless(
        os.environ.get("TASKLEDGER_ZERO_MODEL_CODEX") == "1",
        "set TASKLEDGER_ZERO_MODEL_CODEX=1 for the local no-model capability sentinel",
    )
    async def test_r4_ambient_mcp_sentinel_is_removed_on_start_and_resume(self):
        codex_home = self.root / "codex-home"
        codex_home.mkdir()
        sentinel = self.root / "sentinel_mcp.py"
        marker = self.root / "sentinel-started"
        sentinel.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text('started\\n')\n"
            "for line in sys.stdin:\n"
            "    try: message = json.loads(line)\n"
            "    except json.JSONDecodeError: continue\n"
            "    if 'id' not in message: continue\n"
            "    method = message.get('method')\n"
            "    if method == 'initialize':\n"
            "        result = {'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'sentinel','version':'1'}}\n"
            "    elif method == 'tools/list': result = {'tools':[]}\n"
            "    else: result = {}\n"
            "    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}), flush=True)\n"
        )
        sentinel.chmod(0o700)
        (codex_home / "config.toml").write_text(
            "[mcp_servers.sentinel]\n"
            f"command = {json.dumps(str(sentinel))}\n"
            f"args = [{json.dumps(str(marker))}]\n"
        )
        self.role_file(
            'model="gpt-5.6-luna"\nmodel_reasoning_effort="low"\n'
        )
        (self.root / ".codex" / "agents" / "taskledger-reviewer.toml").write_text(
            'model="gpt-5.6-luna"\nmodel_reasoning_effort="low"\n'
        )

        class InheritedRuntime(AppServerRuntime):
            async def _ensure_started(inner_self):
                if inner_self.process and inner_self.process.returncode is None:
                    return
                inner_self.process = await asyncio.create_subprocess_exec(
                    inner_self.codex_executable,
                    "app-server",
                    "--stdio",
                    "--strict-config",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                inner_self.reader_task = asyncio.create_task(inner_self._read_messages())
                inner_self.stderr_task = asyncio.create_task(inner_self._drain_stderr())
                await inner_self._request("initialize", {
                    "clientInfo": {"name": "sentinel_control", "title": "Sentinel Control", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                })
                await inner_self._notify("initialized", {})

        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            control = InheritedRuntime(repository_root=str(self.root))
            try:
                await control._ensure_started()
                status = await control._request(
                    "mcpServerStatus/list", {"cursor": None, "limit": 100, "detail": "full"}
                )
                self.assertIn("sentinel", json.dumps(status))
                self.assertTrue(marker.is_file(), "control app server did not load the valid sentinel")
            finally:
                await control.close()

            marker.unlink()
            runtime = AppServerRuntime(repository_root=str(self.root))
            try:
                session = await runtime.start_session(
                    role=SessionRole.REVIEWER,
                    profile="taskledger_reviewer",
                    subject_id="sentinel-review",
                    cwd=str(self.root),
                    writable=False,
                )
                started_status = await runtime._request(
                    "mcpServerStatus/list", {"cursor": None, "limit": 100, "detail": "full"}
                )
                sentinel_status = next(
                    row for row in started_status["data"] if row["name"] == "sentinel"
                )
                self.assertIsNone(sentinel_status["runtimeStatus"])
                self.assertFalse(marker.exists())
                config = runtime.session_config[session.thread_id]["nativeConfig"]
                self.assertEqual(config["agents"]["enabled"], False)
                self.assertEqual(config["features"]["multi_agent_v2"], False)
                self.assertEqual(config["features"]["collab"], False)
            finally:
                await runtime.close()

            resumed_runtime = AppServerRuntime(repository_root=str(self.root))
            try:
                with self.assertRaisesRegex(RuntimeError, "no rollout found"):
                    await resumed_runtime.resume_session(
                        thread_id=session.thread_id,
                        role=SessionRole.REVIEWER,
                        profile="taskledger_reviewer",
                        subject_id="sentinel-review",
                        cwd=str(self.root),
                        writable=False,
                    )
                resumed_status = await resumed_runtime._request(
                    "mcpServerStatus/list", {"cursor": None, "limit": 100, "detail": "full"}
                )
                resumed_sentinel = next(
                    row for row in resumed_status["data"] if row["name"] == "sentinel"
                )
                self.assertIsNone(resumed_sentinel["runtimeStatus"])
                self.assertFalse(marker.exists())
            finally:
                await resumed_runtime.close()

    def completed_report_run(self):
        run_id = self.journal.create_run("p", mode="PROJECT", config={"manifest": {"scope": "POST_APPROVAL_EXECUTION"}, "limits": {"max_workers": 1, "max_reviewers": 1, "max_inflight_targets": 2}})
        stamp = now()
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO principals(id,project_id,role,assignment_id,active,created_at) VALUES('principal','p','ORCHESTRATOR',NULL,1,?)",
                (stamp,),
            )
            self.con.execute(
                "INSERT INTO tasks(id,project_id,current_revision,state,created_at,updated_at,completed_at) VALUES('task','p',1,'COMPLETED',?,?,?)",
                (stamp, stamp, stamp),
            )
            self.con.execute(
                "INSERT INTO task_revisions(task_id,revision,objective,implementation_scope,created_by_principal_id,created_at) VALUES('task',1,'objective','scope','principal',?)",
                (stamp,),
            )
            self.con.execute(
                "INSERT INTO assignments(id,project_id,task_id,task_revision,attempt_number,worker_profile,state,base_commit_oid,branch_name,worktree_path,context_json,created_at,closed_at) VALUES('a','p','task',1,1,'routine','CLOSED','base','branch','/tmp','{}',?,?)",
                (stamp, stamp),
            )
            self.con.execute(
                "INSERT INTO submissions(id,project_id,assignment_id,sequence,state,summary,head_commit_oid,changed_files_json,evidence_json,risks_json,unresolved_questions_json,follow_up_work_json,payload_hash,submitted_by_principal_id,submitted_at,resolved_at) VALUES('submission','p','a',1,'REJECTED','summary','head','[]','[]','[]','[]','[]','hash','principal',?,?)",
                (stamp, stamp),
            )
            self.con.execute(
                "INSERT INTO submission_verifications(id,submission_id,verifier_principal_id,outcome,criterion_results_json,behavior_matches_intent,required_evidence_present,blocking_issues_remaining,corrections,notes,created_at) VALUES('verification','submission','principal','REJECTED','[]',0,1,0,'fix','notes',?)",
                (stamp,),
            )
            self.con.execute(
                "INSERT INTO execution_receipts(id,project_id,assignment_id,executed_by_principal_id,execution_role,command,cwd,tree_fingerprint_before,tree_fingerprint_after,started_at,finished_at,status,telemetry_json) VALUES('receipt','p','a','principal','REVIEWER','check','/tmp','before','after',?,?,'FAILED','{}')",
                (stamp, stamp),
            )
            self.con.execute(
                "INSERT INTO integration_attempts(id,project_id,task_id,submission_id,state,canonical_branch,canonical_before_oid,accepted_head_oid,error_json,started_at,finished_at) VALUES('integration','p','task','submission','FAILED','main','before','head','{}',?,?)",
                (stamp, stamp),
            )
            self.con.execute(
                "INSERT INTO blockers(id,project_id,category,scope_type,scope_id,description,state,created_by_system,created_at,resolution,resolved_at) VALUES('blocker','p','TEST','PROJECT',NULL,'blocked','RESOLVED',1,?,'resolved',?)",
                (stamp, stamp),
            )
        session = self.journal.create_session(run_id=run_id, project_id="p", role="WORKER", profile="routine", subject_id="a", external_thread_id="thread", config_hash="h", runtime_identity={})
        packet = __import__("taskledger.controller.model", fromlist=["PromptPacket"]).PromptPacket("work", DispatchReason.INITIAL_WORK, controller_payload_bytes=4, static_assignment_bytes=2)
        local = self.journal.begin_turn(session.id, "worker", packet=packet, progress_before="before")
        handle = RuntimeTurnHandle("thread", "turn")
        self.journal.acknowledge_turn(local, handle)
        self.journal.complete_turn(local, RuntimeTurnResult(handle, usage=Usage(10, 4, 3, 1, 2), usage_precision=UsagePrecision.THREAD_TOTAL_DELTA, cumulative_before=Usage(), cumulative_after=Usage(10, 4, 3, 1, 2)))
        self.journal.consume_turn(local, progress_after="after")
        self.journal.finish_run(run_id, "COMPLETED")
        return run_id

    def test_nc191_native_role_metadata_and_config_parsing(self):
        self.role_file('name="native"\ndescription="role"\nnickname_candidates=["Ada"]\nmodel="m"\nmodel_reasoning_effort="low"\nmodel_verbosity="low"\n')
        profile = self.resolver().resolve("routine", SessionRole.WORKER)
        self.assertEqual(profile.role_name, "native")
        self.assertEqual(profile.nickname_candidates, ("Ada",))
        self.assertNotIn("name", profile.native_config)
        self.assertEqual(profile.native_config["model_verbosity"], "low")

    def test_nc192_project_local_profile_precedence(self):
        self.role_file('model="global"\nmodel_reasoning_effort="low"\n', user=True)
        path = self.role_file('model="project"\nmodel_reasoning_effort="medium"\n')
        profile = self.resolver().resolve("routine", SessionRole.WORKER)
        self.assertEqual((profile.model, profile.source_kind, profile.source_path), ("project", "PROJECT", path.resolve()))

    async def test_nc193_profile_unknown_field_fails_loudly(self):
        self.role_file('model="m"\nmodel_reasoning_effort="low"\nunknown_codex_field=true\n')
        runtime = StubAppServer(str(self.root))
        with self.assertRaisesRegex(RuntimeError, "unknown configuration field"):
            await runtime.start_session(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=None, writable=False)

    def test_nc194_controller_owned_profile_conflict_fails(self):
        for body in ('approval_policy="never"', '[agents]\nenabled=true', '[features]\nmulti_agent_v2=true', '[features]\ncollab=true', '[tools]\nweb_search=true'):
            self.role_file(f'model="m"\nmodel_reasoning_effort="low"\n{body}\n')
            with self.assertRaises(ConfigurationConflict):
                self.resolver().resolve("routine", SessionRole.WORKER)

    async def test_nc195_profile_identity_persisted_and_drift_detected(self):
        self.role_file('model="m"\nmodel_reasoning_effort="low"\n')
        runtime = StubAppServer(str(self.root))
        identity = runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=None, writable=False)
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(run_id=run, project_id="p", role="WORKER", profile="routine", subject_id="a", external_thread_id="t", config_hash=identity.agent_config_hash, runtime_identity=identity.as_dict())
        self.assertEqual(session.runtime_identity["profile_source_kind"], "PROJECT")
        self.role_file('model="different"\nmodel_reasoning_effort="low"\n')
        self.assertNotEqual(runtime.session_identity(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=None, writable=False).as_dict(), session.runtime_identity)

    def test_nc196_generic_reviewer_profile(self):
        text = Path("skills/taskledger/assets/taskledger-reviewer.toml").read_text()
        self.assertIn("bounded independent review", text)
        self.assertNotIn("Review only the supplied immutable", text)
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        a = runtime.session_identity(role=SessionRole.REVIEWER, profile="taskledger_reviewer", subject_id="s", cwd=None, writable=False)
        b = runtime.session_identity(role=SessionRole.REQUIREMENT_REVIEWER, profile="taskledger_reviewer", subject_id="r", cwd=None, writable=False)
        self.assertEqual(a.profile_hash, b.profile_hash)

    async def test_nc197_multi_agent_disabled_in_runtime_config(self):
        self.role_file('model="m"\nmodel_reasoning_effort="low"\n')
        runtime = StubAppServer(str(self.root))
        await runtime.start_session(role=SessionRole.WORKER, profile="routine", subject_id="a", cwd=None, writable=False)
        config = runtime.requests[0][1]["config"]
        self.assertIs(config["agents"]["enabled"], False)
        self.assertIs(config["features"]["multi_agent_v2"], False)
        self.assertIs(config["features"]["collab"], False)
        self.assertIs(config["include_collaboration_mode_instructions"], False)

    def test_nc198_full_first_worker_prompt(self):
        con, _, adapter = self.mini_adapter()
        packet = adapter.worker_prompt_packet("a", first_turn=True)
        self.assertIn('"objective":"build"', packet.text)
        self.assertIn("Do not load the Taskledger orchestration skill or invoke the Taskledger CLI", packet.text)
        self.assertIn("Implementation skills explicitly required", packet.text)
        self.assertNotIn("Do not load Codex skill files", packet.text)
        self.assertGreater(packet.static_assignment_bytes, 0)
        con.close()

    def test_nc199_minimal_unchanged_continuation(self):
        con, _, adapter = self.mini_adapter()
        first = adapter.worker_prompt_packet("a", first_turn=True)
        second = adapter.worker_prompt_packet("a", first_turn=False, previous_hashes=first.context_hashes)
        self.assertNotIn('"objective":"build"', second.text)
        self.assertNotIn("Do not reload Codex skill files", second.text)
        self.assertEqual((second.static_assignment_bytes, second.dynamic_state_bytes), (0, 0))
        con.close()

    def test_legacy_worker_policy_is_superseded_once_with_question_answer(self):
        con, _, adapter = self.mini_adapter()
        con.execute("INSERT INTO worker_questions VALUES('q','a','which API?',1,'ANSWERED','Use v2','2026')")
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(
            run_id=run, project_id="p", role="WORKER", profile="routine",
            subject_id="a", external_thread_id="thread", config_hash="h",
        )
        old = self.journal.begin_turn(
            session.id, "worker", packet=PromptPacket("legacy", DispatchReason.INITIAL_WORK)
        )
        old_handle = RuntimeTurnHandle("thread", "old-turn")
        self.journal.acknowledge_turn(old, old_handle)
        self.journal.complete_turn(old, RuntimeTurnResult(old_handle))
        self.journal.consume_turn(old)
        self.assertEqual(self.journal.worker_prompt_policy_version(session.id), 1)

        packet = adapter.worker_prompt_packet(
            "a", first_turn=False, previous_hashes={}, include_policy_update=True,
        )
        self.assertIn("earlier blanket prohibition", packet.text)
        self.assertIn("which API?", packet.text)
        self.assertIn("Use v2", packet.text)
        self.assertEqual(packet.prompt_builder_version, "controller-prompt-v3")
        migrated = self.journal.begin_turn(session.id, "worker", packet=packet)
        self.assertEqual(self.journal.worker_prompt_policy_version(session.id), 2)
        self.assertEqual(self.journal.session_turn_count(session.id), 2)
        self.journal.fail_turn(migrated, "test stop", uncertain=True)

        next_packet = adapter.worker_prompt_packet(
            "a", first_turn=False, previous_hashes=packet.context_hashes,
            include_policy_update=self.journal.worker_prompt_policy_version(session.id) < 2,
        )
        self.assertNotIn("earlier blanket prohibition", next_packet.text)
        self.journal.finish_run(run, "PAUSED")
        con.close()

    async def test_resume_offset_survives_repeated_attached_continuations(self):
        class CounterRuntime(StubAppServer):
            def __init__(self, root, replayed=None):
                super().__init__(root)
                self.replayed = replayed
                self.provider_total = Usage()
                self.turn_number = 0

            async def _request(self, method, params):
                self.requests.append((method, params))
                if method == "thread/resume":
                    if self.replayed is not None:
                        self.thread_usage[params["threadId"]] = self.replayed
                        self.thread_usage_events.setdefault(params["threadId"], asyncio.Event()).set()
                    return {"thread": {"id": params["threadId"]}}
                if method == "turn/start":
                    self.turn_number += 1
                    return {"turn": {"id": f"turn-{self.turn_number}"}}
                raise AssertionError(method)

        self.role_file('model="m"\nmodel_reasoning_effort="low"\n')
        runtime = CounterRuntime(str(self.root))
        baseline = Usage(100, 20, 30, 5, 2)
        session = await runtime.resume_session(
            thread_id="thread", role=SessionRole.WORKER, profile="routine",
            subject_id="a", cwd=None, writable=False,
            cumulative_usage_baseline=baseline,
        )
        self.assertEqual(runtime.thread_usage_offsets["thread"], baseline)
        prior = baseline
        for provider_total in (Usage(4, 1, 2), Usage(9, 2, 5), Usage(15, 4, 9)):
            await runtime.resume_session(
                thread_id=session.thread_id, role=SessionRole.WORKER, profile="routine",
                subject_id="a", cwd=None, writable=False,
                cumulative_usage_baseline=prior,
            )
            handle = await runtime.start_turn(thread_id="thread", prompt="continue")
            runtime.thread_usage["thread"] = baseline + provider_total
            result = runtime._turn_result(handle, {})
            self.assertGreater(result.usage.total_tokens, 0)
            self.assertEqual(result.cumulative_before, prior)
            prior = result.cumulative_after
        self.assertEqual(runtime.thread_usage_offsets["thread"], baseline)
        self.assertEqual(sum(method == "thread/resume" for method, _ in runtime.requests), 1)

        incompatible = CounterRuntime(str(self.root), replayed=Usage(99, 20, 30, 5, 2))
        with self.assertRaisesRegex(RuntimeError, "durable controller baseline"):
            await incompatible.resume_session(
                thread_id="thread", role=SessionRole.WORKER, profile="routine",
                subject_id="a", cwd=None, writable=False,
                cumulative_usage_baseline=baseline,
            )

    async def test_terminal_recovery_uses_bounded_latest_provider_history(self):
        class RecoveryRuntime(StubAppServer):
            async def _request(self, method, params):
                self.requests.append((method, params))
                if method == "thread/turns/list":
                    return {"data": [
                        {"id": "missing", "status": "completed"},
                        {"id": "before", "status": "completed"},
                    ], "nextCursor": "older-is-irrelevant"}
                if method == "thread/resume":
                    self.thread_usage[params["threadId"]] = Usage(14, 3, 7, 2, 1)
                    self.thread_usage_events.setdefault(params["threadId"], asyncio.Event()).set()
                    return {"thread": {"id": params["threadId"]}}
                raise AssertionError(method)

        self.role_file('model="m"\nmodel_reasoning_effort="low"\n')
        runtime = RecoveryRuntime(str(self.root))
        result = await runtime.recover_terminal_usage(
            handle=RuntimeTurnHandle("thread", "missing"), previous_turn_id="before",
            cumulative_before=Usage(10, 2, 5, 1, 1), target_is_first=False,
            role=SessionRole.WORKER, profile="routine", subject_id="a",
            cwd=None, writable=False,
        )
        self.assertEqual(result.usage, Usage(4, 1, 2, 1, 0))
        history = runtime.requests[0]
        self.assertEqual(history[0], "thread/turns/list")
        self.assertEqual(history[1]["limit"], 3)
        self.assertEqual(history[1]["sortDirection"], "desc")
        self.assertFalse(any(method == "turn/start" for method, _ in runtime.requests))

    def test_nc200_correction_delta(self):
        con, service, adapter = self.mini_adapter()
        first = adapter.worker_prompt_packet("a", first_turn=True)
        service.dynamic["correction_packet"] = {"reviewed_submission_id": "s", "corrections": "fix"}
        second = adapter.worker_prompt_packet("a", first_turn=False, previous_hashes=first.context_hashes)
        self.assertEqual(second.dispatch_reason, DispatchReason.CORRECTION)
        self.assertIn("reviewed_submission_id", second.text)
        self.assertNotIn('"objective":"build"', second.text)
        con.close()

    def test_nc201_checkpoint_and_question_delta(self):
        con, service, adapter = self.mini_adapter()
        first = adapter.worker_prompt_packet("a", first_turn=True)
        service.dynamic["checkpoint_progress"] = {"approved_count": 1, "total": 2}
        second = adapter.worker_prompt_packet("a", first_turn=False, previous_hashes=first.context_hashes)
        self.assertEqual(second.dispatch_reason, DispatchReason.CHECKPOINT_CONTINUATION)
        con.execute("INSERT INTO worker_questions VALUES('q','a','answer me',1,'ANSWERED','yes','2026')")
        third = adapter.worker_prompt_packet("a", first_turn=False, previous_hashes=second.context_hashes)
        self.assertEqual(third.dispatch_reason, DispatchReason.QUESTION_ANSWERED)
        self.assertIn("answer me", third.text)
        con.execute("INSERT INTO worker_questions VALUES('old','a','older question',0,'OPEN',NULL,'2025')")
        snapshot = adapter.worker_prompt_packet("a", first_turn=True)
        con.execute("UPDATE worker_questions SET answer='new answer' WHERE id='q'")
        delta = adapter.worker_prompt_packet("a", first_turn=False, previous_hashes=snapshot.context_hashes)
        self.assertIn("new answer", delta.text)
        self.assertNotIn("older question", delta.text)
        con.close()

    async def test_nc202_structured_output_retry_is_delta(self):
        ledger = FakeLedger(); ledger.submit()
        ledger.reviewer_prompt = lambda preparation: "full evidence " * 100
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[TurnScript(structured_output={}), TurnScript(structured_output=accepted_verdict())])
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        result = await Supervisor(project_id="p", run_id=run, ledger=ledger, runtime=runtime, journal=self.journal, config=SupervisorConfig()).run_assignment("a1")
        self.assertEqual(result.status.value, "INTEGRATED")
        self.assertGreater(len(runtime.prompts[0]["prompt"]), len(runtime.prompts[1]["prompt"]))
        self.assertIn("previous result", runtime.prompts[1]["prompt"])

        failed_ledger = FakeLedger(); failed_ledger.submit()
        failed_ledger.reviewer_prompt = lambda preparation: "full evidence " * 100
        failed_runtime = FakeRuntime(
            worker_turns=[],
            reviewer_turns=[TurnScript(failure="transport failed"), TurnScript(structured_output=accepted_verdict())],
        )
        failed_run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        failed_result = await Supervisor(
            project_id="p", run_id=failed_run, ledger=failed_ledger, runtime=failed_runtime,
            journal=self.journal, config=SupervisorConfig(),
        ).run_assignment("a1")
        self.assertEqual(failed_result.status.value, "INTEGRATED")
        self.assertEqual(failed_runtime.prompts[0]["prompt"], failed_runtime.prompts[1]["prompt"])

    def test_nc203_compact_final_review_projection(self):
        source = inspect.getsource(ProjectController._final_review_projection)
        self.assertNotIn("requirement_rows", source)
        self.assertNotIn("implementation_scope", source)

    def test_nc204_correction_plan_preserves_existing_links(self):
        task = {"ref": "x", "objective": "o", "implementation_scope": "s", "acceptance_criteria": ["a"], "required_checks": [], "requirement_ids": ["r"], "dependency_task_ids": ["d"], "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["x"]}
        planned = ProjectController._plan_task(task)
        self.assertEqual((planned["requirement_ids"], planned["dependency_task_ids"]), (["r"], ["d"]))

    def test_nc205_independent_capacity_pools(self):
        controller = inspect.getsource(ProjectController.__init__)
        self.assertIn("worker_capacity", controller); self.assertIn("reviewer_capacity", controller)

    def test_nc206_scheduler_has_explicit_inflight_limit(self):
        config = ProjectControllerConfig(max_workers=2, max_reviewers=1)
        self.assertEqual(config.effective_max_inflight_targets, 3)
        self.assertIn("effective_max_inflight_targets", inspect.getsource(ProjectController._compatible_batch))

    def test_nc207_single_scheduler_loop(self):
        self.assertFalse(hasattr(ProjectController, "run_without_lock"))
        self.assertIn("_run_loop", inspect.getsource(ProjectController.run))
        self.assertNotIn("self.run(", inspect.getsource(ProjectController._create_corrections))

    async def test_nc208_terminal_worker_sessions_close_but_blockers_resume(self):
        ledger = FakeLedger(); runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.submit)], reviewer_turns=[TurnScript(structured_output=accepted_verdict())])
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        await Supervisor(project_id="p", run_id=run, ledger=ledger, runtime=runtime, journal=self.journal).run_assignment("a1")
        self.assertIsNone(self.journal.find_active_session(project_id="p", role="WORKER", subject_id="a1"))
        blocked = FakeLedger(assignment_id="b"); blocked_runtime = FakeRuntime(worker_turns=[TurnScript(effect=blocked.block)], reviewer_turns=[])
        run2 = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        await Supervisor(project_id="p", run_id=run2, ledger=blocked, runtime=blocked_runtime, journal=self.journal).run_assignment("b")
        self.assertIsNotNone(self.journal.find_active_session(project_id="p", role="WORKER", subject_id="b"))

    def test_nc209_aggregate_task_creator_budget_and_grant(self):
        run = self.journal.create_run("p", mode="PROJECT", config={})
        for n in range(2):
            session = self.journal.create_session(run_id=run, project_id="p", role="TASK_CREATOR", profile="p", subject_id=str(n), external_thread_id=str(n), config_hash="h", runtime_identity={})
            local = self.journal.begin_turn(session.id, "correction-planning", dispatch_reason=DispatchReason.CORRECTION_PLANNING)
            handle = RuntimeTurnHandle(str(n), "t" + str(n)); self.journal.acknowledge_turn(local, handle); self.journal.complete_turn(local, RuntimeTurnResult(handle, usage_precision=UsagePrecision.THREAD_TOTAL_DELTA)); self.journal.consume_turn(local)
        self.assertEqual(self.journal.run_turn_count(run_id=run, roles=("TASK_CREATOR",)), 2)
        self.journal.grant_budget(run_id=run, kind="TASK_CREATOR_TURNS", amount=1, reason="approved", principal_id=self._principal())

    def _principal(self):
        stamp = now(); pid = "principal"
        self.con.execute("INSERT OR IGNORE INTO principals VALUES(?,?,?,?,?,?,?)", (pid, "p", "ORCHESTRATOR", None, 1, stamp, None))
        return pid

    def test_nc210_multi_response_cumulative_usage_delta(self):
        runtime = AppServerRuntime(repository_root=str(Path.cwd()))
        handle = RuntimeTurnHandle("thread", "turn")
        runtime.turn_usage_before["turn"] = Usage(10, 4, 3, 1, 2)
        runtime.thread_usage["thread"] = Usage(30, 11, 9, 4, 7)
        result = runtime._turn_result(handle, {})
        self.assertEqual(result.usage, Usage(20, 7, 6, 3, 5))
        self.assertEqual(result.usage_precision, UsagePrecision.THREAD_TOTAL_DELTA)

    def test_nc211_cache_write_tokens_accounted_without_double_count(self):
        usage = AppServerRuntime._usage({"inputTokens": 10, "cachedInputTokens": 4, "cacheWriteInputTokens": 3, "outputTokens": 2})
        self.assertEqual((usage.total_tokens, usage.cache_write_input_tokens), (12, 3))

    def test_nc212_usage_precision_persisted_and_reported(self):
        run = self.completed_report_run()
        report = self.journal.usage_report(run_id=run)
        self.assertEqual(report["precision"]["THREAD_TOTAL_DELTA"], 1)

    async def test_nc213_missing_usage_fails_closed(self):
        ledger = FakeLedger(); runtime = FakeRuntime(worker_turns=[TurnScript(effect=ledger.progress, usage_missing=True), TurnScript(effect=ledger.submit)], reviewer_turns=[])
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        result = await Supervisor(project_id="p", run_id=run, ledger=ledger, runtime=runtime, journal=self.journal, config=SupervisorConfig(max_total_tokens=100)).run_assignment("a1")
        self.assertEqual((result.pause_reason.value, runtime.turns_started), ("BUDGET_EXHAUSTED", 1))

    def test_nc214_legacy_usage_table_preserved_but_not_populated(self):
        run = self.completed_report_run()
        self.assertIsNotNone(self.con.execute("SELECT name FROM sqlite_master WHERE name='controller_usage_events'").fetchone())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM controller_usage_events").fetchone()[0], 0)

    def test_nc215_controller_run_manifest_frozen(self):
        manifest = {"canonical_starting_oid": "oid", "starting_plan_fingerprint": "plan", "execution_policy_hash": "policy", "controller_configuration_hash": "config", "taskledger_schema_version": 7, "codex_protocol_identity": "protocol"}
        run = self.journal.create_run("p", mode="PROJECT", config={"manifest": manifest})
        self.assertEqual(self.journal.run(run)["config"]["manifest"], manifest)

    def test_nc216_controller_events_are_transition_based(self):
        run = self.journal.create_run("p", mode="PROJECT", config={})
        first = self.journal.append_event(run_id=run, scope_type="TARGET", scope_id="t", event_type="TARGET_WAITING_FOR_DEPENDENCY", reason_code="WAITING_FOR_DEPENDENCY", transition_only=True)
        second = self.journal.append_event(run_id=run, scope_type="TARGET", scope_id="t", event_type="TARGET_WAITING_FOR_DEPENDENCY", reason_code="WAITING_FOR_DEPENDENCY", transition_only=True)
        self.assertTrue(first); self.assertFalse(second)

    def test_nc217_wait_reason_history_reconstructable(self):
        run = self.journal.create_run("p", mode="PROJECT", config={})
        for reason in ("WAITING_FOR_DEPENDENCY", "WAITING_FOR_WAVE", "WAITING_FOR_WORKER_CAPACITY", "WAITING_FOR_REVIEWER_CAPACITY", "WAITING_FOR_WRITE_SURFACE"):
            self.journal.append_event(run_id=run, scope_type="TARGET", scope_id=reason, event_type="TARGET_" + reason, reason_code=reason)
        self.assertEqual({row[0] for row in self.con.execute("SELECT reason_code FROM controller_events WHERE run_id=? AND reason_code LIKE 'WAITING_%'", (run,))}, {"WAITING_FOR_DEPENDENCY", "WAITING_FOR_WAVE", "WAITING_FOR_WORKER_CAPACITY", "WAITING_FOR_REVIEWER_CAPACITY", "WAITING_FOR_WRITE_SURFACE"})

    async def test_nc218_reporting_never_starts_model_turn(self):
        run = self.completed_report_run()
        class ReportRuntime:
            async def provider_history(self, thread):
                return {"turns": [{"id": "turn"}], "items": []}

            async def start_turn(self, **kwargs):
                raise AssertionError("reporting attempted to start a model turn")

        runtime = ReportRuntime()
        report = await controller_report(
            self.con, run, include_provider_detail=True, provider_loader=runtime.provider_history,
        )
        self.assertEqual(report["identity"]["run_id"], run)

    async def test_nc219_report_survives_provider_unavailable(self):
        run = self.completed_report_run()
        async def loader(thread): raise RuntimeError("offline")
        report = await controller_report(self.con, run, include_provider_detail=True, provider_loader=loader)
        self.assertEqual(report["quality"]["provider_detail"]["status"], "UNAVAILABLE")
        self.assertEqual(report["identity"]["state"], "COMPLETED")

    async def test_nc220_provider_enrichment_is_not_persisted(self):
        run = self.completed_report_run(); before = self.con.total_changes
        async def loader(thread): return {"turns": [{"id": "turn"}], "items": [{"turnId": "turn", "item": {"type": "commandExecution", "status": "completed", "durationMs": 4, "exitCode": 0}}]}
        report = await controller_report(self.con, run, include_provider_detail=True, provider_loader=loader)
        self.assertEqual(report["context_efficiency"]["provider_activity"]["command_count"], 1)
        self.assertEqual(self.con.total_changes, before)

    async def test_nc221_deterministic_report(self):
        run = self.completed_report_run()
        first = await controller_report(self.con, run, include_provider_detail=False)
        second = await controller_report(self.con, run, include_provider_detail=False)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    async def test_nc222_report_uses_existing_domain_facts(self):
        run = self.completed_report_run()
        report = await controller_report(self.con, run, include_provider_detail=False)
        self.assertEqual(report["workers"]["routine"]["tasks"], 1)
        self.assertEqual(report["reviews"]["submissions"]["first_pass_rejected"], 1)
        self.assertEqual(report["reliability"]["required_checks"]["failed"], 1)
        self.assertEqual(report["reliability"]["blockers"]["total"], 1)
        self.assertEqual(report["reliability"]["integration"]["failed"], 1)
        names = {row[0] for row in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("controller_routing_metrics", names)

    def test_nc223_sensitive_content_excluded_from_telemetry(self):
        columns = {row[1] for row in self.con.execute("PRAGMA table_info(controller_turns)")}
        self.assertFalse({"prompt", "command_output", "patch", "reasoning", "tool_result"} & columns)
        self.assertIn("response_hash", columns)
        run = self.journal.create_run("p", mode="ASSIGNMENT", config={})
        session = self.journal.create_session(run_id=run, project_id="p", role="WORKER", profile="routine", subject_id="secret", external_thread_id="thread-secret", config_hash="h", runtime_identity={})
        local = self.journal.begin_turn(session.id, "worker")
        handle = RuntimeTurnHandle("thread-secret", "turn-secret")
        self.journal.acknowledge_turn(local, handle)
        self.journal.complete_turn(local, RuntimeTurnResult(handle, structured_output={"secret": "worker prose"}, final_response="worker prose"))
        stored = self.con.execute("SELECT result_json,final_response,response_hash FROM controller_turns WHERE id=?", (local,)).fetchone()
        self.assertEqual((stored["result_json"], stored["final_response"]), (None, None))
        self.assertIsNotNone(stored["response_hash"])

    async def test_nc224_architecture_invariant_reporting(self):
        run = self.completed_report_run()
        async def loader(thread): return {"turns": [{"id": "turn"}], "items": [{"turnId": "turn", "item": {"type": "collabAgentToolCall"}}, {"turnId": "turn", "item": {"type": "subAgentActivity"}}]}
        report = await controller_report(self.con, run, include_provider_detail=True, provider_loader=loader)
        self.assertEqual(report["architecture_invariants"]["nested_agent_calls"], 1)
        self.assertEqual(report["architecture_invariants"]["subagent_activity"], 1)
