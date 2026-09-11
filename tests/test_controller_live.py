from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.model import SessionRole


LIVE = os.environ.get("TASKLEDGER_LIVE_CODEX") == "1"
LIVE_MODEL = os.environ.get("TASKLEDGER_LIVE_MODEL", "gpt-5.6-luna")
LIVE_EFFORT = os.environ.get("TASKLEDGER_LIVE_EFFORT", "low")


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

    async def test_cx001_cx008_cx009_cx010_cx082_worker_thread_contract(self):
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

            first_handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt="Reply with exactly the single word ALPHA and do nothing else.",
            )
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
            second_handle = await runtime.start_turn(
                thread_id=session.thread_id,
                prompt="Reply only with the exact word you returned in your previous response.",
            )
            second = await runtime.wait_turn(second_handle)
            self.assertEqual((second.final_response or "").strip(), "ALPHA")
            self.assertGreater(first.usage.total_tokens + second.usage.total_tokens, 0)
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
        finally:
            await runtime.close()

    async def test_cx011_cx012_thread_resume_after_client_restart(self):
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
            )
            self.assertEqual(resumed.thread_id, session.thread_id)
            handle = await second_runtime.start_turn(
                thread_id=session.thread_id,
                prompt="Reply only with the nonce I asked you to remember.",
            )
            result = await second_runtime.wait_turn(handle)
            self.assertEqual((result.final_response or "").strip(), "COBALT")
        finally:
            await second_runtime.close()

    async def test_cx028_cx035_cx049_cx094_live_assignment_lifecycle(self):
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
                max_total_tokens=150_000,
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
                self.fail(repr({"result": result, "task_state": task_state, "submissions": submissions, "turns": turns}))
            self.assertLessEqual(result.worker_turns, 3)
            self.assertLessEqual(result.reviewer_turns, 2)
            self.assertEqual((fixture.root / "README").read_text().strip(), "live-controller-ok")
        finally:
            if runtime is not None:
                await runtime.close()
            if broker is not None:
                await broker.close()
            if service is not None:
                service.con.close()
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
