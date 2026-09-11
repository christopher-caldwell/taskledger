from __future__ import annotations

import asyncio
import os
import signal
import threading
import unittest
from argparse import Namespace
from unittest.mock import patch

from taskledger.controller.fakes import FakeRuntime, TurnScript
from taskledger.controller.initial_planning import (
    InitialPlanner,
    PreparationConfig,
    finish_preparation,
    materialized_plan,
    store_planning_preparation,
    validate_proposal,
)
from taskledger.controller.journal import Journal
from taskledger.controller.reporting import controller_report
from taskledger.db import connect
from taskledger.service import Service
from taskledger.cli import prepare_project_command, run_project_controller, start_prepared_project
from taskledger.controller.project import ProjectControllerResult


def proposal(*, ambiguities=None):
    return {
        "requirements": [{
            "ref": "req-one", "statement": "Observable behavior", "details": "The behavior is visible.",
            "implementation_required": True, "sources": [{"locator": "Section 1", "excerpt": None}],
        }],
        "tasks": [{
            "ref": "task-one", "objective": "Implement behavior", "implementation_scope": "src/example.py",
            "acceptance_criteria": ["Behavior is observable"], "required_checks": ["python3 -m unittest"],
            "requirement_refs": ["req-one"], "dependency_refs": [],
        }],
        "execution_policy": [{
            "task_ref": "task-one", "wave": 1, "worker_profile": "routine",
            "parallel_safe": True, "write_surfaces": ["src/example.py"],
        }],
        "ambiguities": list(ambiguities or []), "assumptions": ["Existing public API remains stable"],
    }


