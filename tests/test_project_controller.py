from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from taskledger.controller.fakes import FakeRuntime, TurnScript, accepted_verdict, rejected_verdict
from taskledger.controller.journal import Journal
from taskledger.controller.model import (
    RuntimeSession,
    RuntimeTurnHandle,
    RuntimeTurnInspection,
    RuntimeTurnResult,
    SessionRole,
    Usage,
    UsagePrecision,
)
from taskledger.controller.project import (
    ProjectController,
    ProjectControllerConfig,
    SemanticJobBudgetExhausted,
    validate_execution_policy,
)
from taskledger.controller.supervisor import SupervisorConfig
from taskledger.db import connect
from taskledger.service import Service


@dataclass
class ControlledTurn:
    effect: Callable[[], Any] | None = None
    structured_output: dict[str, Any] | Callable[[], dict[str, Any] | None] | None = None
    release: asyncio.Event | None = None
    started: asyncio.Event | None = None
    usage: Usage = Usage()


class SubjectRuntime(FakeRuntime):
    """Test-local runtime that resolves scripts by durable subject identity."""

    def __init__(self, resolver):
        super().__init__(worker_turns=[], reviewer_turns=[], final_reviewer_turns=[])
        self.resolver = resolver
        self.subjects: dict[str, str] = {}
        self.subject_turn_counts: dict[tuple[SessionRole, str], int] = {}
        self.controlled: dict[tuple[str, str], ControlledTurn] = {}
        self.active: set[tuple[SessionRole, str, str]] = set()
        self.max_active = {role: 0 for role in SessionRole}
        self.timeline: list[tuple[int, str, SessionRole, str, str]] = []
        self.clock = 0

    async def start_session(self, *, role, profile, subject_id, cwd, writable):
        session = await super().start_session(
            role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=writable
        )
        self.subjects[session.thread_id] = subject_id
        return session

    async def start_turn(self, *, thread_id, prompt, output_schema=None):
        role = self.roles[thread_id]
        subject = self.subjects[thread_id]
        key = (role, subject)
        index = self.subject_turn_counts.get(key, 0)
        self.subject_turn_counts[key] = index + 1
        script = self.resolver(role, subject, index)
        self.turns_started += 1
        self.prompts.append({"thread_id": thread_id, "prompt": prompt, "output_schema": output_schema})
        handle = RuntimeTurnHandle(thread_id, f"turn-{self.turns_started}")
        self.controlled[(thread_id, handle.turn_id)] = script
        self.turns[(thread_id, handle.turn_id)] = ("RUNNING", TurnScript(usage=script.usage))
        self.active.add((role, subject, handle.turn_id))
        self.max_active[role] = max(
            self.max_active[role], sum(item[0] == role for item in self.active)
        )
        self.clock += 1
        self.timeline.append((self.clock, "START", role, subject, handle.turn_id))
        if script.started:
            script.started.set()
        return handle

    async def wait_turn(self, handle):
        script = self.controlled[(handle.thread_id, handle.turn_id)]
        if script.release:
            await script.release.wait()
        if script.effect:
            value = script.effect()
            if inspect.isawaitable(value):
                await value
        role = self.roles[handle.thread_id]
        subject = self.subjects[handle.thread_id]
        self.active.discard((role, subject, handle.turn_id))
        self.clock += 1
        self.timeline.append((self.clock, "END", role, subject, handle.turn_id))
        self.turns[(handle.thread_id, handle.turn_id)] = ("COMPLETED", TurnScript(usage=script.usage))
        structured = script.structured_output() if callable(script.structured_output) else script.structured_output
        return RuntimeTurnResult(
            handle,
            structured_output=structured,
            usage=script.usage,
            usage_precision=UsagePrecision.THREAD_TOTAL_DELTA,
        )

    async def inspect_turn(self, handle):
        state, _ = self.turns.get((handle.thread_id, handle.turn_id), ("UNKNOWN", None))
        return RuntimeTurnInspection(state)


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

    def build_single_plan(self):
        _, applied = self.fixture.command("plan", "apply", {
            "requirements": [{
                "ref": "r1", "statement": "first", "details": "first behavior",
                "implementation_required": True,
                "sources": [{"specification_id": self.spec_id, "locator": "1"}],
            }],
            "tasks": [{
                "ref": "t1", "objective": "implement first", "implementation_scope": "first.txt",
                "acceptance_criteria": ["first exists"], "required_checks": [],
                "requirement_refs": ["r1"], "requirement_ids": [],
                "dependency_refs": [], "dependency_task_ids": [],
            }],
        }, project=self.project_id)
        self.fixture.command("plan", "validate", {}, project=self.project_id)
        return applied["data"]["requirement_ids"]["r1"], applied["data"]["task_ids"]["t1"]

    @staticmethod
    def commit_file(service, task_id: str, filename: str, content: str, *, submit: bool) -> None:
        assignment = service.con.execute(
            "SELECT * FROM assignments WHERE task_id=? AND state='ACTIVE'", (task_id,)
        ).fetchone()
        worktree = Path(assignment["worktree_path"])
        (worktree / filename).write_text(content)
        subprocess.run(["git", "-C", str(worktree), "add", filename], check=True, capture_output=True)
        subprocess.run([
            "git", "-C", str(worktree), "-c", "user.name=Controller Test",
            "-c", "user.email=controller@example.invalid", "commit", "-qm", "update " + filename,
        ], check=True, capture_output=True)
        if submit:
            worker = service.con.execute(
                "SELECT * FROM principals WHERE assignment_id=? AND role='WORKER'", (assignment["id"],)
            ).fetchone()
            service.worker_submit(worker, {
                "summary": "implemented", "evidence": [{"label": "controlled", "details": filename}],
                "risks": [], "unresolved_questions": [], "follow_up_work": [],
            })

    def test_r1a_project_continuation_rejection_correction_and_completion(self):
        requirement_id, task_id = self.build_single_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)

        def assignment_for_subject(subject):
            return service.con.execute("SELECT * FROM assignments WHERE id=?", (subject,)).fetchone()

        def criterion_for_submission(subject):
            return service.con.execute(
                "SELECT c.id FROM submissions s JOIN assignments a ON a.id=s.assignment_id "
                "JOIN task_acceptance_criteria c ON c.task_id=a.task_id WHERE s.id=?",
                (subject,),
            ).fetchone()[0]

        def resolver(role, subject, index):
            if role == SessionRole.WORKER:
                assignment = assignment_for_subject(subject)
                effects = (
                    lambda: self.commit_file(service, assignment["task_id"], "first.txt", "durable draft\n", submit=False),
                    lambda: self.commit_file(service, assignment["task_id"], "first.txt", "needs correction\n", submit=True),
                    lambda: self.commit_file(service, assignment["task_id"], "first.txt", "first.txt\n", submit=True),
                )
                return ControlledTurn(effect=effects[index], usage=Usage(10, 2, 1))
            if role == SessionRole.REVIEWER:
                criterion = criterion_for_submission(subject)
                sequence = service.con.execute(
                    "SELECT sequence FROM submissions WHERE id=?", (subject,)
                ).fetchone()[0]
                verdict = rejected_verdict((criterion,)) if sequence == 1 else accepted_verdict((criterion,))
                return ControlledTurn(structured_output=verdict, usage=Usage(5, 1, 1))
            if role == SessionRole.REQUIREMENT_REVIEWER:
                return ControlledTurn(structured_output={
                    "outcome": "SATISFIED",
                    "requirement_results": [{
                        "requirement_id": requirement_id, "satisfied": True,
                        "evidence": "integrated canonical file inspected",
                    }],
                    "findings": [], "notes": "complete",
                }, usage=Usage(5, 1, 1))
            raise AssertionError((role, subject, index))

        runtime = SubjectRuntime(resolver)
        targets = validate_execution_policy(service, project, [{
            "task_id": task_id, "wave": 1, "worker_profile": "routine",
            "parallel_safe": False, "write_surfaces": ["first.txt"],
        }])
        config = ProjectControllerConfig(
            max_workers=1, max_reviewers=1, max_total_worker_turns=3,
            max_total_reviewer_turns=3,
            supervisor=SupervisorConfig(max_worker_turns=3, max_total_tokens=1000),
        )
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(
            self.project_id, config={"limits": config.as_dict()}, targets=targets
        )
        result = asyncio.run(ProjectController(
            service=service, project=project, orchestrator=orchestrator, run_id=run_id,
            runtime=runtime, journal=journal, config=config,
        ).run())
        self.assertEqual(result.status, "COMPLETED", result)
        assignment = service.con.execute("SELECT * FROM assignments WHERE task_id=?", (task_id,)).fetchone()
        worker_session = service.con.execute(
            "SELECT * FROM controller_sessions WHERE run_id=? AND role='WORKER'", (run_id,)
        ).fetchone()
        worker_turns = list(service.con.execute(
            "SELECT * FROM controller_turns WHERE session_id=? ORDER BY sequence", (worker_session["id"],)
        ))
        self.assertEqual([row["dispatch_reason"] for row in worker_turns], [
            "INITIAL_WORK", "ACTIVE_CONTINUATION", "CORRECTION",
        ])
        self.assertEqual(len({row["external_turn_id"] for row in worker_turns}), 3)
        self.assertTrue(all(row["session_id"] == worker_session["id"] for row in worker_turns))
        submissions = list(service.con.execute(
            "SELECT id,state,head_commit_oid FROM submissions WHERE assignment_id=? ORDER BY sequence",
            (assignment["id"],),
        ))
        self.assertEqual([row["state"] for row in submissions], ["REJECTED", "ACCEPTED"])
        self.assertEqual(len({row["id"] for row in submissions}), 2)
        self.assertEqual(len({row["head_commit_oid"] for row in submissions}), 2)
        reviews = list(service.con.execute(
            "SELECT outcome,corrections FROM submission_verifications WHERE submission_id IN (?,?) ORDER BY created_at,id",
            (submissions[0]["id"], submissions[1]["id"]),
        ))
        self.assertEqual([row["outcome"] for row in reviews], ["REJECTED", "ACCEPTED"])
        self.assertEqual(reviews[0]["corrections"], "fix the defect")
        reviewer_sessions = list(service.con.execute(
            "SELECT subject_id,external_thread_id FROM controller_sessions WHERE run_id=? AND role='REVIEWER'",
            (run_id,),
        ))
        self.assertEqual({row["subject_id"] for row in reviewer_sessions}, {row["id"] for row in submissions})
        self.assertEqual(len({row["external_thread_id"] for row in reviewer_sessions}), 2)
        for reviewer in reviewer_sessions:
            reviewed_assignment = service.con.execute(
                "SELECT assignment_id FROM submissions WHERE id=?", (reviewer["subject_id"],)
            ).fetchone()[0]
            worker_end = next(
                row[0] for row in runtime.timeline
                if row[1:4] == ("END", SessionRole.WORKER, reviewed_assignment)
            )
            review_start = next(
                row[0] for row in runtime.timeline
                if row[1:4] == ("START", SessionRole.REVIEWER, reviewer["subject_id"])
            )
            self.assertLess(worker_end, review_start)
        self.assertEqual(service.con.execute(
            "SELECT lifecycle FROM projects WHERE id=?", (self.project_id,)
        ).fetchone()[0], "COMPLETED")
        self.assertEqual(service.con.execute(
            "SELECT COUNT(*) FROM requirement_verifications WHERE requirement_id=? AND state='CURRENT'",
            (requirement_id,),
        ).fetchone()[0], 1)
        integration = service.current_integration(project, task_id)
        self.assertIsNotNone(integration)
        self.assertEqual(integration["canonical_after_oid"], subprocess.run(
            ["git", "-C", str(self.fixture.root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip())
        service.con.close()

    def test_r1a_unsatisfied_final_requirement_prevents_empty_queue_completion(self):
        requirement_id, task_id = self.build_single_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)

        def resolver(role, subject, index):
            if role == SessionRole.WORKER:
                return ControlledTurn(effect=lambda: self.commit_file(
                    service, task_id, "first.txt", "first.txt\n", submit=True
                ))
            if role == SessionRole.REVIEWER:
                criterion = service.con.execute(
                    "SELECT c.id FROM submissions s JOIN assignments a ON a.id=s.assignment_id "
                    "JOIN task_acceptance_criteria c ON c.task_id=a.task_id WHERE s.id=?", (subject,),
                ).fetchone()[0]
                return ControlledTurn(structured_output=accepted_verdict((criterion,)))
            if role == SessionRole.REQUIREMENT_REVIEWER:
                return ControlledTurn(structured_output={
                    "outcome": "AMBIGUOUS",
                    "requirement_results": [{
                        "requirement_id": requirement_id, "satisfied": False,
                        "evidence": "final requirement remains unsatisfied",
                    }],
                    "findings": [{
                        "classification": "PRODUCT_AMBIGUITY", "description": "input required",
                        "requirement_ids": [requirement_id],
                    }],
                    "notes": "do not fabricate satisfaction",
                })
            raise AssertionError(role)

        runtime = SubjectRuntime(resolver)
        targets = validate_execution_policy(service, project, [{
            "task_id": task_id, "wave": 1, "worker_profile": "routine",
            "parallel_safe": False, "write_surfaces": ["first.txt"],
        }])
        config = ProjectControllerConfig(
            max_workers=1, max_reviewers=1, max_total_worker_turns=1,
            max_total_reviewer_turns=2, max_final_reviewer_turns=1,
            supervisor=SupervisorConfig(max_worker_turns=1, max_total_tokens=1000),
        )
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(
            self.project_id, config={"limits": config.as_dict()}, targets=targets
        )
        result = asyncio.run(ProjectController(
            service=service, project=project, orchestrator=orchestrator, run_id=run_id,
            runtime=runtime, journal=journal, config=config,
        ).run())
        self.assertEqual(result.status, "PAUSED")
        self.assertEqual(result.pause_reason, "BLOCKED")
        self.assertTrue(all(row["state"] == "INTEGRATED" for row in journal.targets(run_id)))
        self.assertEqual(service.con.execute(
            "SELECT lifecycle FROM projects WHERE id=?", (self.project_id,)
        ).fetchone()[0], "ACTIVE")
        self.assertEqual(service.con.execute(
            "SELECT COUNT(*) FROM requirement_verifications WHERE requirement_id=? AND state='CURRENT'",
            (requirement_id,),
        ).fetchone()[0], 0)
        service.con.close()

    def test_r1b_review_starts_before_independent_worker_is_released(self):
        requirement_ids, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        task_a, task_b = task_ids["t1"], task_ids["t2"]

        async def execute():
            release_b = asyncio.Event()
            worker_b_started = asyncio.Event()
            reviewer_a_started = asyncio.Event()

            def task_for_assignment(assignment_id):
                return service.con.execute(
                    "SELECT task_id FROM assignments WHERE id=?", (assignment_id,)
                ).fetchone()[0]

            def task_for_submission(submission_id):
                return service.con.execute(
                    "SELECT a.task_id FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.id=?",
                    (submission_id,),
                ).fetchone()[0]

            def resolver(role, subject, index):
                if role == SessionRole.WORKER:
                    task_id = task_for_assignment(subject)
                    filename = "first.txt" if task_id == task_a else "second.txt"
                    return ControlledTurn(
                        effect=lambda: self.commit_file(service, task_id, filename, filename + "\n", submit=True),
                        release=release_b if task_id == task_b else worker_b_started,
                        started=worker_b_started if task_id == task_b else None,
                    )
                if role == SessionRole.REVIEWER:
                    task_id = task_for_submission(subject)
                    criterion = service.con.execute(
                        "SELECT id FROM task_acceptance_criteria WHERE task_id=?", (task_id,)
                    ).fetchone()[0]
                    return ControlledTurn(
                        structured_output=accepted_verdict((criterion,)),
                        started=reviewer_a_started if task_id == task_a else None,
                    )
                if role == SessionRole.REQUIREMENT_REVIEWER:
                    return ControlledTurn(structured_output={
                        "outcome": "SATISFIED",
                        "requirement_results": [{
                            "requirement_id": rid, "satisfied": True, "evidence": "integrated"
                        } for rid in sorted(requirement_ids.values())],
                        "findings": [], "notes": "complete",
                    })
                raise AssertionError(role)

            runtime = SubjectRuntime(resolver)
            targets = validate_execution_policy(service, project, [
                {"task_id": task_a, "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["first.txt"]},
                {"task_id": task_b, "wave": 1, "worker_profile": "routine", "parallel_safe": True, "write_surfaces": ["second.txt"]},
            ])
            config = ProjectControllerConfig(
                max_workers=2, max_reviewers=1, max_total_worker_turns=2,
                max_total_reviewer_turns=3, max_final_reviewer_turns=1,
                supervisor=SupervisorConfig(max_worker_turns=1, max_total_tokens=1000),
            )
            journal = Journal(service.con, self.fixture.home)
            run_id = journal.create_project_run(
                self.project_id, config={"limits": config.as_dict()}, targets=targets
            )
            controller_task = asyncio.create_task(ProjectController(
                service=service, project=project, orchestrator=orchestrator, run_id=run_id,
                runtime=runtime, journal=journal, config=config,
            ).run())
            pending_error = None
            try:
                await asyncio.wait_for(reviewer_a_started.wait(), timeout=2)
                assignment_b = service.con.execute(
                    "SELECT id FROM assignments WHERE task_id=?", (task_b,)
                ).fetchone()[0]
                self.assertTrue(any(
                    role == SessionRole.WORKER and subject == assignment_b
                    for role, subject, _ in runtime.active
                ))
            except BaseException as exc:
                pending_error = exc
            finally:
                release_b.set()
            result = await asyncio.wait_for(controller_task, timeout=5)
            if pending_error is not None:
                raise pending_error
            self.assertEqual(result.status, "COMPLETED", result)
            self.assertEqual(runtime.max_active[SessionRole.WORKER], 2)
            self.assertEqual(runtime.max_active[SessionRole.REVIEWER], 1)
            assignment_a = service.con.execute(
                "SELECT id FROM assignments WHERE task_id=?", (task_a,)
            ).fetchone()[0]
            submission_a = service.con.execute(
                "SELECT id FROM submissions WHERE assignment_id=?", (assignment_a,)
            ).fetchone()[0]
            worker_a_end = next(row[0] for row in runtime.timeline if row[1:4] == ("END", SessionRole.WORKER, assignment_a))
            reviewer_a_start = next(row[0] for row in runtime.timeline if row[1:4] == ("START", SessionRole.REVIEWER, submission_a))
            worker_b_start = next(row[0] for row in runtime.timeline if row[1:4] == ("START", SessionRole.WORKER, assignment_b))
            worker_b_end = next(row[0] for row in runtime.timeline if row[1:4] == ("END", SessionRole.WORKER, assignment_b))
            self.assertLess(worker_a_end, reviewer_a_start)
            self.assertLess(worker_b_start, reviewer_a_start)
            self.assertLess(reviewer_a_start, worker_b_end)

        try:
            asyncio.run(execute())
        finally:
            service.con.close()

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

    def test_nc209_task_creator_project_budget_pauses_before_second_planning_turn(self):
        _, task_ids = self.build_plan()
        service = Service(connect(self.fixture.home), self.fixture.home)
        project, orchestrator = service.auth_orchestrator(self.project_id, None)
        runtime = FakeRuntime(
            worker_turns=[], reviewer_turns=[],
            task_creator_turns=[TurnScript(structured_output={"invalid": True}), TurnScript(structured_output={"ok": True})],
        )
        targets = validate_execution_policy(service, project, [
            {"task_id": task_id, "wave": 1, "worker_profile": "routine", "parallel_safe": False, "write_surfaces": [task_id]}
            for task_id in task_ids.values()
        ])
        config = ProjectControllerConfig(max_task_creator_turns=2, max_total_task_creator_turns=1)
        journal = Journal(service.con, self.fixture.home)
        run_id = journal.create_project_run(self.project_id, config={"limits": config.as_dict()}, targets=targets)
        controller = ProjectController(
            service=service, project=project, orchestrator=orchestrator, run_id=run_id,
            runtime=runtime, journal=journal, config=config,
        )

        async def execute():
            return await controller._semantic_job(
                role=SessionRole.TASK_CREATOR,
                profile=config.task_creator_profile,
                subject_id="creator-budget-test",
                prompt="plan corrections",
                schema={"type": "object"},
                max_turns=2,
                prompt_kind="correction-planning",
                validator=lambda value: value if isinstance(value, dict) and value.get("ok") else None,
            )

        with self.assertRaisesRegex(SemanticJobBudgetExhausted, "task creator turn budget"):
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
