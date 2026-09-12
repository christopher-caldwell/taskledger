from __future__ import annotations

import asyncio
import os
import signal
import threading
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from taskledger.controller.fakes import FakeRuntime, TurnScript
from taskledger.controller.model import RuntimeTurnHandle, RuntimeTurnResult, Usage, UsagePrecision
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

    def enable_profiles(self):
        profiles = self.fixture.root / ".codex" / "agents"
        profiles.mkdir(parents=True, exist_ok=True)
        template = 'name = "test"\nmodel = "fake-model"\nmodel_reasoning_effort = "low"\n'
        for name in ("taskledger-worker-routine.toml", "taskledger-worker-complex.toml"):
            (profiles / name).write_text(template)
        self.fixture.git("add", ".codex")
        self.fixture.git("commit", "-qm", "add profiles")

    def test_cli_prepare_has_no_parent_conversation_and_returns_approval_hash(self):
        self.enable_profiles()
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

    def test_engine_host_adopts_repository_after_explicit_first_preparation(self):
        from tests.test_acceptance import TaskledgerAcceptance
        from taskledger.application.host import EngineHost
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
            host = EngineHost(str(fresh.root), runtime_factory=lambda **_: runtime)
            self.assertEqual(host.current_snapshot().project.get("effective_phase"), "UNINITIALIZED")
            self.assertFalse(fresh.home.exists())

            async def scenario():
                receipt = await host.prepare_project("first-prepare", spec_path="spec.md", live=True,
                    limits={"max_initial_planner_turns": 1}, preflight={"local_inputs": [], "services": []})
                self.assertEqual(receipt.disposition, "ACCEPTED")
                for _ in range(300):
                    status = await host.operation_status("first-prepare")
                    if status.phase != "ACCEPTED":
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(status.phase, "SUCCEEDED", status.error)
                self.assertNotEqual(host.current_snapshot().project.get("effective_phase"), "UNINITIALIZED")
            try:
                asyncio.run(scenario())
            finally:
                asyncio.run(host.close())
            self.assertTrue((fresh.home / "taskledger.sqlite3").is_file())
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
            run_id=run_id, preparation_id=preparation_id, spec_path="spec.md",
            specification_bytes=revision["content_bytes"], specification_hash=revision["content_hash"], config=self.config,
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

    def test_pause_latch_blocks_initial_planner_provider_admission(self):
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
        run_id = self.journal.create_run(self.project_id, mode="PREPARATION", config={})
        revision = self.service.con.execute(
            "SELECT r.* FROM specifications s JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.id=?",
            (self.spec_id,),
        ).fetchone()
        preparation_id = store_planning_preparation(
            self.service, self.project, run_id=run_id, starting_oid="0" * 40,
            spec_id=self.spec_id, spec_hash=revision["content_hash"], profile_hashes={}, config=self.config,
        )
        with self.assertRaisesRegex(Exception, "paused before provider (session start|dispatch)"):
            asyncio.run(InitialPlanner(
                service=self.service, project=self.project, journal=self.journal, runtime=runtime,
                run_id=run_id, preparation_id=preparation_id, spec_path="spec.md",
                specification_bytes=revision["content_bytes"], specification_hash=revision["content_hash"],
                config=self.config, stop_requested=lambda: True,
            ).run())
        self.assertEqual(runtime.turns_started, 0)

    def test_engine_host_preparation_is_accepted_and_strongly_owned(self):
        from taskledger.application.host import EngineHost
        self.enable_profiles()
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
        host = EngineHost(str(self.fixture.root), project_id=self.project_id, runtime_factory=lambda **_: runtime)

        async def scenario():
            receipt = await host.prepare_project("prepare-1", spec_path="spec.md", live=True,
                limits={"max_initial_planner_turns": 1}, preflight={"local_inputs": [], "services": []})
            self.assertEqual(receipt.disposition, "ACCEPTED")
            for _ in range(200):
                status = await host.operation_status("prepare-1")
                if status.phase != "ACCEPTED":
                    break
                await asyncio.sleep(.01)
            self.assertEqual(status.phase, "SUCCEEDED")
            self.assertIsNotNone(host.current_snapshot().preparation)
            detail = await host.preparation_detail(status.result.get("preparation_id"))
            self.assertEqual(detail.get("proposal_hash"), status.result.get("proposal_hash"))
            self.assertIsNotNone(detail.get("proposal_json"))
            report = await host.run_report(status.result.get("planning_run_id"))
            self.assertEqual(report.get("run_id"), status.result.get("planning_run_id"))
        try:
            asyncio.run(scenario())
        finally:
            asyncio.run(host.close())
        self.assertEqual(runtime.turns_started, 1)
        self.assertTrue(runtime.closed)

    def test_engine_host_exact_start_returns_before_lifecycle_finishes(self):
        from taskledger.application.host import EngineHost
        self.enable_profiles()
        planning = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
        execution = FakeRuntime(worker_turns=[], reviewer_turns=[])
        resumed_execution = FakeRuntime(worker_turns=[], reviewer_turns=[])
        runtimes = iter((planning, execution, resumed_execution))
        host = EngineHost(str(self.fixture.root), project_id=self.project_id, runtime_factory=lambda **_: next(runtimes))

        async def wait_terminal(operation_id):
            for _ in range(300):
                status = await host.operation_status(operation_id)
                if status.phase != "ACCEPTED":
                    return status
                await asyncio.sleep(.01)
            self.fail(f"{operation_id} did not finish")

        async def scenario():
            self.assertEqual((await host.prepare_project("prepare-start", spec_path="spec.md", live=True,
                limits={"max_initial_planner_turns": 1}, preflight={"local_inputs": [], "services": []})).disposition, "ACCEPTED")
            prepared = await wait_terminal("prepare-start")
            preparation_id = prepared.result.get("preparation_id")
            proposal_hash = prepared.result.get("proposal_hash")
            receipt = await host.start_prepared_project("start-1", preparation_id=preparation_id,
                approve_proposal_hash=proposal_hash, live=True)
            self.assertEqual(receipt.disposition, "ACCEPTED")
            self.assertIsNotNone(receipt.run_id)
            await host.refresh_snapshot()
            task_id = host.current_snapshot().tasks[0].get("id")
            detail = await host.task_detail(task_id)
            self.assertIn("acceptance_criteria", dict(detail.fields))
            self.assertIn("integration_attempts", dict(detail.fields))
            pause = await host.request_pause(receipt.run_id)
            self.assertIn(pause.status, {"REQUESTED", "NOT_RUNNING"})
            terminal = await wait_terminal("start-1")
            self.assertIn(terminal.phase, {"SUCCEEDED", "FAILED"})
            resume = await host.resume_run("resume-1", run_id=receipt.run_id, live=True)
            self.assertEqual(resume.disposition, "ACCEPTED")
            self.assertEqual((await host.request_pause(resume.run_id)).status, "REQUESTED")
            self.assertIn((await wait_terminal("resume-1")).phase, {"SUCCEEDED", "FAILED"})
        try:
            asyncio.run(scenario())
        finally:
            asyncio.run(host.close())
        self.assertTrue(execution.closed)
        self.assertTrue(resumed_execution.closed)

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
            run_id=run_id, preparation_id=preparation_id, spec_path="spec.md",
            specification_bytes=revision["content_bytes"], specification_hash=revision["content_hash"], config=self.config,
        ).run())
        result = finish_preparation(self.service, preparation_id, planned, turn_id)
        self.assertEqual(result["status"], "FAILED")

    def test_prepare_rebinds_changed_specification_and_prompt_uses_selected_bytes(self):
        self.enable_profiles()
        first = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal(ambiguities=["clarify identity"]))])
        second = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(structured_output=proposal())])
        request = {"spec_path": "spec.md", "live": True, "limits": {"max_initial_planner_turns": 1}, "preflight": {"local_inputs": [], "services": []}}
        prior = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            with patch("taskledger.controller.app_server.AppServerRuntime", return_value=first):
                failed = prepare_project_command(Namespace(project=self.project_id, token=None), request)
            (self.fixture.root / "spec.md").write_text("Specification revision B\n")
            self.fixture.git("add", "spec.md")
            self.fixture.git("commit", "-qm", "revise specification")
            with patch("taskledger.controller.app_server.AppServerRuntime", return_value=second):
                prepared = prepare_project_command(Namespace(project=self.project_id, token=None), {**request, "run_group_id": failed["run_group_id"]})
        finally:
            os.chdir(prior)
        from taskledger.core import sha256
        current_hash = sha256((self.fixture.root / "spec.md").read_bytes())
        row = self.service.con.execute(
            "SELECT p.specification_hash,r.config_json FROM project_preparations p JOIN controller_runs r ON r.id=p.planning_run_id WHERE p.id=?",
            (prepared["preparation_id"],),
        ).fetchone()
        self.assertEqual(row["specification_hash"], current_hash)
        self.assertEqual(__import__("json").loads(row["config_json"])["manifest"]["specification_hash"], current_hash)
        self.assertIn("Specification revision B", second.prompts[0]["prompt"])
        self.assertEqual(self.service.con.execute("SELECT COUNT(*) FROM preparation_attempts WHERE run_group_id=?", (failed["run_group_id"],)).fetchone()[0], 2)

    def test_start_rejects_a_changed_specification_before_git_drift(self):
        run_id = self.journal.create_run(self.project_id, mode="PREPARATION", config={})
        revision = self.service.con.execute(
            "SELECT r.* FROM specifications s JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.id=?", (self.spec_id,)
        ).fetchone()
        preparation_id = store_planning_preparation(
            self.service, self.project, run_id=run_id, starting_oid=__import__("taskledger.git", fromlist=["oid"]).oid(self.fixture.root),
            spec_id=self.spec_id, spec_hash=revision["content_hash"], profile_hashes={}, config=self.config,
        )
        session = self.journal.create_session(
            run_id=run_id, project_id=self.project_id, role="TASK_CREATOR", profile="taskledger_task_creator",
            subject_id=preparation_id, external_thread_id="thread", config_hash="hash", runtime_identity={},
        )
        turn_id = self.journal.begin_turn(session.id, "initial-planning")
        self.journal.acknowledge_turn(turn_id, RuntimeTurnHandle("thread", "turn"))
        self.journal.complete_turn(turn_id, RuntimeTurnResult(RuntimeTurnHandle("thread", "turn")))
        finished = finish_preparation(self.service, preparation_id, proposal(), turn_id)
        (self.fixture.root / "spec.md").write_text("changed after preparation\n")
        with self.assertRaisesRegex(Exception, "Specification changed after preparation"):
            start_prepared_project(self.service, self.project, self.principal, {
                "preparation_id": preparation_id, "approve_proposal_hash": finished["proposal_hash"], "live": True,
            })

    def test_mutation_during_planning_never_stores_a_proposal(self):
        self.enable_profiles()
        def mutate_specification():
            (self.fixture.root / "spec.md").write_text("changed while planning\n")
        runtime = FakeRuntime(worker_turns=[], reviewer_turns=[], task_creator_turns=[TurnScript(effect=mutate_specification, structured_output=proposal())])
        prior = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            with patch("taskledger.controller.app_server.AppServerRuntime", return_value=runtime):
                with self.assertRaisesRegex(Exception, "Specification changed during preparation"):
                    prepare_project_command(Namespace(project=self.project_id, token=None), {
                        "spec_path": "spec.md", "live": True, "limits": {"max_initial_planner_turns": 1},
                        "preflight": {"local_inputs": [], "services": []},
                    })
        finally:
            os.chdir(prior)
        row = self.service.con.execute("SELECT state,proposal_json FROM project_preparations ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertEqual(row["state"], "FAILED")
        self.assertIsNone(row["proposal_json"])

    def test_source_normalization_is_deterministic_for_proposals_and_requirements(self):
        value = proposal()
        value["requirements"][0]["sources"] = [
            {"locator": "Section 1", "excerpt": "first"}, {"locator": "Section 1", "excerpt": "second"},
            {"locator": "Section 1", "excerpt": "first"}, {"locator": "Section 2", "excerpt": None},
        ]
        normalized = validate_proposal(value)
        self.assertEqual(normalized["requirements"][0]["sources"], [
            {"locator": "Section 1", "excerpt": "first\n\nsecond"}, {"locator": "Section 2", "excerpt": None},
        ])
        created = self.service.create_requirement(self.project, self.principal, {
            "statement": "Source normalization", "details": "Preserves excerpts", "implementation_required": False,
            "sources": [
                {"specification_id": self.spec_id, "locator": "Section 1", "excerpt": "first"},
                {"specification_id": self.spec_id, "locator": "Section 1", "excerpt": "second"},
                {"specification_id": self.spec_id, "locator": "Section 1", "excerpt": "first"},
                {"specification_id": self.spec_id, "locator": "Section 2", "excerpt": None},
            ],
        })
        sources = self.service.con.execute(
            "SELECT locator,excerpt FROM requirement_source_refs WHERE requirement_id=? ORDER BY locator", (created["requirement_id"],)
        ).fetchall()
        self.assertEqual([(source["locator"], source["excerpt"]) for source in sources], [("Section 1", "first\n\nsecond"), ("Section 2", None)])

    def test_unexpected_errors_include_safe_diagnostic_correlation(self):
        from taskledger.cli import main
        output, errors = StringIO(), StringIO()
        prior = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            with patch("taskledger.cli.dispatch", side_effect=RuntimeError("secret request body")), redirect_stdout(output), redirect_stderr(errors):
                status = main(["project", "show", "--verbose"])
        finally:
            os.chdir(prior)
        response = __import__("json").loads(output.getvalue())
        self.assertEqual(status, 70)
        self.assertEqual(response["error"]["details"]["phase"], "COMMAND_DISPATCH")
        diagnostic = response["error"]["details"]["correlation_id"]
        path = self.fixture.home / "diagnostics" / f"{diagnostic}.json"
        self.assertTrue(path.is_file())
        contents = path.read_text()
        self.assertNotIn("secret request body", contents)
        self.assertIn(str(path), errors.getvalue())

    def test_diagnostic_write_failure_preserves_error_and_phase(self):
        from taskledger.cli import command_phase, main

        def fail_in_materialization(*_args):
            with command_phase("PLAN_MATERIALIZATION"):
                raise RuntimeError("request contents must not be retained")

        output = StringIO()
        prior = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            with patch("taskledger.cli.dispatch", side_effect=fail_in_materialization), patch("taskledger.cli.os.open", side_effect=OSError("disk unavailable")), redirect_stdout(output):
                status = main(["project", "show"])
        finally:
            os.chdir(prior)
        response = __import__("json").loads(output.getvalue())
        self.assertEqual(status, 70)
        self.assertEqual(response["error"]["details"]["phase"], "PLAN_MATERIALIZATION")
        self.assertIn("correlation_id", response["error"]["details"])

    def test_preflight_failure_is_recorded_as_a_zero_model_attempt(self):
        self.enable_profiles()
        prior = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            with self.assertRaisesRegex(Exception, "Preparation preflight failed"):
                prepare_project_command(Namespace(project=self.project_id, token=None), {
                    "spec_path": "spec.md", "live": True, "limits": {"max_initial_planner_turns": 1},
                    "preflight": {"local_inputs": [{"name": "missing", "path": str(self.fixture.root / "missing"), "required": True}], "services": []},
                })
        finally:
            os.chdir(prior)
        attempt = self.service.con.execute(
            "SELECT state,failure_reason,planning_run_id FROM preparation_attempts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        self.assertEqual((attempt["state"], attempt["failure_reason"], attempt["planning_run_id"]), ("FAILED", "INVALID_REQUEST", None))

    def test_frozen_attempt_lineage_aggregates_complete_and_zero_model_attempts(self):
        def record_attempt(group_id, token_count, *, ambiguous=False):
            attempt = self.service.begin_preparation_attempt(self.project, group_id)
            run_id = self.journal.create_run(self.project_id, mode="PREPARATION", config={})
            self.service.link_preparation_attempt(attempt["attempt_id"], planning_run_id=run_id)
            revision = self.service.con.execute(
                "SELECT r.* FROM specifications s JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.id=?", (self.spec_id,)
            ).fetchone()
            preparation_id = store_planning_preparation(
                self.service, self.project, run_id=run_id, starting_oid="0" * 40, spec_id=self.spec_id,
                spec_hash=revision["content_hash"], profile_hashes={}, config=self.config,
            )
            self.service.link_preparation_attempt(attempt["attempt_id"], preparation_id=preparation_id)
            session = self.journal.create_session(
                run_id=run_id, project_id=self.project_id, role="TASK_CREATOR", profile="taskledger_task_creator",
                subject_id=preparation_id, external_thread_id=f"thread-{token_count}", config_hash="hash", runtime_identity={},
            )
            turn_id = self.journal.begin_turn(session.id, "initial-planning")
            handle = RuntimeTurnHandle(session.thread_id, f"turn-{token_count}")
            self.journal.acknowledge_turn(turn_id, handle)
            self.journal.complete_turn(turn_id, RuntimeTurnResult(
                handle, usage=Usage(input_tokens=token_count), usage_precision=UsagePrecision.THREAD_TOTAL_DELTA,
            ))
            result = finish_preparation(self.service, preparation_id, proposal(ambiguities=["ambiguous"]) if ambiguous else proposal(), turn_id)
            self.journal.finish_run(run_id, "FAILED" if ambiguous else "COMPLETED")
            self.service.finish_preparation_attempt(
                attempt["attempt_id"], succeeded=not ambiguous, failure_reason="AMBIGUOUS_REQUIREMENT" if ambiguous else None,
            )
            return attempt, run_id, preparation_id

        zero = self.service.begin_preparation_attempt(self.project)
        self.service.finish_preparation_attempt(zero["attempt_id"], succeeded=False, failure_reason="PREFLIGHT_FAILED")
        first, _, _ = record_attempt(zero["run_group_id"], 208_653, ambiguous=True)
        selected, selected_run, selected_preparation = record_attempt(zero["run_group_id"], 638_761)
        execution = self.journal.create_run(self.project_id, mode="PROJECT", config={"manifest": {
            "preparation_run_id": selected_run, "preparation_run_group_id": zero["run_group_id"],
            "preparation_attempt_ids": [zero["attempt_id"], first["attempt_id"], selected["attempt_id"]],
        }})
        self.journal.finish_run(execution, "COMPLETED")
        self.service.con.execute("UPDATE project_preparations SET state='APPROVED' WHERE id=?", (selected_preparation,))
        later, _, _ = record_attempt(zero["run_group_id"], 99)
        report = asyncio.run(controller_report(self.service.con, execution, include_provider_detail=False))
        whole = report["economics"]["whole_taskledger_run"]
        self.assertEqual(whole["direct_preparation_known_tokens"], 638_761)
        self.assertEqual(whole["preparation_lineage"]["aggregate_preparation_known_tokens"], 847_414)
        self.assertEqual(whole["known_total_tokens"], 847_414)
        self.assertTrue(whole["preparation_lineage"]["aggregate_accounting_complete"])
        self.assertEqual(len(whole["preparation_lineage"]["attempts"]), 3)
        self.assertEqual(whole["preparation_lineage"]["attempts"][0]["accounting_status"], "ZERO_MODEL_FAILURE")
        self.assertNotIn(later["attempt_id"], [detail["attempt_id"] for detail in whole["preparation_lineage"]["attempts"]])

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

    def test_lifecycle_lease_remains_held_during_interruption_reconciliation(self):
        from taskledger.application.lifecycle import LifecycleOwner
        from taskledger.controller.model import RuntimeTurnHandle
        run_id = self.journal.create_run(self.project_id, mode="PROJECT", config={})
        session = self.journal.create_session(run_id=run_id, project_id=self.project_id, role="WORKER",
            profile="routine", subject_id="task", external_thread_id="thread", config_hash="hash")
        local_id = self.journal.begin_turn(session.id, "worker")
        self.journal.acknowledge_turn(local_id, RuntimeTurnHandle("thread", "turn"))
        entered, release = threading.Event(), threading.Event()

        class Runtime(FakeRuntime):
            async def interrupt_turn(self, handle):
                entered.set()
                await asyncio.to_thread(release.wait)

        class HangingController:
            def __init__(self, **kwargs): pass
            async def run(self): await asyncio.Event().wait()

        runtime = Runtime(worker_turns=[], reviewer_turns=[])
        owner = LifecycleOwner(self.service, self.project, self.principal, run_id, object(), runtime)

        async def scenario():
            with patch("taskledger.controller.project.ProjectController", HangingController):
                task = asyncio.create_task(owner.run())
                await asyncio.sleep(.05); owner.request_stop()
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with self.journal.project_lock(self.project_id): pass
                release.set()
                return await task
        result = asyncio.run(scenario())
        self.assertEqual(result.status, "PAUSED")


if __name__ == "__main__":
    unittest.main()
