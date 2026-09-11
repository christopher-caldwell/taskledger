from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.model import SessionRole


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

    async def test_cx012_thread_resume_after_client_restart(self):
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
            )
            self.assertEqual(resumed.thread_id, session.thread_id)
            handle = await second_runtime.start_turn(
                thread_id=session.thread_id,
                prompt="Reply only with the nonce I asked you to remember.",
            )
            result = await second_runtime.wait_turn(handle)
            self.assertEqual((result.final_response or "").strip(), "COBALT")
            report_usage("restart-after", "WORKER", result)
        finally:
            await second_runtime.close()

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
                    "SELECT s.role,s.profile,s.runtime_identity_json,t.external_turn_id,t.input_tokens,t.cached_input_tokens,t.output_tokens,t.reasoning_tokens,t.usage_missing "
                    "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
                    (run_id,),
                ):
                    identity = json.loads(row["runtime_identity_json"])
                    print("TASKLEDGER_LIVE_USAGE " + json.dumps({
                        "case": "live-assignment-lifecycle-failed", "role": row["role"], "profile": row["profile"],
                        "model": identity["model"], "effort": identity["effort"], "turn_id": row["external_turn_id"],
                        "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                        "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"],
                        "usage_missing": bool(row["usage_missing"]),
                    }, sort_keys=True))
                self.fail(repr({"result": result, "task_state": task_state, "submissions": submissions, "turns": turns}))
            self.assertLessEqual(result.worker_turns, 3)
            self.assertLessEqual(result.reviewer_turns, 2)
            self.assertEqual((fixture.root / "README").read_text().strip(), "live-controller-ok")
            for row in service.con.execute(
                "SELECT s.role,s.profile,s.runtime_identity_json,t.external_turn_id,t.input_tokens,t.cached_input_tokens,t.output_tokens,t.reasoning_tokens,t.usage_missing "
                "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
                (run_id,),
            ):
                identity = json.loads(row["runtime_identity_json"])
                print("TASKLEDGER_LIVE_USAGE " + json.dumps({
                    "case": "live-assignment-lifecycle", "role": row["role"], "profile": row["profile"],
                    "model": identity["model"], "effort": identity["effort"], "turn_id": row["external_turn_id"],
                    "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                    "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"],
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

    async def test_cx099_cx103_live_multi_task_project(self):
        """Budget: four Luna/low worker turns and five total reviewer turns."""
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.controller.journal import Journal
        from taskledger.controller.project import ProjectController, ProjectControllerConfig, validate_execution_policy
        from taskledger.controller.supervisor import SupervisorConfig
        from taskledger.db import connect
        from taskledger.service import Service

        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fixture.setUp()
        runtime = None
        service = None
        try:
            project_id = fixture.init()
            agents = fixture.root / ".codex" / "agents"
            agents.mkdir(parents=True, exist_ok=True)
            profile = f'model = "{LIVE_MODEL}"\nmodel_reasoning_effort = "{LIVE_EFFORT}"\n'
            for name in (
                "taskledger-worker-routine.toml",
                "taskledger-worker-complex.toml",
                "taskledger-reviewer.toml",
                "taskledger-task-creator.toml",
            ):
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
                {"task_id": task_ids["t-a"], "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["a.txt"]},
                {"task_id": task_ids["t-b"], "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["b.txt"]},
            ])
            config = ProjectControllerConfig(
                max_workers=2,
                max_reviewers=1,
                max_total_worker_turns=4,
                max_total_reviewer_turns=5,
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
            run_id = journal.create_project_run(project_id, config={"limits": config.as_dict()}, targets=targets)
            runtime = AppServerRuntime(repository_root=str(fixture.root), worker_tools={})
            result = await ProjectController(
                service=service, project=project, orchestrator=orchestrator, run_id=run_id,
                runtime=runtime, journal=journal, config=config,
            ).run()
            self.assertEqual(result.status, "COMPLETED", result)
            self.assertEqual((fixture.root / "a.txt").read_text().strip(), "alpha")
            self.assertEqual((fixture.root / "b.txt").read_text().strip(), "beta")
            self.assertEqual(service.con.execute(
                "SELECT lifecycle FROM projects WHERE id=?", (project_id,)
            ).fetchone()[0], "COMPLETED")

            worker_turns = list(service.con.execute(
                "SELECT t.started_at,t.finished_at FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id "
                "WHERE s.run_id=? AND s.role='WORKER' ORDER BY t.started_at", (run_id,),
            ))
            self.assertEqual(len(worker_turns), 2)
            self.assertLess(worker_turns[1]["started_at"], worker_turns[0]["finished_at"])
            reviewer_turns = list(service.con.execute(
                "SELECT t.started_at,t.finished_at FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id "
                "WHERE s.run_id=? AND s.role='REVIEWER' ORDER BY t.started_at", (run_id,),
            ))
            self.assertEqual(len(reviewer_turns), 2)
            self.assertGreaterEqual(reviewer_turns[1]["started_at"], reviewer_turns[0]["finished_at"])

            rows = list(service.con.execute(
                "SELECT s.role,s.profile,s.runtime_identity_json,t.external_turn_id,t.input_tokens,t.cached_input_tokens,t.output_tokens,t.reasoning_tokens,t.usage_missing "
                "FROM controller_turns t JOIN controller_sessions s ON s.id=t.session_id WHERE s.run_id=? ORDER BY t.started_at,t.id",
                (run_id,),
            ))
            self.assertEqual(len(rows), 5)
            self.assertFalse(any(row["usage_missing"] for row in rows))
            for row in rows:
                identity = json.loads(row["runtime_identity_json"])
                print("TASKLEDGER_LIVE_USAGE " + json.dumps({
                    "case": "live-multi-task-project", "role": row["role"], "profile": row["profile"],
                    "model": identity["model"], "effort": identity["effort"], "turn_id": row["external_turn_id"],
                    "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
                    "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"],
                    "usage_missing": bool(row["usage_missing"]),
                }, sort_keys=True))
        finally:
            if runtime is not None:
                await runtime.close()
            if service is not None:
                service.con.close()
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