class InitialPlanningTests(unittest.TestCase):
    def setUp(self):
        from tests.test_acceptance import TaskledgerAcceptance
        self.fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        self.fixture.setUp()
        self.project_id = self.fixture.init()
        _, registered = self.fixture.command("spec", "register", {"relative_path": "spec.md"}, project=self.project_id)
        self.spec_id = registered["data"]["specification_id"]
        self.service = Service(connect(self.fixture.home), self.fixture.home)
        self.project, self.principal = self.service.auth_orchestrator(self.project_id, None)
        self.journal = Journal(self.service.con, self.service.home)
        self.config = PreparationConfig(2, {"supervisor": {"turn_timeout_seconds": 5}}, [], [])

    def tearDown(self):
        self.service.con.close()
        self.fixture.tearDown()

    def test_cli_prepare_has_no_parent_conversation_and_returns_approval_hash(self):
        profiles = self.fixture.root / ".codex" / "agents"
        profiles.mkdir(parents=True)
        template = 'name = "test"\nmodel = "fake-model"\nmodel_reasoning_effort = "low"\n'
        for name in ("taskledger-worker-routine.toml", "taskledger-worker-complex.toml"):
            (profiles / name).write_text(template)
        self.fixture.git("add", ".codex")
        self.fixture.git("commit", "-qm", "add profiles")
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
        prior = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            with patch("taskledger.controller.app_server.AppServerRuntime", return_value=runtime):
                result = prepare_project_command(Namespace(project=self.project_id, token=None), {
                    "spec_path": "spec.md", "live": True,
                    "limits": {"max_initial_planner_turns": 1}, "preflight": {"local_inputs": [], "services": []},
                })
        finally:
            os.chdir(prior)
        self.assertEqual(result["status"], "AWAITING_APPROVAL")
        self.assertTrue(result["proposal_hash"].startswith("sha256:"))
        self.assertEqual(runtime.turns_started, 1)
        self.assertTrue(runtime.closed)
        execution_runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
        with patch("taskledger.controller.app_server.AppServerRuntime", return_value=execution_runtime), patch(
            "taskledger.cli.run_project_controller",
            return_value=ProjectControllerResult("COMPLETED", "execution-run"),
        ):
            started = start_prepared_project(self.service, self.project, self.principal, {
                "preparation_id": result["preparation_id"],
                "approve_proposal_hash": result["proposal_hash"], "live": True,
            })
        self.assertEqual(started["status"], "COMPLETED")
        self.assertEqual(self.service.con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        self.assertEqual(self.service.con.execute("SELECT state FROM project_preparations").fetchone()[0], "APPROVED")

    def test_cli_prepare_initializes_an_eligible_repository(self):
        from tests.test_acceptance import TaskledgerAcceptance
        fresh = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fresh.setUp()
        try:
            profiles = fresh.root / ".codex" / "agents"
            profiles.mkdir(parents=True)
            template = 'name = "test"\nmodel = "fake-model"\nmodel_reasoning_effort = "low"\n'
            for name in ("taskledger-worker-routine.toml", "taskledger-worker-complex.toml"):
                (profiles / name).write_text(template)
            fresh.git("add", ".codex")
            fresh.git("commit", "-qm", "add profiles")
            runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
            prior = os.getcwd()
            try:
                os.chdir(fresh.root)
                with patch("taskledger.controller.app_server.AppServerRuntime", return_value=runtime):
                    result = prepare_project_command(Namespace(project=None, token=None), {
                        "spec_path": "spec.md", "live": True,
                        "limits": {"max_initial_planner_turns": 1},
                        "preflight": {"local_inputs": [], "services": []},
                    })
            finally:
                os.chdir(prior)
            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            self.assertTrue((fresh.home / "taskledger.sqlite3").is_file())
            self.assertTrue((fresh.home / "credentials").is_dir())
        finally:
            fresh.tearDown()

    def test_bounded_planner_persists_immutable_proposal_and_materializes(self):
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
        run_id = self.journal.create_run(self.project_id, mode="PREPARATION", config={"manifest": {"scope": "PREPARATION"}})
        revision = self.service.con.execute(
            "SELECT r.* FROM specifications s JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.id=?",
            (self.spec_id,),
        ).fetchone()
        preparation_id = store_planning_preparation(
            self.service, self.project, run_id=run_id, starting_oid="0" * 40, spec_id=self.spec_id,
            spec_hash=revision["content_hash"], profile_hashes={"fake": True}, config=self.config,
        )
        planned, turn_id = asyncio.run(InitialPlanner(
            service=self.service, project=self.project, journal=self.journal, runtime=runtime,
            run_id=run_id, preparation_id=preparation_id, spec_path="spec.md", config=self.config,
        ).run())
        result = finish_preparation(self.service, preparation_id, planned, turn_id)
        self.assertEqual(result["status"], "AWAITING_APPROVAL")
        self.assertTrue(result["proposal_hash"].startswith("sha256:"))
        applied = self.service.apply_plan(self.project, self.principal, materialized_plan({
            "proposal": planned, "specification_id": self.spec_id,
        }))
        self.assertEqual(applied["tasks_created"], 1)
        self.assertTrue(self.service.validate_plan(self.project, self.principal)["valid"])
        self.assertEqual(runtime.sessions_started, 1)
        self.assertEqual(runtime.turns_started, 1)

    def test_ambiguity_is_persisted_as_failed_preparation(self):
        value = proposal(ambiguities=["Choose retention period"])
        self.assertIsNotNone(validate_proposal(value))
        run_id = self.journal.create_run(self.project_id, mode="PREPARATION", config={})
        revision = self.service.con.execute(
            "SELECT r.* FROM specifications s JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.id=?", (self.spec_id,)
        ).fetchone()
        preparation_id = store_planning_preparation(
            self.service, self.project, run_id=run_id, starting_oid="0" * 40, spec_id=self.spec_id,
            spec_hash=revision["content_hash"], profile_hashes={}, config=self.config,
        )
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=value)])
        planned, turn_id = asyncio.run(InitialPlanner(
            service=self.service, project=self.project, journal=self.journal, runtime=runtime,
            run_id=run_id, preparation_id=preparation_id, spec_path="spec.md", config=self.config,
        ).run())
        result = finish_preparation(self.service, preparation_id, planned, turn_id)
        self.assertEqual(result["status"], "FAILED")

    def test_open_acknowledged_turn_is_incomplete_in_reports(self):
        run_id = self.journal.create_run(self.project_id, mode="PROJECT", config={"manifest": {"scope": "POST_APPROVAL_EXECUTION"}})
        session = self.journal.create_session(
            run_id=run_id, project_id=self.project_id, role="WORKER", profile="routine", subject_id="task",
            external_thread_id="thread", config_hash="hash", runtime_identity={},
        )
        local_id = self.journal.begin_turn(session.id, "worker")
        from taskledger.controller.model import RuntimeTurnHandle
        self.journal.acknowledge_turn(local_id, RuntimeTurnHandle("thread", "provider-turn"))
        usage = self.journal.usage_report(run_id=run_id)
        self.assertFalse(usage["complete"])
        self.assertEqual(usage["accounting_status"], "UNRECONCILED_ACTIVE_TURN")
        self.assertEqual(usage["unresolved_turn_count"], 1)
        report = asyncio.run(controller_report(self.service.con, run_id, include_provider_detail=False))
        self.assertFalse(report["quality"]["accounting_complete"])
        self.assertEqual(report["quality"]["known_token_subtotal"], 0)
        self.assertEqual(report["quality"]["unresolved_turn_count"], 1)
        self.assertIsNone(report["scheduler"]["gross_elapsed_ms"])
        self.assertEqual(report["scheduler"]["timing_status"], "OPEN_RUN_END_UNKNOWN")

    def test_sigint_interrupts_acknowledged_turn_and_pauses_run(self):
        run_id = self.journal.create_run(self.project_id, mode="PROJECT", config={})
        session = self.journal.create_session(
            run_id=run_id, project_id=self.project_id, role="WORKER", profile="routine", subject_id="task",
            external_thread_id="thread", config_hash="hash", runtime_identity={},
        )
        local_id = self.journal.begin_turn(session.id, "worker")
        from taskledger.controller.model import RuntimeTurnHandle
        self.journal.acknowledge_turn(local_id, RuntimeTurnHandle("thread", "turn"))
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])

        class HangingController:
            def __init__(self, **kwargs):
                pass

            async def run(self):
                await asyncio.Event().wait()

        timer = threading.Timer(0.2, os.kill, args=(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            with patch("taskledger.controller.project.ProjectController", HangingController):
                result = run_project_controller(
                    self.service, self.project, self.principal, run_id, object(), runtime
                )
        finally:
            timer.cancel()
        self.assertEqual(result.status, "PAUSED")
        self.assertEqual(result.pause_reason, "USER_INTERRUPTED")
        self.assertEqual(self.journal.run(run_id)["state"], "PAUSED")
        self.assertEqual(len(runtime.interrupted), 1)
        self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
