from __future__ import annotations

import asyncio
import json
import subprocess
import unittest
from pathlib import Path

from taskledger.controller.fakes import FakeRuntime, TurnScript, accepted_verdict
from taskledger.controller.journal import Journal
from taskledger.controller.model import SessionRole, Usage
from taskledger.controller.project import (
    ProjectController,
    ProjectControllerConfig,
    SemanticJobBudgetExhausted,
    validate_execution_policy,
)
from taskledger.controller.supervisor import SupervisorConfig
from taskledger.db import connect
from taskledger.service import Service


class ProjectControllerTests(unittest.TestCase):
    def setUp(self):
        from tests.test_acceptance import TaskledgerAcceptance
        self.fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        self.fixture.setUp()
        self.project_id = self.fixture.init()
        _, spec = self.fixture.command("spec", "register", {"relative_path": "spec.md"}, project=self.project_id)
        self.spec_id = spec["data"]["specification_id"]

    def tearDown(self):
        self.fixture.tearDown()

    def build_plan(self):
        requirements = [
            {"ref": "r1", "statement": "first", "details": "first behavior", "implementation_required": True, "sources": [{"specification_id": self.spec_id, "locator": "1"}]},
            {"ref": "r2", "statement": "second", "details": "second behavior", "implementation_required": True, "sources": [{"specification_id": self.spec_id, "locator": "1"}]},
        ]
        tasks = [
            {"ref": "t1", "objective": "implement first", "implementation_scope": "first.txt", "acceptance_criteria": ["first exists"], "required_checks": [], "requirement_refs": ["r1"], "requirement_ids": [], "dependency_refs": [], "dependency_task_ids": []},
            {"ref": "t2", "objective": "implement second", "implementation_scope": "second.txt", "acceptance_criteria": ["second exists"], "required_checks": [], "requirement_refs": ["r2"], "requirement_ids": [], "dependency_refs": [], "dependency_task_ids": []},
        ]
        _, applied = self.fixture.command("plan", "apply", {"requirements": requirements, "tasks": tasks}, project=self.project_id)
        self.fixture.command("plan", "validate", {}, project=self.project_id)
        return applied["data"]["requirement_ids"], applied["data"]["task_ids"]

    def test_project_controller_runs_approved_wave_and_completes_by_requirement(self):
        requirement_ids, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        ordered_tasks = sorted(task_ids.values())

        def implement(task_id: str, filename: str):
            def effect():
                assignment = service.con.execute("SELECT * FROM assignments WHERE task_id=? AND state='ACTIVE'", (task_id,)).fetchone()
                worktree = Path(assignment["worktree_path"])
                (worktree / filename).write_text(filename + "\n")
                subprocess.run(["git", "-C", str(worktree), "add", filename], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(worktree), "-c", "user.name=Controller Test", "-c", "user.email=controller@example.invalid", "commit", "-qm", "implement " + filename], check=True, capture_output=True)
                worker = service.con.execute("SELECT * FROM principals WHERE assignment_id=? AND role='WORKER'", (assignment["id"],)).fetchone()
                service.worker_submit(worker, {"summary": "implemented", "evidence": [{"label": "fake", "details": filename}], "risks": [], "unresolved_questions": [], "follow_up_work": []})
            return effect

        filename_by_task = {task_ids["t1"]: "first.txt", task_ids["t2"]: "second.txt"}
        worker_turns = [TurnScript(effect=implement(task_id, filename_by_task[task_id])) for task_id in ordered_tasks]
        reviewer_turns = []
        for task_id in ordered_tasks:
            criterion = service.con.execute("SELECT id FROM task_acceptance_criteria WHERE task_id=?", (task_id,)).fetchone()[0]
            reviewer_turns.append(TurnScript(structured_output=accepted_verdict((criterion,))))
        final = {
            "outcome": "SATISFIED",
            "requirement_results": [
                {"requirement_id": rid, "satisfied": True, "evidence": "integrated canonical behavior inspected"}
                for rid in sorted(requirement_ids.values())
            ],
            "findings": [],
            "notes": "complete",
        }
        runtime = FakeRuntime(worker_turns=worker_turns, reviewer_turns=reviewer_turns, final_reviewer_turns=[TurnScript(structured_output=final)])
        targets = validate_execution_policy(service, project, [
            {"task_id": task_id, "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": [filename_by_task[task_id]]}
            for task_id in ordered_tasks
        ])
        config = ProjectControllerConfig(max_workers=2, max_reviewers=1, supervisor=SupervisorConfig(max_total_tokens=1000))
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=targets)
        result = asyncio.run(ProjectController(service=service, project=project, orchestrator=orchestrator, run_id=run_id, runtime=runtime, journal=journal, config=config).run())
        self.assertEqual(result.status, "COMPLETED", result)
        self.assertEqual(result.integrated_tasks, 2)
        self.assertEqual(service.con.execute("SELECT lifecycle FROM projects WHERE id=?", (self.project_id,)).fetchone()[0], "COMPLETED")
        self.assertEqual(service.con.execute("SELECT COUNT(*) FROM requirement_verifications WHERE state='CURRENT'").fetchone()[0], 2)
        self.assertEqual([row[0] for row in service.con.execute("SELECT state FROM controller_run_targets ORDER BY task_id")], ["INTEGRATED", "INTEGRATED"])
        service.con.close()

    def test_scheduler_enforces_parallel_flag_and_write_surface_compatibility(self):
        _, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        config = ProjectControllerConfig()
        journal = Journal(service.con, self.fixture.home)
        targets = validate_execution_policy(service, project, [
            {"task_id": task_ids["t1"], "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["shared"]},
            {"task_id": task_ids["t2"], "wave": 1, "worker_profile": "complex", "parallel_safe": True, "write_surfaces": ["shared"]},
        ])
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=targets)
        controller = ProjectController(service=service, project=project, orchestrator=orchestrator, run_id=run_id, runtime=FakeRuntime(worker_turns=[], reviewer_turns=[]), journal=journal, config=config)
        self.assertEqual(len(controller._compatible_batch(journal.targets(run_id))), 1)
        service.con.execute("UPDATE controller_run_targets SET write_surfaces_json='[\"other\"]' WHERE task_id=?", (task_ids["t2"],))
        self.assertEqual(len(controller._compatible_batch(journal.targets(run_id))), 2)
        service.con.execute("UPDATE controller_run_targets SET write_surfaces_json='[\"src/module.py\"]' WHERE task_id=?", (task_ids["t1"],))
        service.con.execute("UPDATE controller_run_targets SET write_surfaces_json='[\"src/\"]' WHERE task_id=?", (task_ids["t2"],))
        self.assertEqual(len(controller._compatible_batch(journal.targets(run_id))), 1)
        service.con.execute("UPDATE controller_run_targets SET parallel_safe=0 WHERE task_id=?", (task_ids["t1"],))
        self.assertEqual(len(controller._compatible_batch(journal.targets(run_id))), 1)
        journal.finish_run(run_id, "PAUSED")
        service.con.close()

    def test_bounded_semantic_job_retries_malformed_output(self):
        requirement_ids, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        requirement_id = next(iter(requirement_ids.values()))
        valid = {
            "outcome": "SATISFIED",
            "requirement_results": [
                {"requirement_id": item, "satisfied": True, "evidence": "inspected"}
                for item in sorted(requirement_ids.values())
            ],
            "findings": [],
            "notes": "valid",
        }
        runtime = FakeRuntime(
            worker_turns=[], reviewer_turns=[],
            final_reviewer_turns=[TurnScript(structured_output={"outcome": "SATISFIED"}), TurnScript(structured_output=valid)],
        )
        targets = validate_execution_policy(service, project, [
            {"task_id": task_id, "wave": 1, "worker_profile": "routine", "parallel_safe": False, "write_surfaces": [task_id]}
            for task_id in task_ids.values()
        ])
        config = ProjectControllerConfig(max_final_reviewer_turns=2)
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=targets)
        controller = ProjectController(service=service, project=project, orchestrator=orchestrator, run_id=run_id, runtime=runtime, journal=journal, config=config)

        async def execute():
            return await controller._semantic_job(
                role=SessionRole.REQUIREMENT_REVIEWER,
                profile=config.supervisor.reviewer_profile,
                subject_id="final-test",
                prompt="review",
                schema=controller._final_review_schema(service.requirement_rows(project)),
                max_turns=2,
                prompt_kind="final-review",
                validator=lambda value: controller._validate_final_verdict(value, service.requirement_rows(project)),
            )

        payload, _ = asyncio.run(execute())
        self.assertEqual(payload["outcome"], "SATISFIED")
        self.assertEqual(runtime.turns_started, 2)
        self.assertEqual(requirement_id in {item["requirement_id"] for item in payload["requirement_results"]}, True)
        journal.finish_run(run_id, "PAUSED")
        service.con.close()

    def test_semantic_job_rechecks_project_token_admission_before_retry(self):
        _, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        runtime = FakeRuntime(
            worker_turns=[], reviewer_turns=[],
            final_reviewer_turns=[
                TurnScript(structured_output={"malformed": True}, usage=Usage(input_tokens=10)),
                TurnScript(structured_output={"would": "exceed admission"}),
            ],
        )
        targets = validate_execution_policy(service, project, [
            {"task_id": task_id, "wave": 1, "worker_profile": "routine", "parallel_safe": False, "write_surfaces": [task_id]}
            for task_id in task_ids.values()
        ])
        config = ProjectControllerConfig(
            max_final_reviewer_turns=2,
            supervisor=SupervisorConfig(max_total_tokens=10),
        )
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=targets)
        controller = ProjectController(
            service=service, project=project, orchestrator=orchestrator, run_id=run_id,
            runtime=runtime, journal=journal, config=config,
        )

        async def execute():
            return await controller._semantic_job(
                role=SessionRole.REQUIREMENT_REVIEWER,
                profile=config.supervisor.reviewer_profile,
                subject_id="budget-test",
                prompt="review",
                schema=controller._final_review_schema(service.requirement_rows(project)),
                max_turns=2,
                prompt_kind="final-review",
                validator=lambda value: controller._validate_final_verdict(value, service.requirement_rows(project)),
            )

        with self.assertRaisesRegex(SemanticJobBudgetExhausted, "token admission"):
            asyncio.run(execute())
        self.assertEqual(runtime.turns_started, 1)
        journal.finish_run(run_id, "PAUSED")
        service.con.close()

    def test_final_and_correction_contracts_reject_inconsistent_semantics(self):
        requirement_ids, _ = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        config = ProjectControllerConfig()
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=[])
        controller = ProjectController(service=service, project=project, orchestrator=orchestrator, run_id=run_id, runtime=FakeRuntime(worker_turns=[], reviewer_turns=[]), journal=journal, config=config)
        results = [
            {"requirement_id": rid, "satisfied": False, "evidence": "missing"}
            for rid in sorted(requirement_ids.values())
        ]
        mixed = {
            "outcome": "DEFECTS", "requirement_results": results,
            "findings": [
                {"classification": "IMPLEMENTATION_DEFECT", "description": "bug", "requirement_ids": []},
                {"classification": "PRODUCT_AMBIGUITY", "description": "unclear", "requirement_ids": []},
            ],
            "notes": "mixed",
        }
        self.assertIsNone(controller._validate_final_verdict(mixed, service.requirement_rows(project)))
        self.assertIsNone(controller._validate_corrections({"tasks": [{"requirement_ids": []}]}, "review"))
        journal.finish_run(run_id, "PAUSED")
        service.con.close()

    def test_final_defect_invokes_bounded_task_creator_and_resumes_scheduling(self):
        requirement_ids, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        initial_tasks = sorted(task_ids.values())

        def submit_current(task_id: str, filename: str):
            def effect():
                assignment = service.con.execute(
                    "SELECT * FROM assignments WHERE task_id=? AND state='ACTIVE'", (task_id,)
                ).fetchone()
                worktree = Path(assignment["worktree_path"])
                (worktree / filename).write_text(filename + "\n")
                subprocess.run(["git", "-C", str(worktree), "add", filename], check=True, capture_output=True)
                subprocess.run([
                    "git", "-C", str(worktree), "-c", "user.name=Controller Test",
                    "-c", "user.email=controller@example.invalid", "commit", "-qm", "implement " + filename,
                ], check=True, capture_output=True)
                worker = service.con.execute(
                    "SELECT * FROM principals WHERE assignment_id=? AND role='WORKER'", (assignment["id"],)
                ).fetchone()
                service.worker_submit(worker, {
                    "summary": "implemented", "evidence": [{"label": "fake", "details": filename}],
                    "risks": [], "unresolved_questions": [], "follow_up_work": [],
                })
            return effect

        def submit_correction():
            task = service.con.execute(
                "SELECT id FROM tasks WHERE project_id=? AND state='ASSIGNED' AND id NOT IN (?,?)",
                (self.project_id, *initial_tasks),
            ).fetchone()
            submit_current(task["id"], "correction.txt")()

        def verdict_for_pending():
            criterion = service.con.execute(
                "SELECT c.id FROM task_acceptance_criteria c JOIN tasks t ON t.id=c.task_id WHERE t.state='SUBMITTED'"
            ).fetchone()[0]
            return accepted_verdict((criterion,))

        first_requirement = sorted(requirement_ids.values())[0]
        correction_plan = {
            "tasks": [{
                "objective": "repair final review defect",
                "implementation_scope": "correction.txt",
                "acceptance_criteria": ["correction exists"],
                "required_checks": [],
                "requirement_ids": [first_requirement],
                "dependency_task_ids": initial_tasks,
                "worker_profile": "complex",
                "parallel_safe": False,
                "write_surfaces": ["correction.txt"],
            }]
        }
        defect = {
            "outcome": "DEFECTS",
            "requirement_results": [
                {"requirement_id": rid, "satisfied": rid != first_requirement, "evidence": "integrated state inspected"}
                for rid in sorted(requirement_ids.values())
            ],
            "findings": [{
                "classification": "IMPLEMENTATION_DEFECT", "description": "missing correction",
                "requirement_ids": [first_requirement],
            }],
            "notes": "needs a bounded correction",
        }
        satisfied = {
            "outcome": "SATISFIED",
            "requirement_results": [
                {"requirement_id": rid, "satisfied": True, "evidence": "corrected canonical state inspected"}
                for rid in sorted(requirement_ids.values())
            ],
            "findings": [],
            "notes": "complete",
        }
        runtime = FakeRuntime(
            worker_turns=[
                TurnScript(effect=submit_current(initial_tasks[0], "first-pass-a.txt")),
                TurnScript(effect=submit_current(initial_tasks[1], "first-pass-b.txt")),
                TurnScript(effect=submit_correction),
            ],
            reviewer_turns=[TurnScript(structured_output=verdict_for_pending) for _ in range(3)],
            final_reviewer_turns=[TurnScript(structured_output=defect), TurnScript(structured_output=satisfied)],
            task_creator_turns=[TurnScript(structured_output=correction_plan)],
        )
        targets = validate_execution_policy(service, project, [
            {"task_id": task_id, "wave": 1, "worker_profile": "routine", "parallel_safe": False, "write_surfaces": [task_id]}
            for task_id in initial_tasks
        ])
        config = ProjectControllerConfig(max_workers=1, max_reviewers=1, supervisor=SupervisorConfig(max_total_tokens=1000))
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=targets)
        result = asyncio.run(ProjectController(
            service=service, project=project, orchestrator=orchestrator, run_id=run_id,
            runtime=runtime, journal=journal, config=config,
        ).run())
        self.assertEqual(result.status, "COMPLETED", result)
        self.assertEqual(result.integrated_tasks, 3)
        self.assertEqual(service.con.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (self.project_id,)).fetchone()[0], 3)
        self.assertEqual(service.con.execute("SELECT COUNT(*) FROM controller_sessions WHERE role='TASK_CREATOR'").fetchone()[0], 1)
        self.assertEqual((self.fixture.root / "correction.txt").read_text().strip(), "correction.txt")
        service.con.close()


if __name__ == "__main__":
    unittest.main()
