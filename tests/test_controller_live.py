from __future__ import annotations

import os
import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from taskledger.controller.benchmark import SCHEMA_VERSION, compare_measurements, normalize_measurement
from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.model import SessionRole, UsagePrecision
from taskledger.controller.reporting import controller_report
from taskledger.core import LedgerError
from tests.live_evidence import EvidenceRuntime, LiveEvidence


LIVE = os.environ.get("TASKLEDGER_LIVE_CODEX") == "1"
LIVE_MODEL = os.environ.get("TASKLEDGER_LIVE_MODEL", "gpt-5.6-luna")
LIVE_EFFORT = os.environ.get("TASKLEDGER_LIVE_EFFORT", "low")


def report_usage(case: str, role: str, result) -> None:
    print("TASKLEDGER_LIVE_USAGE " + json.dumps({
        "case": case,
        "role": role,
        "model": LIVE_MODEL,
        "effort": LIVE_EFFORT,
        "thread_id": result.handle.thread_id,
        "turn_id": result.handle.turn_id,
        **result.usage.__dict__,
        "usage_missing": result.usage_missing,
        "usage_precision": result.usage_precision.value,
        "cumulative_before": result.cumulative_before.__dict__ if result.cumulative_before else None,
        "cumulative_after": result.cumulative_after.__dict__ if result.cumulative_after else None,
        "exact_response_count": result.exact_response_count,
    }, sort_keys=True))


