import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from taskledger.core import LedgerError
from taskledger.db import connect
from taskledger.controller.fakes import FakeRuntime
from taskledger.controller.model import RuntimeIdentity, SessionRole
from taskledger.operator import (
    approval_matches,
    bootstrap,
    commit_model_plan,
    format_proposal,
    preview_model_plan,
    remember_approval,
)
from taskledger.service import Service


def proposal(*, objective="Add operator command", ambiguities=None):
    return {
        "requirements": [{
            "ref": "R1", "statement": "Operators can start simply", "details": "The workflow is observable.",
            "implementation_required": True, "sources": [{"locator": "Request", "excerpt": None}],
        }],
        "tasks": [{
            "ref": "T1", "objective": objective, "implementation_scope": "src/taskledger/cli.py",
            "acceptance_criteria": ["The command succeeds"], "required_checks": ["python3 -m unittest"],
            "requirement_refs": ["R1"], "dependency_refs": [],
        }],
        "execution_policy": [{
            "task_ref": "T1", "wave": 1, "worker_profile": "routine", "parallel_safe": True,
            "write_surfaces": ["src/taskledger/cli.py"],
        }],
        "assumptions": ["Git is installed"], "ambiguities": list(ambiguities or []),
    }


class OperatorWorkflowTests(unittest.TestCase):
    def test_bootstrap_is_idempotent_and_preserves_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "feat/operator", directory], check=True)
            first = bootstrap(directory, confirm=lambda _prompt: "yes")
            self.assertEqual(first["status"], "COMPLETE")
            self.assertTrue(first["initialized"])
            self.assertEqual((root / ".gitignore").read_text().count(".taskledger/"), 1)
            profiles = root / ".codex" / "agents"
            self.assertEqual(len(tuple(profiles.glob("taskledger-*.toml"))), 4)

            customized = profiles / "taskledger-worker-routine.toml"
            customized.write_text("custom = true\n")
            second = bootstrap(directory, confirm=lambda _prompt: self.fail("initialized bootstrap must not prompt"))
            self.assertFalse(second["initialized"])
            self.assertEqual((root / ".gitignore").read_text().count(".taskledger/"), 1)
            self.assertEqual(customized.read_text(), "custom = true\n")
            self.assertIn(".codex/agents/taskledger-worker-routine.toml", second["existing"])

    def test_bootstrap_decline_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", "-b", "main", directory], check=True)
            result = bootstrap(directory, confirm=lambda _prompt: "")
            self.assertEqual(result["status"], "CANCELLED")
            self.assertFalse((Path(directory) / ".taskledger").exists())
            self.assertFalse((Path(directory) / ".codex").exists())

    def test_model_bootstrap_commits_only_generated_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", directory], check=True)
            subprocess.run(["git", "-C", directory, "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", directory, "config", "user.email", "test@example.invalid"], check=True)
            result = bootstrap(directory, confirm_branch="main", commit_setup=True)
            self.assertIsNotNone(result["setup_commit_oid"])
            files = subprocess.run(
                ["git", "-C", directory, "show", "--pretty=format:", "--name-only", "HEAD"],
                check=True, text=True, capture_output=True,
            ).stdout.split()
            self.assertEqual(files, [
                ".codex/agents/taskledger-reviewer.toml",
                ".codex/agents/taskledger-task-creator.toml",
                ".codex/agents/taskledger-worker-complex.toml",
                ".codex/agents/taskledger-worker-routine.toml",
                ".gitignore",
            ])

    def test_model_bootstrap_refuses_preexisting_dirty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", directory], check=True)
            subprocess.run(["git", "-C", directory, "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", directory, "config", "user.email", "test@example.invalid"], check=True)
            (root / "existing.txt").write_text("clean\n")
            subprocess.run(["git", "-C", directory, "add", "existing.txt"], check=True)
            subprocess.run(["git", "-C", directory, "commit", "-qm", "initial"], check=True)
            (root / "existing.txt").write_text("dirty\n")
            with self.assertRaises(LedgerError) as caught:
                bootstrap(directory, confirm_branch="main", commit_setup=True)
            self.assertEqual(caught.exception.code, "CANONICAL_WORKTREE_DIRTY")
            self.assertFalse((root / ".taskledger").exists())
            self.assertFalse((root / ".codex").exists())

    def test_approval_is_bound_to_exact_preparation_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SimpleNamespace(home=Path(directory))
            project = {"id": "project"}
            result = {"preparation_id": "prep", "proposal_hash": "sha256:one"}
            remember_approval(service, project, result, "specs/init.md")
            self.assertTrue(approval_matches(service, project, {
                "id": "prep", "proposal_hash": "sha256:one", "state": "AWAITING_APPROVAL",
            }))
            self.assertFalse(approval_matches(service, project, {
                "id": "prep", "proposal_hash": "sha256:two", "state": "AWAITING_APPROVAL",
            }))

    def test_proposal_formatter_shows_human_plan(self):
        rendered = format_proposal("specs/init.md", proposal())
        self.assertIn("Specification: specs/init.md", rendered)
        self.assertIn("T1 [routine] Add operator command", rendered)
        self.assertIn("Write surfaces: src/taskledger/cli.py", rendered)
        self.assertIn("Checks: python3 -m unittest", rendered)
        self.assertIn("Git is installed", rendered)
        self.assertNotIn('"execution_policy"', rendered)

    def test_model_preview_is_read_only_and_commit_creates_only_preparation(self):
        from tests.test_acceptance import TaskledgerAcceptance
        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fixture.setUp()
        service = None
        try:
            project_id = fixture.init()
            profiles = fixture.root / ".codex" / "agents"
            profiles.mkdir(parents=True)
            for name in ("taskledger-task-creator.toml", "taskledger-reviewer.toml", "taskledger-worker-routine.toml", "taskledger-worker-complex.toml"):
                (profiles / name).write_text('name = "test"\nmodel = "fake"\nmodel_reasoning_effort = "low"\n')
            fixture.git("add", ".codex")
            fixture.git("commit", "-qm", "profiles")
            service = Service(connect(fixture.home), fixture.home)
            project, principal = service.auth_orchestrator(project_id, None)
            runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
            request = {"spec_path": "spec.md", "proposal": proposal()}
            before = {
                table: service.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("specifications", "project_preparations", "controller_runs", "controller_sessions", "tasks")
            }
            preview = preview_model_plan(service, project, request, runtime_factory=lambda **_: runtime)
            self.assertEqual(preview["status"], "PREVIEW")
            self.assertEqual(before, {
                table: service.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            })
            committed = commit_model_plan(service, project, principal, {
                **request, "approve_plan_hash": preview["plan_hash"],
            }, runtime_factory=lambda **_: runtime)
            self.assertEqual(committed["status"], "READY")
            self.assertEqual(service.con.execute("SELECT origin FROM project_preparations").fetchone()[0], "INVOKING_MODEL")
            self.assertEqual(service.con.execute("SELECT COUNT(*) FROM controller_runs").fetchone()[0], 0)
            self.assertEqual(service.con.execute("SELECT COUNT(*) FROM controller_sessions").fetchone()[0], 0)
            self.assertEqual(service.con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            row = __import__("taskledger.controller.initial_planning", fromlist=["preparation_row"]).preparation_row(service, committed["preparation_id"])
            self.assertTrue(approval_matches(service, project, row))
            from taskledger.application.queries import ConsoleQueries
            snapshot = ConsoleQueries(service, project, "test", lambda _run_id: False).snapshot()
            self.assertTrue(snapshot.preparation.get("operator_approved"))
            from taskledger.cli import start_prepared_project
            from taskledger.controller.project import ProjectControllerResult
            with patch("taskledger.controller.app_server.AppServerRuntime", return_value=runtime), patch(
                "taskledger.cli.run_project_controller", return_value=ProjectControllerResult("COMPLETED", "execution-run"),
            ):
                started = start_prepared_project(service, project, principal, {
                    "preparation_id": committed["preparation_id"],
                    "approve_proposal_hash": committed["proposal_hash"], "live": True,
                }, announce=False)
            self.assertEqual(started["status"], "COMPLETED")
            self.assertEqual(service.con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        finally:
            if service is not None:
                service.con.close()
            fixture.tearDown()

    def test_model_commit_rejects_plan_drift_without_persistence(self):
        from tests.test_acceptance import TaskledgerAcceptance
        fixture = TaskledgerAcceptance("test_version_flag_returns_installed_version")
        fixture.setUp()
        service = None
        try:
            project_id = fixture.init()
            profiles = fixture.root / ".codex" / "agents"
            profiles.mkdir(parents=True)
            for name in ("taskledger-task-creator.toml", "taskledger-reviewer.toml", "taskledger-worker-routine.toml", "taskledger-worker-complex.toml"):
                (profiles / name).write_text('name = "test"\nmodel = "fake"\nmodel_reasoning_effort = "low"\n')
            fixture.git("add", ".codex")
            fixture.git("commit", "-qm", "profiles")
            service = Service(connect(fixture.home), fixture.home)
            project, principal = service.auth_orchestrator(project_id, None)
            runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
            preview = preview_model_plan(service, project, {"spec_path": "spec.md", "proposal": proposal()}, runtime_factory=lambda **_: runtime)
            with self.assertRaises(LedgerError) as caught:
                commit_model_plan(service, project, principal, {
                    "spec_path": "spec.md", "proposal": proposal(objective="Changed after approval"),
                    "approve_plan_hash": preview["plan_hash"],
                }, runtime_factory=lambda **_: runtime)
            self.assertEqual(caught.exception.code, "PREPARATION_STALE")
            with self.assertRaises(LedgerError):
                commit_model_plan(service, project, principal, {
                    "spec_path": "spec.md", "proposal": proposal(), "limits": {"max_workers": 3},
                    "approve_plan_hash": preview["plan_hash"],
                }, runtime_factory=lambda **_: runtime)
            (fixture.root / "baseline.txt").write_text("changed baseline\n")
            fixture.git("add", "baseline.txt")
            fixture.git("commit", "-qm", "change baseline")
            with self.assertRaises(LedgerError):
                commit_model_plan(service, project, principal, {
                    "spec_path": "spec.md", "proposal": proposal(), "approve_plan_hash": preview["plan_hash"],
                }, runtime_factory=lambda **_: runtime)
            spec_preview = preview_model_plan(service, project, {"spec_path": "spec.md", "proposal": proposal()}, runtime_factory=lambda **_: runtime)
            (fixture.root / "spec.md").write_text("Changed specification\n")
            fixture.git("add", "spec.md")
            fixture.git("commit", "-qm", "change specification")
            with self.assertRaises(LedgerError):
                commit_model_plan(service, project, principal, {
                    "spec_path": "spec.md", "proposal": proposal(), "approve_plan_hash": spec_preview["plan_hash"],
                }, runtime_factory=lambda **_: runtime)
            profile_preview = preview_model_plan(service, project, {"spec_path": "spec.md", "proposal": proposal()}, runtime_factory=lambda **_: runtime)
            (profiles / "taskledger-worker-routine.toml").write_text('name = "test"\nmodel = "changed"\nmodel_reasoning_effort = "high"\n')
            fixture.git("add", ".codex/agents/taskledger-worker-routine.toml")
            fixture.git("commit", "-qm", "change profile")
            drifted_runtime = FakeRuntime(worker_turns=[], reviewer_turns=[])
            drifted_runtime.identity_overrides[(SessionRole.WORKER, "routine")] = RuntimeIdentity(
                model="changed", effort="high", agent_config_hash="changed-profile",
                sandbox={"type": "workspaceWrite", "cwd": str(fixture.root)}, protocol_identity="fake-runtime-v1",
            )
            with self.assertRaises(LedgerError):
                commit_model_plan(service, project, principal, {
                    "spec_path": "spec.md", "proposal": proposal(), "approve_plan_hash": profile_preview["plan_hash"],
                }, runtime_factory=lambda **_: drifted_runtime)
            self.assertEqual(service.con.execute("SELECT COUNT(*) FROM specifications").fetchone()[0], 0)
            self.assertEqual(service.con.execute("SELECT COUNT(*) FROM project_preparations").fetchone()[0], 0)
        finally:
            if service is not None:
                service.con.close()
            fixture.tearDown()

    def test_main_routes_human_command_without_json_envelope(self):
        from taskledger.cli import main
        with patch("taskledger.cli._human_bootstrap") as command:
            self.assertEqual(main(["bootstrap"]), 0)
        command.assert_called_once()

    def test_model_preview_keeps_machine_json_envelope(self):
        from taskledger.cli import main
        service = MagicMock()
        service.auth_orchestrator.return_value = ({"id": "project"}, {"id": "principal"})
        output = StringIO()
        with tempfile.NamedTemporaryFile("w", suffix=".json") as request:
            json.dump({"spec_path": "spec.md", "proposal": proposal()}, request)
            request.flush()
            with patch("taskledger.cli.service_for_command", return_value=service), patch(
                "taskledger.operator.preview_model_plan", return_value={"status": "PREVIEW", "plan_hash": "sha256:plan"},
            ), redirect_stdout(output):
                self.assertEqual(main(["plan", "preview", "--input", request.name]), 0)
        envelope = json.loads(output.getvalue())
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["command"], "plan.preview")

    def test_human_prepare_keeps_task_creator_result_and_prompt(self):
        from taskledger.cli import _human_prepare
        with tempfile.TemporaryDirectory() as directory:
            service = SimpleNamespace(home=Path(directory), con=MagicMock())
            service.auth_orchestrator = MagicMock(return_value=({"id": "project"}, {"id": "principal"}))
            args = SimpleNamespace(
                action=None, spec="spec.md", input=None, repo=None, confirm_branch=None,
                commit_setup=False, project=None, token=None,
            )
            prepared = {"status": "AWAITING_APPROVAL", "preparation_id": "prep", "proposal_hash": "sha256:proposal", "proposal": proposal()}
            output = StringIO()
            with patch("taskledger.cli.git.inspect", return_value={"root": directory}), patch(
                "taskledger.cli.prepare_project_command", return_value=prepared,
            ) as creator_path, patch("taskledger.cli.service_for_command", return_value=service), patch(
                "builtins.input", return_value="yes",
            ) as approval_prompt, redirect_stdout(output):
                _human_prepare(args)
            creator_path.assert_called_once()
            approval_prompt.assert_called_once_with("\nApprove this plan? [y/N] ")
            self.assertIn("Plan approved", output.getvalue())

    def test_skill_eval_fixture_matrix_is_complete(self):
        root = Path(__file__).parents[1]
        fixtures = sorted((root / "evals" / "taskledger_skill" / "fixtures").iterdir())
        self.assertEqual([item.name for item in fixtures], [
            "01-routine", "02-complex", "03-parallel", "04-overlap", "05-ambiguity", "06-existing",
        ])
        self.assertTrue(all((item / "spec.md").is_file() for item in fixtures))
        harness = root / "scripts" / "evaluate_taskledger_skill.py"
        result = subprocess.run(["python3", str(harness), "--help"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--repetitions", result.stdout)
        self.assertIn("--thinking", result.stdout)

    def test_packaged_profiles_match_skill_templates(self):
        root = Path(__file__).parents[1]
        for source in (root / "skills" / "taskledger" / "assets").glob("*.toml"):
            packaged = root / "src" / "taskledger" / "assets" / source.name
            self.assertEqual(packaged.read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