@unittest.skipUnless(LIVE, "set TASKLEDGER_LIVE_CODEX=1 to spend model usage")
class ControllerLiveContractTests(unittest.IsolatedAsyncioTestCase):
    """Small, explicitly admitted E3 tests from CONTROLLER_TEST_PLAN.md."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        agents = self.root / ".codex" / "agents"
        agents.mkdir(parents=True)
        profile = f'model = "{LIVE_MODEL}"\nmodel_reasoning_effort = "{LIVE_EFFORT}"\n'
        (agents / "taskledger-worker-routine.toml").write_text(profile)
        (agents / "taskledger-reviewer.toml").write_text(profile)

    def tearDown(self):
        self.temp.cleanup()

    async def test_cx001_cx008_cx009_cx010_cx082_cx117_worker_thread_contract(self):
        """Budget: at most two Luna/low worker turns."""
        runtime = AppServerRuntime(repository_root=str(self.root))
        try:
            session = await runtime.start_session(
                role=SessionRole.WORKER,
                profile="routine",
                subject_id="live-contract-worker",
                cwd=str(self.root),
                writable=True,
            )
            self.assertEqual(runtime.session_config[session.thread_id]["model"], LIVE_MODEL)
            self.assertEqual(runtime.session_config[session.thread_id]["effort"], LIVE_EFFORT)

            static_assignment = "Complete immutable assignment marker: OBJECTIVE-ALPHA; criteria=reply ALPHA."
            first_prompt = static_assignment + " Reply with exactly the single word ALPHA and do nothing else."
            first_handle = await runtime.start_turn(thread_id=session.thread_id, prompt=first_prompt)
            first = await runtime.wait_turn(first_handle)
            self.assertEqual((first.final_response or "").strip(), "ALPHA")

            resumed = await runtime.resume_session(
                thread_id=session.thread_id,
                role=SessionRole.WORKER,
                profile="routine",
                subject_id="live-contract-worker",
                cwd=str(self.root),
                writable=True,
            )
            self.assertEqual(resumed.thread_id, session.thread_id)
            second_prompt = "Reply only with the exact word you returned in your previous response."
            second_handle = await runtime.start_turn(thread_id=session.thread_id, prompt=second_prompt)
            second = await runtime.wait_turn(second_handle)
            self.assertEqual((second.final_response or "").strip(), "ALPHA")
            self.assertGreater(first.usage.total_tokens + second.usage.total_tokens, 0)
            history = await runtime.provider_history(session.thread_id)
            user_items = [
                entry["item"] for entry in history["items"]
                if entry.get("item", {}).get("type") == "userMessage"
            ]
            self.assertGreaterEqual(len(user_items), 2)
            self.assertNotIn("complete immutable assignment marker", json.dumps(user_items[-1]))
            print("TASKLEDGER_CONTINUATION_AUDIT " + json.dumps({
                "turn_1_controller_payload_bytes": len(first_prompt.encode("utf-8")),
                "turn_2_controller_payload_bytes": len(second_prompt.encode("utf-8")),
                "turn_1_static_bytes": len(static_assignment.encode("utf-8")),
                "turn_2_static_bytes": 0,
                "turn_1_dynamic_bytes": 0,
                "turn_2_dynamic_bytes": 0,
            }, sort_keys=True))
            report_usage("worker-thread-contract-1", "WORKER", first)
            report_usage("worker-thread-contract-2", "WORKER", second)
        finally:
            await runtime.close()

    async def test_cx035_cx041_reviewer_schema_and_read_only_sandbox(self):
        """Budget: at most one Luna/low reviewer turn."""
        runtime = AppServerRuntime(repository_root=str(self.root))
        forbidden = self.root / "forbidden.txt"
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["WRITE_BLOCKED"]}},
            "required": ["status"],
            "additionalProperties": False,
        }
        try:
            session = await runtime.start_session(
                role=SessionRole.REVIEWER,
                profile="taskledger_reviewer",
                subject_id="live-contract-review",
                cwd=str(self.root),
                writable=False,
            )
            config = runtime.session_config[session.thread_id]
            self.assertEqual(config["model"], LIVE_MODEL)
            self.assertEqual(config["effort"], LIVE_EFFORT)
            self.assertEqual(config["sandboxPolicy"]["type"], "readOnly")
            handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt=(
                    "Use a shell command to attempt to create forbidden.txt in the current directory. "
                    "The read-only sandbox should deny it. Return status WRITE_BLOCKED after the write fails."
                ),
                output_schema=schema,
            )
            result = await runtime.wait_turn(handle)
            self.assertEqual(result.structured_output, {"status": "WRITE_BLOCKED"})
            self.assertFalse(forbidden.exists())
            self.assertGreater(result.usage.total_tokens, 0)
            report_usage("reviewer-schema-read-only", "REVIEWER", result)
        finally:
            await runtime.close()

    async def test_cx012_cx115_cx116_thread_resume_after_client_restart(self):
        """Budget: at most two Luna/low worker turns across two clients."""
        first_runtime = AppServerRuntime(repository_root=str(self.root))
        session = await first_runtime.start_session(
            role=SessionRole.WORKER,
            profile="routine",
            subject_id="live-restart-worker",
            cwd=str(self.root),
            writable=True,
        )
        try:
            handle = await first_runtime.start_turn(
                thread_id=session.thread_id,
                prompt="Remember the nonce COBALT. Reply with exactly STORED.",
            )
            result = await first_runtime.wait_turn(handle)
            self.assertEqual((result.final_response or "").strip(), "STORED")
            first_total = result.cumulative_after
            first_turn_id = result.handle.turn_id
            self.assertIsNotNone(first_total)
            report_usage("restart-before", "WORKER", result)
        finally:
            await first_runtime.close()

        second_runtime = AppServerRuntime(repository_root=str(self.root))
        try:
            resumed = await second_runtime.resume_session(
                thread_id=session.thread_id,
                role=SessionRole.WORKER,
                profile="routine",
                subject_id="live-restart-worker",
                cwd=str(self.root),
                writable=True,
                cumulative_usage_baseline=first_total,
            )
            self.assertEqual(resumed.thread_id, session.thread_id)
            handle = await second_runtime.start_turn(
                thread_id=session.thread_id,
                prompt="Reply only with the nonce I asked you to remember.",
            )
            result = await second_runtime.wait_turn(handle)
            self.assertEqual((result.final_response or "").strip(), "COBALT")
            report_usage("restart-after", "WORKER", result)
            self.assertEqual(result.cumulative_before, first_total)
            self.assertEqual(result.cumulative_after, first_total + result.usage)
            history = await second_runtime.provider_history(session.thread_id)
            ids = {turn["id"] for turn in history["turns"]}
            self.assertIn(first_turn_id, ids)
            self.assertIn(result.handle.turn_id, ids)
        finally:
            await second_runtime.close()

    async def test_cx112_project_local_native_profile_override(self):
        profile_path = self.root / ".codex" / "agents" / "taskledger-worker-routine.toml"
        profile_path.write_text(
            'name="Disposable Routine"\n'
            'description="live native role"\n'
            'nickname_candidates=["Ledger"]\n'
            f'model="{LIVE_MODEL}"\nmodel_reasoning_effort="{LIVE_EFFORT}"\n'
            'developer_instructions="Reply concisely when directly asked."\nmodel_verbosity="low"\n'
        )
        runtime = AppServerRuntime(repository_root=str(self.root))
        try:
            session = await runtime.start_session(
                role=SessionRole.WORKER, profile="routine", subject_id="cx112", cwd=str(self.root), writable=True,
            )
            identity = session.identity
            self.assertEqual(identity.profile_source_kind, "PROJECT")
            self.assertEqual(identity.profile_source_file, ".codex/agents/taskledger-worker-routine.toml")
            self.assertEqual(identity.profile_role_name, "Disposable Routine")
            self.assertEqual((identity.model, identity.effort), (LIVE_MODEL, LIVE_EFFORT))
            self.assertEqual(len(identity.profile_hash), 64)
            config = runtime.session_config[session.thread_id]
            self.assertEqual(config["developerInstructions"], "Reply concisely when directly asked.")
            self.assertEqual(config["nativeConfig"]["model_verbosity"], "low")
        finally:
            await runtime.close()

    async def test_cx113_nested_agents_are_unavailable(self):
        runtime = AppServerRuntime(repository_root=str(self.root))
        try:
            session = await runtime.start_session(
                role=SessionRole.WORKER, profile="routine", subject_id="cx113", cwd=str(self.root), writable=True,
            )
            handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt=(
                    "Attempt to delegate the words CHILD CHECK to a native Codex subagent or collaboration agent. "
                    "If no such tool is available, reply exactly NESTED_AGENT_UNAVAILABLE. Do no other work."
                ),
            )
            result = await runtime.wait_turn(handle)
            self.assertIn("NESTED_AGENT_UNAVAILABLE", result.final_response or "")
            history = await runtime.provider_history(session.thread_id)
            kinds = [entry.get("item", {}).get("type") for entry in history["items"]]
            self.assertNotIn("collabAgentToolCall", kinds)
            self.assertNotIn("subAgentActivity", kinds)
            report_usage("nested-agents-unavailable", "WORKER", result)
        finally:
            await runtime.close()

    async def test_cx114_one_turn_multiple_model_responses(self):
        runtime = AppServerRuntime(repository_root=str(self.root), experimental_raw_events=True)
        try:
            session = await runtime.start_session(
                role=SessionRole.WORKER, profile="routine", subject_id="cx114", cwd=str(self.root), writable=True,
            )
            handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt="First run the shell command `pwd`. After reading its output, reply exactly MULTI_RESPONSE_OK.",
            )
            result = await runtime.wait_turn(handle)
            self.assertEqual((result.final_response or "").strip(), "MULTI_RESPONSE_OK")
            self.assertGreaterEqual(result.exact_response_count, 2)
            self.assertEqual(result.usage_precision, UsagePrecision.EXACT_RESPONSES)
            self.assertEqual(result.cumulative_after, result.cumulative_before + result.usage)
            history = await runtime.provider_history(session.thread_id)
            self.assertIn("commandExecution", [entry.get("item", {}).get("type") for entry in history["items"]])
            report_usage("multi-response-cumulative-delta", "WORKER", result)
        finally:
            await runtime.close()

    async def test_live_repeated_tool_guard_stops_one_turn(self):
        """Budget: one Luna/low turn intentionally stopped on the fourth identical call."""
        calls = []
        def context_handler(action, data):
            calls.append((action, data))
            return {"assignment": "disposable", "instruction": "continue the requested repetition"}

        runtime = AppServerRuntime(
            repository_root=str(self.root), worker_tools={"live-loop-worker": context_handler},
        )
        try:
            session = await runtime.start_session(
                role=SessionRole.WORKER,
                profile="routine",
                subject_id="live-loop-worker",
                cwd=str(self.root),
                writable=True,
            )
            handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt=(
                    "This is an intentional controller circuit-breaker test. Call taskledger_context "
                    "with its valid empty input ten times in succession. Do not use shell commands, edit "
                    "files, or stop early when a call succeeds."
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "TASKLEDGER_REPEATED_TOOL_CALL_LIMIT"):
                await runtime.wait_turn(handle)
            self.assertLessEqual(len(calls), runtime.MAX_REPEAT_DYNAMIC_TOOL_CALLS_PER_TURN)
            self.assertEqual(
                runtime.turn_interrupt_reasons[handle.turn_id],
                "TASKLEDGER_REPEATED_TOOL_CALL_LIMIT",
            )
            usage = runtime.thread_usage[session.thread_id].subtract(
                runtime.turn_usage_before[handle.turn_id]
            )
            print("TASKLEDGER_LIVE_GUARD " + json.dumps({
                "case": "repeated-tool-call",
                "thread_id": session.thread_id,
                "turn_id": handle.turn_id,
                "handler_calls": len(calls),
                "reason": runtime.turn_interrupt_reasons[handle.turn_id],
                **usage.__dict__,
            }, sort_keys=True))
        finally:
            await runtime.close()

    async def test_live_failed_tool_guard_stops_one_turn(self):
        """Budget: one Luna/low turn intentionally stopped after eight distinct failures."""
        calls = []
        def failing_handler(action, data):
            calls.append((action, data))
            raise LedgerError("DELIBERATE_TEST_FAILURE", "This failure is intentional.")

        runtime = AppServerRuntime(
            repository_root=str(self.root), worker_tools={"live-failure-worker": failing_handler},
        )
        try:
            session = await runtime.start_session(
                role=SessionRole.WORKER,
                profile="routine",
                subject_id="live-failure-worker",
                cwd=str(self.root),
                writable=True,
            )
            commands = ", ".join(f"probe-{number}" for number in range(1, 10))
            handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt=(
                    "This is an intentional controller circuit-breaker test. Call taskledger_check once "
                    f"for each of these distinct command strings, in order: {commands}. Every call will "
                    "fail deliberately; continue to the next string. Do not use shell commands or edit files."
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "TASKLEDGER_FAILED_TOOL_CALL_LIMIT"):
                await runtime.wait_turn(handle)
            self.assertEqual(len(calls), runtime.MAX_FAILED_DYNAMIC_TOOL_CALLS_PER_TURN)
            self.assertEqual(
                runtime.turn_interrupt_reasons[handle.turn_id],
                "TASKLEDGER_FAILED_TOOL_CALL_LIMIT",
            )
            usage = runtime.thread_usage[session.thread_id].subtract(
                runtime.turn_usage_before[handle.turn_id]
            )
            print("TASKLEDGER_LIVE_GUARD " + json.dumps({
                "case": "failed-tool-call",
                "thread_id": session.thread_id,
                "turn_id": handle.turn_id,
                "handler_calls": len(calls),
                "reason": runtime.turn_interrupt_reasons[handle.turn_id],
                **usage.__dict__,
            }, sort_keys=True))
        finally:
            await runtime.close()

    async def test_cx028_cx035_live_assignment_lifecycle(self):
        """Budget: at most three Luna/low worker turns and two reviewer turns."""
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.controller.journal import Journal
        from taskledger.controller.supervisor import Supervisor, SupervisorConfig
        from taskledger.controller.taskledger_adapter import TaskledgerLedgerAdapter
        from taskledger.controller.worker_broker import WorkerBroker, broker_socket_path
        from taskledger.db import connect
        from taskledger.service import Service

        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fixture.setUp()
        runtime = None
        broker = None
        service = None
        try:
            project_id = fixture.init()
            agents = fixture.root / ".codex" / "agents"
            agents.mkdir(parents=True, exist_ok=True)
            profile = f'model = "{LIVE_MODEL}"\nmodel_reasoning_effort = "{LIVE_EFFORT}"\n'
            (agents / "taskledger-worker-routine.toml").write_text(profile)
            (agents / "taskledger-reviewer.toml").write_text(profile)
            fixture.git("add", ".codex/agents")
            fixture.git("commit", "-qm", "configure disposable live agents")
            _, task_id = fixture.setup_task(project_id, objective="Create README containing exactly live-controller-ok")
            fixture.command("plan", "validate", {}, project=project_id)
            _, created = fixture.command(
                "assignment", "create", {"task_id": task_id, "worker_profile": "routine"}, project=project_id
            )
            assignment_id = created["data"]["assignment_id"]
            worker_token = fixture.assignment_token(created)

            service = Service(connect(fixture.home), fixture.home)
            project, orchestrator = service.auth_orchestrator(project_id, None)
            worker = service.authenticate(None, worker_token, "WORKER")
            socket_path = broker_socket_path(fixture.home, assignment_id)
            broker = WorkerBroker(service, worker, socket_path)
            runtime = AppServerRuntime(
                repository_root=str(fixture.root), worker_tools={assignment_id: broker.dispatch}
            )
            adapter = TaskledgerLedgerAdapter(service, project, orchestrator, worker_tool_enabled=True)
            config = SupervisorConfig(
                max_worker_turns=3,
                max_reviewer_turns_per_submission=2,
                max_total_tokens=350_000,
                max_elapsed_seconds=300,
                turn_timeout_seconds=120,
            )
            journal = Journal(service.con, fixture.home)
            run_id = journal.create_run(
                project_id,
                mode="ASSIGNMENT",
                config={"assignment_id": assignment_id, "limits": config.__dict__},
            )
            result = await Supervisor(
                project_id=project_id,
                run_id=run_id,
                ledger=adapter,
                runtime=runtime,
                journal=journal,
                config=config,
            ).run_assignment(assignment_id)
            if result.status.value != "INTEGRATED":
                task_state = service.con.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                submissions = [dict(row) for row in service.con.execute(
                    "SELECT sequence,state,summary FROM submissions WHERE assignment_id=? ORDER BY sequence", (assignment_id,)
                )]
                turns = [dict(row) for row in service.con.execute(
                    "SELECT sequence,state,prompt_kind,error,final_response,progressed FROM controller_turns "
                    "WHERE session_id IN (SELECT id FROM controller_sessions WHERE run_id=?) ORDER BY started_at",
                    (run_id,),
                )]
                for row in service.con.execute(
                    "SELECT s.role,s.profile,s.runtime_identity_json,t.external_turn_id,t.input_tokens,t.cached_input_tokens,t.cache_write_input_tokens,t.output_tokens,t.reasoning_tokens,t.usage_precision,t.exact_response_count,t.usage_missing "
                    "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
                    (run_id,),
                ):
                    identity = json.loads(row["runtime_identity_json"])
                    print("TASKLEDGER_LIVE_USAGE " + json.dumps({
                        "case": "live-assignment-lifecycle-failed", "role": row["role"], "profile": row["profile"],
                        "model": identity["model"], "effort": identity["effort"], "turn_id": row["external_turn_id"],
                        "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                        "cache_write_input_tokens": row["cache_write_input_tokens"],
                        "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"],
                        "usage_precision": row["usage_precision"], "exact_response_count": row["exact_response_count"],
                        "usage_missing": bool(row["usage_missing"]),
                    }, sort_keys=True))
                self.fail(repr({"result": result, "task_state": task_state, "submissions": submissions, "turns": turns}))
            self.assertLessEqual(result.worker_turns, 3)
            self.assertLessEqual(result.reviewer_turns, 2)
            self.assertEqual((fixture.root / "README").read_text().strip(), "live-controller-ok")
            for row in service.con.execute(
                "SELECT s.role,s.profile,s.runtime_identity_json,t.external_turn_id,t.input_tokens,t.cached_input_tokens,t.cache_write_input_tokens,t.output_tokens,t.reasoning_tokens,t.usage_precision,t.exact_response_count,t.usage_missing "
                "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
                (run_id,),
            ):
                identity = json.loads(row["runtime_identity_json"])
                print("TASKLEDGER_LIVE_USAGE " + json.dumps({
                    "case": "live-assignment-lifecycle", "role": row["role"], "profile": row["profile"],
                    "model": identity["model"], "effort": identity["effort"], "turn_id": row["external_turn_id"],
                    "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                    "cache_write_input_tokens": row["cache_write_input_tokens"],
                    "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"],
                    "usage_precision": row["usage_precision"], "exact_response_count": row["exact_response_count"],
                    "usage_missing": bool(row["usage_missing"]),
                }, sort_keys=True))
        finally:
            if runtime is not None:
                await runtime.close()
            if broker is not None:
                await broker.close()
            if service is not None:
                service.con.close()
            fixture.tearDown()

    async def test_r3_live_project_automatic_same_thread_continuation(self):
        """Budget: three worker and three reviewer turns; six total."""
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.controller.journal import Journal
        from taskledger.controller.project import ProjectController, ProjectControllerConfig, validate_execution_policy
        from taskledger.controller.supervisor import SupervisorConfig
        from taskledger.db import connect
        from taskledger.service import Service

        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fixture.setUp()
        runtime = None
        underlying_runtime = None
        evidence = None
        run_id = None
        service = None
        try:
            project_id = fixture.init()
            agents = fixture.root / ".codex" / "agents"
            agents.mkdir(parents=True, exist_ok=True)
            profile = f'model = "{LIVE_MODEL}"\nmodel_reasoning_effort = "{LIVE_EFFORT}"\n'
            (agents / "taskledger-worker-routine.toml").write_text(profile)
            (agents / "taskledger-worker-complex.toml").write_text(
                profile
                + "developer_instructions = "
                + json.dumps(
                    "This profile is used only by disposable task A. On your first turn, create and commit "
                    "a.txt with meaningful but intentionally incomplete draft content, then end the turn "
                    "normally without submitting and without opening a blocker. On the next turn, replace "
                    "a.txt so it contains exactly alpha, commit it, and submit through the provided "
                    "assignment-scoped Taskledger worker tool. Do not perform both stages in one turn."
                )
                + "\n"
            )
            for name in ("taskledger-reviewer.toml", "taskledger-task-creator.toml"):
                (agents / name).write_text(profile)
            fixture.git("add", ".codex/agents")
            fixture.git("commit", "-qm", "configure disposable project agents")
            _, registered = fixture.command("spec", "register", {"relative_path": "spec.md"}, project=project_id)
            specification_id = registered["data"]["specification_id"]
            _, applied = fixture.command("plan", "apply", {
                "requirements": [
                    {"ref": "r-a", "statement": "Create a.txt", "details": "a.txt contains exactly alpha", "implementation_required": True, "sources": [{"specification_id": specification_id, "locator": "1"}]},
                    {"ref": "r-b", "statement": "Create b.txt", "details": "b.txt contains exactly beta", "implementation_required": True, "sources": [{"specification_id": specification_id, "locator": "1"}]},
                ],
                "tasks": [
                    {"ref": "t-a", "objective": "Create a.txt containing exactly alpha", "implementation_scope": "a.txt", "acceptance_criteria": ["a.txt contains exactly alpha"], "required_checks": [], "requirement_refs": ["r-a"], "requirement_ids": [], "dependency_refs": [], "dependency_task_ids": []},
                    {"ref": "t-b", "objective": "Create b.txt containing exactly beta", "implementation_scope": "b.txt", "acceptance_criteria": ["b.txt contains exactly beta"], "required_checks": [], "requirement_refs": ["r-b"], "requirement_ids": [], "dependency_refs": [], "dependency_task_ids": []},
                ],
            }, project=project_id)
            fixture.command("plan", "validate", {}, project=project_id)

            service = Service(connect(fixture.home), fixture.home)
            project, orchestrator = service.auth_orchestrator(project_id, None)
            task_ids = applied["data"]["task_ids"]
            targets = validate_execution_policy(service, project, [
                {"task_id": task_ids["t-a"], "wave": 1, "worker_profile": "complex", "parallel_safe": True, "write_surfaces": ["a.txt"]},
                {"task_id": task_ids["t-b"], "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["b.txt"]},
            ])
            config = ProjectControllerConfig(
                max_workers=2,
                max_reviewers=1,
                max_total_worker_turns=3,
                max_total_reviewer_turns=3,
                reviewer_token_reserve=150_000,
                max_final_reviewer_turns=1,
                max_task_creator_turns=1,
                supervisor=SupervisorConfig(
                    max_worker_turns=2,
                    max_reviewer_turns_per_submission=1,
                    max_total_tokens=600_000,
                    max_elapsed_seconds=300,
                    turn_timeout_seconds=120,
                ),
            )
            journal = Journal(service.con, fixture.home)
            underlying_runtime = AppServerRuntime(repository_root=str(fixture.root), worker_tools={})
            _, plan_fingerprint, _ = service.plan_current(project_id)
            manifest = {
                "canonical_starting_oid": subprocess.run(
                    ["git", "-C", str(fixture.root), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip(),
                "starting_plan_fingerprint": plan_fingerprint,
                "execution_policy_hash": hashlib.sha256(json.dumps(targets, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "controller_configuration_hash": hashlib.sha256(json.dumps(config.as_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "taskledger_schema_version": 7,
                "codex_protocol_identity": underlying_runtime.protocol_identity,
                "scope": "POST_APPROVAL_EXECUTION",
            }
            evidence_dir = Path(os.environ.get(
                "TASKLEDGER_LIVE_EVIDENCE_DIR",
                str(Path(__file__).parents[1] / ".taskledger-validation-evidence" / "benchmark-readiness"),
            ))
            evidence = LiveEvidence(
                directory=evidence_dir,
                validation_id="r3-live-controller-continuation",
                source_root=Path(__file__).parents[1],
                model=LIVE_MODEL,
                effort=LIVE_EFFORT,
                configuration={
                    "limits": config.as_dict(),
                    "execution_policy_hash": manifest["execution_policy_hash"],
                    "controller_configuration_hash": manifest["controller_configuration_hash"],
                    "protocol_identity": underlying_runtime.protocol_identity,
                    "global_validation_cap": {
                        "paid_turns": 14,
                        "admission_tokens": 1_200_000,
                        "elapsed_seconds": 600,
                        "corrected_reruns": 1,
                    },
                },
            )
            first_a_boundary = {}

            def observe_result(turn_result, runtime_subject):
                if runtime_subject.get("role") != "WORKER":
                    return
                assignment = service.con.execute(
                    "SELECT * FROM assignments WHERE id=?", (runtime_subject.get("subject_id"),)
                ).fetchone()
                if not assignment or assignment["task_id"] != task_ids["t-a"] or first_a_boundary:
                    return
                worktree = Path(assignment["worktree_path"])
                first_a_boundary["provider_completed_normally"] = True
                try:
                    first_a_boundary.update({
                        "assignment_state": assignment["state"],
                        "task_state": service.con.execute(
                            "SELECT state FROM tasks WHERE id=?", (assignment["task_id"],)
                        ).fetchone()[0],
                        "file_content": (worktree / "a.txt").read_text() if (worktree / "a.txt").is_file() else None,
                        "head_oid": subprocess.run(
                            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True,
                        ).stdout.strip(),
                        "base_oid": assignment["base_commit_oid"],
                        "submission_count": service.con.execute(
                            "SELECT COUNT(*) FROM submissions WHERE assignment_id=?", (assignment["id"],)
                        ).fetchone()[0],
                        "blocker_count": service.con.execute(
                            "SELECT COUNT(*) FROM blockers WHERE scope_type='ASSIGNMENT' AND scope_id=? AND state='OPEN'",
                            (assignment["id"],),
                        ).fetchone()[0],
                    })
                except Exception as exc:
                    first_a_boundary["observer_error"] = f"{type(exc).__name__}: {exc}"

            runtime = EvidenceRuntime(underlying_runtime, evidence, on_result=observe_result)
            run_id = journal.create_project_run(project_id, config={"limits": config.as_dict(), "manifest": manifest}, targets=targets)
            print("TASKLEDGER_PAID_VALIDATION_LIMITS " + json.dumps({
                "method": "test_r3_live_project_automatic_same_thread_continuation",
                "model": LIVE_MODEL,
                "effort": LIVE_EFFORT,
                "per_attempt": {"worker_turns": 3, "reviewer_turns": 3, "total_turns": 6, "admission_tokens": 600_000, "elapsed_seconds": 300},
                "global": {"paid_turns": 14, "admission_tokens": 1_200_000, "elapsed_seconds": 600, "corrected_reruns": 1},
            }, sort_keys=True))
            result = await ProjectController(
                service=service, project=project, orchestrator=orchestrator, run_id=run_id,
                runtime=runtime, journal=journal, config=config,
            ).run()
            await evidence.finalize(
                con=service.con,
                run_id=run_id,
                outcome=result.status,
                provider_loader=underlying_runtime.provider_history,
            )
            self.assertEqual(result.status, "COMPLETED", result)
            self.assertEqual((fixture.root / "a.txt").read_text().strip(), "alpha")
            self.assertEqual((fixture.root / "b.txt").read_text().strip(), "beta")
            self.assertEqual(service.con.execute(
                "SELECT lifecycle FROM projects WHERE id=?", (project_id,)
            ).fetchone()[0], "COMPLETED")

            assignment_a = service.con.execute(
                "SELECT id FROM assignments WHERE task_id=?", (task_ids["t-a"],)
            ).fetchone()[0]
            worker_session_a = service.con.execute(
                "SELECT id,external_thread_id FROM controller_sessions WHERE run_id=? AND role='WORKER' AND subject_id=?",
                (run_id, assignment_a),
            ).fetchone()
            worker_turns_a = list(service.con.execute(
                "SELECT * FROM controller_turns WHERE session_id=? ORDER BY sequence", (worker_session_a["id"],),
            ))
            self.assertIn(len(worker_turns_a), {1, 2})
            self.assertEqual([row["state"] for row in worker_turns_a], ["COMPLETED"] * len(worker_turns_a))
            self.assertEqual(worker_turns_a[0]["dispatch_reason"], "INITIAL_WORK")
            if len(worker_turns_a) == 2:
                self.assertEqual(worker_turns_a[1]["dispatch_reason"], "ACTIVE_CONTINUATION")
            self.assertEqual(len({row["external_turn_id"] for row in worker_turns_a}), len(worker_turns_a))
            self.assertGreater(worker_turns_a[0]["static_assignment_bytes"], 0)
            self.assertEqual(first_a_boundary["provider_completed_normally"], True)
            self.assertNotIn("observer_error", first_a_boundary, first_a_boundary)
            self.assertNotEqual(first_a_boundary["head_oid"], first_a_boundary["base_oid"])
            self.assertEqual(first_a_boundary["blocker_count"], 0)
            if len(worker_turns_a) == 2:
                self.assertEqual(worker_turns_a[1]["static_assignment_bytes"], 0)
                self.assertLess(worker_turns_a[1]["controller_payload_bytes"], worker_turns_a[0]["controller_payload_bytes"])
                self.assertEqual(first_a_boundary["assignment_state"], "ACTIVE")
                self.assertEqual(first_a_boundary["task_state"], "ASSIGNED")
                self.assertNotEqual((first_a_boundary["file_content"] or "").strip(), "alpha")
                self.assertEqual(first_a_boundary["submission_count"], 0)
            else:
                # A weak live model may validly finish the task despite the profile's request to
                # stop after a draft. That exercises the terminal-submit path rather than continuation.
                self.assertEqual(first_a_boundary["assignment_state"], "ACTIVE")
                self.assertEqual(first_a_boundary["task_state"], "SUBMITTED")
                self.assertEqual((first_a_boundary["file_content"] or "").strip(), "alpha")
                self.assertEqual(first_a_boundary["submission_count"], 1)
            worker_turns = list(service.con.execute(
                "SELECT t.started_at,t.finished_at,s.subject_id FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id "
                "WHERE s.run_id=? AND s.role='WORKER' ORDER BY t.started_at", (run_id,),
            ))
            self.assertEqual(len(worker_turns), 1 + len(worker_turns_a))
            reviewer_turns = list(service.con.execute(
                "SELECT t.started_at,t.finished_at FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id "
                "WHERE s.run_id=? AND s.role='REVIEWER' ORDER BY t.started_at", (run_id,),
            ))
            self.assertEqual(len(reviewer_turns), 2)
            self.assertGreaterEqual(reviewer_turns[1]["started_at"], reviewer_turns[0]["finished_at"])
            natural_overlap = any(
                worker["started_at"] < reviewer["finished_at"] and reviewer["started_at"] < worker["finished_at"]
                for worker in worker_turns for reviewer in reviewer_turns
            )
            print("TASKLEDGER_LIVE_CONCURRENCY_DIAGNOSTIC " + json.dumps({
                "natural_worker_reviewer_overlap_observed": natural_overlap,
                "timing_dependent_gate": False,
            }, sort_keys=True))

            rows = list(service.con.execute(
                "SELECT s.role,s.profile,s.runtime_identity_json,t.external_turn_id,t.input_tokens,t.cached_input_tokens,t.cache_write_input_tokens,t.output_tokens,t.reasoning_tokens,t.usage_precision,t.exact_response_count,t.usage_missing "
                "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
                (run_id,),
            ))
            self.assertEqual(len(rows), 4 + len(worker_turns_a))
            self.assertFalse(any(row["usage_missing"] for row in rows))
            reviewer_identities = [
                json.loads(row["runtime_identity_json"])
                for row in service.con.execute(
                    "SELECT runtime_identity_json FROM controller_sessions "
                    "WHERE run_id=? AND role IN ('REVIEWER','REQUIREMENT_REVIEWER') ORDER BY role,id",
                    (run_id,),
                )
            ]
            self.assertEqual(len({identity["profile_hash"] for identity in reviewer_identities}), 1)
            self.assertEqual(len({identity["profile_name"] for identity in reviewer_identities}), 1)
            for row in rows:
                identity = json.loads(row["runtime_identity_json"])
                print("TASKLEDGER_LIVE_USAGE " + json.dumps({
                    "case": "r3-live-controller-continuation", "role": row["role"], "profile": row["profile"],
                    "model": identity["model"], "effort": identity["effort"], "turn_id": row["external_turn_id"],
                    "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                    "cache_write_input_tokens": row["cache_write_input_tokens"],
                    "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"],
                    "usage_precision": row["usage_precision"], "exact_response_count": row["exact_response_count"],
                    "usage_missing": bool(row["usage_missing"]),
                }, sort_keys=True))
            async def forbidden_start_turn(**kwargs):
                raise AssertionError("reporting attempted to start a model turn")
            underlying_runtime.start_turn = forbidden_start_turn
            report_one = await controller_report(
                service.con, run_id, include_provider_detail=True, provider_loader=underlying_runtime.provider_history,
            )
            report_two = await controller_report(
                service.con, run_id, include_provider_detail=True, provider_loader=underlying_runtime.provider_history,
            )
            self.assertEqual(report_one, report_two)
            self.assertEqual(report_one["quality"]["provider_detail"]["status"], "AVAILABLE")
            self.assertTrue(report_one["quality"]["provider_detail"]["complete"])
            self.assertEqual(report_one["architecture_invariants"]["nested_agent_calls"], 0)
            self.assertEqual(report_one["architecture_invariants"]["subagent_activity"], 0)
            totals = report_one["economics"]["totals"]
            live_measurement = normalize_measurement({
                "schema_version": SCHEMA_VERSION,
                "outcome": "COMPLETED_VERIFIED",
                "accounting": "COMPLETE",
                "provenance": {"source": "r3 retained live report"},
                "workload_identity": "r3-two-file-continuation-v1",
                "starting_code_identity": manifest["canonical_starting_oid"],
                "review_standard": "independent-submission-and-final-review-v1",
                "measurement_scope": "POST_APPROVAL_EXECUTION",
                "role_configuration_identity": manifest["controller_configuration_hash"],
                "usage": {key: totals[key] for key in (
                    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                    "output_tokens", "reasoning_tokens",
                )},
            })
            synthetic = {
                **live_measurement,
                "outcome": "INCOMPLETE",
                "accounting": "PARTIAL_MISSING",
                "provenance": {"source": "synthetic incomplete comparison fixture"},
            }
            comparison_one = compare_measurements(live_measurement, synthetic)
            comparison_two = compare_measurements(live_measurement, synthetic)
            self.assertEqual(comparison_one, comparison_two)
            self.assertIsNone(comparison_one["winner"])
            self.assertEqual(comparison_one["left"]["outcome"], "COMPLETED_VERIFIED")
            self.assertEqual(comparison_one["left"]["accounting"], "COMPLETE")
            table_names = {row[0] for row in service.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            forbidden_shadow_tables = sorted(table_names.intersection({
                "controller_turn_items", "controller_commands", "controller_file_changes",
                "controller_compactions", "controller_codex_events", "controller_raw_responses",
            }))
            worker_storage = list(service.con.execute(
                "SELECT t.result_json,t.final_response,t.response_hash FROM controller_turns t "
                "JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? AND s.role='WORKER'",
                (run_id,),
            ))
            raw_usage_rows = service.con.execute(
                "SELECT COUNT(*) FROM controller_usage_events e JOIN controller_turns t ON t.id=e.turn_id "
                "JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=?",
                (run_id,),
            ).fetchone()[0]
            self.assertFalse(forbidden_shadow_tables)
            self.assertTrue(all(
                row["result_json"] is None and row["final_response"] is None
                for row in worker_storage
            ))
            self.assertEqual(raw_usage_rows, 0)
            print("TASKLEDGER_STORAGE_AUDIT " + json.dumps({
                "new_append_only_table": "controller_events",
                "provider_shadow_tables": forbidden_shadow_tables,
                "raw_provider_usage_rows": raw_usage_rows,
                "worker_prose_or_result_rows": sum(
                    row["result_json"] is not None or row["final_response"] is not None for row in worker_storage
                ),
                "worker_response_hash_rows": sum(bool(row["response_hash"]) for row in worker_storage),
            }, sort_keys=True))
            print("TASKLEDGER_LIVE_REPORT " + json.dumps({
                "identity": report_one["identity"],
                "scope": report_one["scope"],
                "quality": report_one["quality"],
                "economics": report_one["economics"],
                "scheduler": report_one["scheduler"],
                "architecture_invariants": report_one["architecture_invariants"],
            }, sort_keys=True))
        except BaseException as exc:
            if evidence is not None:
                await evidence.finalize(
                    con=service.con if service is not None else None,
                    run_id=run_id,
                    outcome="FAILED",
                    failure=exc,
                    provider_loader=underlying_runtime.provider_history if underlying_runtime is not None else None,
                )
            raise
        finally:
            if runtime is not None:
                await runtime.close()
            if service is not None:
                service.con.close()
            fixture.tearDown()
            if evidence is not None:
                LiveEvidence.print_rollup(evidence.directory)


if __name__ == "__main__":
    unittest.main()
