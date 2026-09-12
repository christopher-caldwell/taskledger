from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"src"))


class TaskledgerAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.home = self.root / ".taskledger"
        self.root.mkdir()
        self.git("init", "-q", "-b", "feat/ledger")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / ".gitignore").write_text(".taskledger/\n")
        (self.root / "spec.md").write_text("Specification\n")
        (self.root / "code.txt").write_text("base\n")
        self.git("add", ".gitignore", "spec.md", "code.txt")
        self.git("commit", "-qm", "initial")

    def tearDown(self): self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def command(self, resource, action, data=None, *, token=None, project=None, repo=None, confirm=None, ledger_home=None):
        args=[sys.executable, "-m", "taskledger", resource, action]
        args += ["--input", "-"]
        if project: args += ["--project", project]
        if token: args += ["--token", token]
        if repo: args += ["--repo", repo]
        if confirm: args += ["--confirm-branch", confirm]
        env={**os.environ, "PYTHONPATH":str(ROOT / "src")}
        env.pop("TASKLEDGER_HOME", None)
        if ledger_home is not None: env["TASKLEDGER_HOME"]=str(ledger_home)
        result=subprocess.run(args, input=json.dumps(data or {}), text=True, capture_output=True, env=env, cwd=self.root)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def init(self):
        self.assertFalse(self.home.exists())
        code, first=self.command("project", "init", repo=str(self.root))
        self.assertEqual(code, 0); self.assertTrue(first["data"]["confirmation_required"])
        self.assertTrue(first["data"]["ledger_directory_ignored"])
        self.assertFalse(self.home.exists())
        code, second=self.command("project", "init", repo=str(self.root), confirm="feat/ledger")
        self.assertEqual(code, 0)
        self.assertTrue((self.home / "taskledger.sqlite3").is_file())
        return second["data"]["project_id"]

    def assignment_token(self, assignment):
        self.assertNotIn("worker_token", assignment["data"])
        return Path(assignment["data"]["worker_token_path"]).read_text().strip()

    def db_scalar(self, statement, parameters=()):
        con=sqlite3.connect(self.home/"taskledger.sqlite3")
        try:
            return con.execute(statement,parameters).fetchone()[0]
        finally:
            con.close()

    def test_version_flag_returns_installed_version(self):
        env={**os.environ,"PYTHONPATH":str(ROOT/"src")}
        result=subprocess.run([sys.executable,"-m","taskledger","--version"],text=True,capture_output=True,env=env,cwd=self.root)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stderr,"")
        output=json.loads(result.stdout)
        self.assertEqual(output["command"],"version")
        self.assertEqual(output["data"]["version"],"0.6.0")
        manifest=json.loads((ROOT/".codex-plugin"/"plugin.json").read_text())
        self.assertEqual(manifest["version"],"0.6.0")

    def test_nc_cli_controller_report_succeeds_without_codex_executable(self):
        from taskledger.controller.journal import Journal
        from taskledger.db import connect

        project_id = self.init()
        con = connect(self.home)
        journal = Journal(con, self.home)
        run_id = journal.create_run(project_id, mode="PROJECT", config={"manifest": {"scope": "POST_APPROVAL_EXECUTION"}})
        journal.finish_run(run_id, "COMPLETED")
        con.close()
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PATH": str(Path(shutil.which("git")).parent)}
        result = subprocess.run(
            [sys.executable, "-m", "taskledger", "controller", "report", "--input", "-", "--project", project_id],
            input=json.dumps({"run_id": run_id}), text=True, capture_output=True, env=env, cwd=self.root,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)["data"]
        self.assertEqual(report["identity"]["run_id"], run_id)
        self.assertEqual(report["quality"]["provider_detail"]["status"], "UNAVAILABLE")

    def test_skill_declares_context_free_worker_spawn_contract(self):
        skill=(ROOT/"skills"/"taskledger"/"SKILL.md").read_text()
        self.assertIn("foreground Python controller",skill)
        self.assertIn("controller run-project",skill)
        self.assertIn("Prefer `routine`",skill)
        self.assertIn("Classify parallel safety separately",skill)
        self.assertIn("prospective write set",skill)
        self.assertIn("handle a new request directly by default",skill)
        self.assertIn("second rejection",skill)
        self.assertIn("`required_checks`",skill)
        self.assertIn("Concrete routing examples",skill)
        self.assertIn("small complex foundation followed by routine rollout tasks",skill)
        self.assertIn("complete project suite",skill)
        self.assertIn("registered specifications as frozen",skill)
        self.assertIn("primary purpose is to re-audit already integrated work",skill)
        self.assertIn("exhaustive public command and input registry",skill)
        self.assertIn("safe worktree prerequisites",skill)
        self.assertIn("`project cleanup`",skill)
        self.assertIn("retains every assignment branch and commit",skill)
        self.assertIn("Retained workers and vertical checkpoints",skill)
        self.assertIn("`project wait`",skill)
        self.assertIn("`evidence export`",skill)
        self.assertIn("correction packet",skill)
        self.assertNotIn("primary orchestrator remains responsible",skill)

        installation=(ROOT/"docs"/"AI_ORCHESTRATED_TOOL_INSTALLATION.md").read_text()
        self.assertIn("Installation is a set of contracts",installation)
        self.assertIn("taskledger@personal",installation)
        self.assertIn("Blueprint for a similar model-assisted tool",installation)

        for profile in ("routine","complex"):
            template=(ROOT/"skills"/"taskledger"/"assets"/f"taskledger-worker-{profile}.toml").read_text()
            self.assertIn("persisted worker context",template)
            self.assertIn("task.required_checks",template)
            self.assertIn("registered_specifications",template)
            self.assertIn("focused checks while iterating",template)
            self.assertIn("progress probe",template)
            self.assertIn("`correction_packet`",template)
            self.assertIn("taskledger worker checkpoint",template)
            self.assertIn("taskledger worker check",template)

        reviewer=(ROOT/"skills"/"taskledger"/"assets"/"taskledger-reviewer.toml").read_text()
        self.assertIn('name = "taskledger_reviewer"',reviewer)
        self.assertIn('model = "gpt-6-astra"',reviewer)
        self.assertIn("Do not edit files",reviewer)
        creator=(ROOT/"skills"/"taskledger"/"assets"/"taskledger-task-creator.toml").read_text()
        self.assertIn('name = "taskledger_task_creator"',creator)
        self.assertIn("Do not schedule",creator)

        commands=(ROOT/"skills"/"taskledger"/"references"/"commands.md").read_text()
        self.assertIn("exhaustive public command and input registry",commands)
        self.assertIn("do not probe unlisted commands or flags",commands)
        self.assertIn("`project preflight`",commands)
        self.assertIn("`submission check`",commands)
        self.assertIn("`evidence export`",commands)

    def test_existing_assignment_rows_receive_complex_profile_during_schema_upgrade(self):
        from taskledger.db import connect

        prior_schema_home=Path(self.tmp.name)/"prior-schema-ledger"
        prior_schema_home.mkdir()
        con=sqlite3.connect(prior_schema_home/"taskledger.sqlite3")
        con.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES(1,'2026-01-01T00:00:00.000Z');
            CREATE TABLE assignments(
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,
              task_revision INTEGER NOT NULL,attempt_number INTEGER NOT NULL,
              state TEXT NOT NULL,base_commit_oid TEXT NOT NULL,branch_name TEXT NOT NULL,
              worktree_path TEXT NOT NULL,context_json TEXT NOT NULL,created_at TEXT NOT NULL,
              activated_at TEXT,closed_at TEXT,revoked_at TEXT,revocation_reason TEXT,
              UNIQUE(task_id,attempt_number));
            INSERT INTO assignments VALUES(
              'assignment','project','task',1,1,'CLOSED','base','branch','worktree','{}',
              '2026-01-01T00:00:00.000Z',NULL,NULL,NULL,NULL);
        """)
        con.close()

        migrated=connect(prior_schema_home)
        self.assertEqual(migrated.execute("SELECT worker_profile FROM assignments").fetchone()[0],"complex")
        self.assertEqual([row[0] for row in migrated.execute("SELECT version FROM schema_migrations ORDER BY version")],[1,2,3,4,5,6,7,8,9,10])
        migrated.close()

    def test_task_create_confirms_success_after_post_commit_error(self):
        from taskledger.db import connect, transaction as real_transaction
        from taskledger.service import Service

        project_id=self.init()
        _,spec=self.command("spec","register",{"relative_path":"spec.md"},project=project_id)
        requirement_ids=[]
        for number in range(6):
            _,requirement=self.command("requirement","create",{"statement":f"Requirement {number}","details":f"Requirement {number} is observable","implementation_required":True,"sources":[{"specification_id":spec["data"]["specification_id"],"locator":str(number)}]},project=project_id)
            requirement_ids.append(requirement["data"]["requirement_id"])
        service=Service(connect(self.home),self.home)
        project,principal=service.auth_orchestrator(project_id,None)
        data={"objective":"Deliver a cohesive public API","implementation_scope":"Public API and focused tests","acceptance_criteria":[f"Criterion {number}" for number in range(6)],"requirement_ids":requirement_ids,"dependency_task_ids":[]}

        @contextmanager
        def commit_then_raise(con):
            with real_transaction(con):yield con
            raise sqlite3.OperationalError("simulated error after visible commit")

        with patch("taskledger.service.transaction",commit_then_raise):
            created=service.create_task(project,principal,data)
        self.assertEqual(created["state"],"PLANNED")
        con=sqlite3.connect(self.home/"taskledger.sqlite3")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tasks WHERE id=?",(created["task_id"],)).fetchone()[0],1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM task_requirement_links WHERE task_id=?",(created["task_id"],)).fetchone()[0],6)
        con.close();service.con.close()

    def test_failed_assignment_preparation_accepts_base_oid_recovery_proof(self):
        from taskledger.core import LedgerError
        from taskledger.db import connect
        from taskledger.service import Service
        from taskledger import git as taskledger_git

        project_id=self.init();_,task_id=self.setup_task(project_id)
        self.command("plan","validate",{},project=project_id)
        service=Service(connect(self.home),self.home)
        project,principal=service.auth_orchestrator(project_id,None)
        base=taskledger_git.oid(self.root)
        real_run=taskledger_git.run

        def fail_before_worktree(repo,args,*,check=True):
            if list(args[:2])==["worktree","add"]:raise LedgerError("GIT_COMMAND_FAILED","simulated worktree creation failure")
            return real_run(repo,args,check=check)

        with patch("taskledger.service.git.run",side_effect=fail_before_worktree):
            with self.assertRaises(LedgerError) as failed:service.assignment_create(project,principal,{"task_id":task_id,"worker_profile":"complex"})
        self.assertEqual(failed.exception.code,"RECOVERY_REQUIRED")
        operation=service.con.execute("SELECT * FROM operations WHERE project_id=? AND kind='ASSIGNMENT_PREPARATION' AND state='UNCERTAIN'",(project_id,)).fetchone()
        assignment=service.con.execute("SELECT * FROM assignments WHERE id=?",(operation["entity_id"],)).fetchone()
        expected=json.loads(operation["expected_state_json"])
        self.assertEqual(expected["base"],base)
        self.assertFalse(Path(expected["worktree"]).exists())
        self.assertIsNone(taskledger_git.oid_or_none(self.root,f"refs/heads/{expected['branch']}"))

        recovered=service.recover_operation(project,principal,{"operation_id":operation["id"],"current_oid":base},False)
        self.assertEqual(recovered["state"],"FAILED")
        self.assertEqual(service.con.execute("SELECT state FROM assignments WHERE id=?",(assignment["id"],)).fetchone()[0],"REVOKED")
        self.assertEqual(service.con.execute("SELECT state FROM tasks WHERE id=?",(task_id,)).fetchone()[0],"PLANNED")
        self.assertEqual(service.con.execute("SELECT COUNT(*) FROM blockers WHERE scope_type='OPERATION' AND scope_id=? AND state='OPEN'",(operation["id"],)).fetchone()[0],0)
        service.con.close()

    def setup_task(self, project, *, objective="Change a file", dependencies=None):
        _, spec=self.command("spec", "register", {"relative_path":"spec.md"}, project=project)
        _, req=self.command("requirement", "create", {"statement":objective, "details":objective + " visibly", "implementation_required":True, "sources":[{"specification_id":spec["data"]["specification_id"],"locator":"1"}]}, project=project)
        _, task=self.command("task", "create", {"objective":objective, "implementation_scope":"README", "acceptance_criteria":["File changed"], "requirement_ids":[req["data"]["requirement_id"]], "dependency_task_ids":dependencies or []}, project=project)
        return req["data"]["requirement_id"], task["data"]["task_id"]

    def test_init_requires_exact_branch_and_rejects_unknown_request_fields(self):
        code, output=self.command("project", "init", repo=str(self.root), confirm="main")
        self.assertEqual(code, 3)
        self.assertEqual(output["error"]["code"], "BRANCH_CONFIRMATION_MISMATCH")
        project=self.init()
        code, output=self.command("spec", "register", {"relative_path":"spec.md", "surprise":True}, project=project)
        self.assertEqual(code, 2)
        self.assertEqual(output["error"]["code"], "UNKNOWN_FIELD")

    def test_repeated_init_continues_the_existing_project(self):
        project=self.init()
        code, detected=self.command("project", "init", repo=str(self.root))
        self.assertEqual(code, 0)
        self.assertTrue(detected["data"]["already_initialized"])
        self.assertFalse(detected["data"]["confirmation_required"])
        self.assertEqual(detected["data"]["project_id"], project)
        self.assertEqual(detected["data"]["next_action"], "project.recover")
        self.assertIn("already initialized", detected["data"]["message"])
        code, confirmed=self.command("project", "init", repo=str(self.root), confirm="feat/ledger")
        self.assertEqual(code, 0)
        self.assertEqual(confirmed["data"]["project_id"], project)

    def test_init_requires_ignored_local_ledger_directory(self):
        (self.root / ".gitignore").write_text("")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "remove ledger ignore")
        code, detected=self.command("project", "init", repo=str(self.root))
        self.assertEqual(code, 0)
        self.assertFalse(detected["data"]["ledger_directory_ignored"])
        self.assertFalse(self.home.exists())
        code, blocked=self.command("project", "init", repo=str(self.root), confirm="feat/ledger")
        self.assertEqual(code, 5)
        self.assertEqual(blocked["error"]["code"], "LEDGER_DIRECTORY_NOT_IGNORED")
        self.assertFalse(self.home.exists())

    def test_explicit_taskledger_home_supports_current_external_store(self):
        external_home=Path(self.tmp.name)/"external-home"
        code, detected=self.command("project", "init", repo=str(self.root), ledger_home=external_home)
        self.assertEqual(code, 0)
        self.assertFalse(external_home.exists())
        self.assertTrue(detected["data"]["ledger_directory_ignored"])
        code, initialized=self.command("project", "init", repo=str(self.root), confirm="feat/ledger", ledger_home=external_home)
        self.assertEqual(code, 0)
        self.assertTrue((external_home/"taskledger.sqlite3").is_file())
        self.assertFalse(self.home.exists())

    def test_storage_failure_does_not_claim_recovery_is_required(self):
        self.home.write_text("not a directory")
        code, failed=self.command("project", "init", repo=str(self.root), confirm="feat/ledger")
        self.assertEqual(code, 3)
        self.assertEqual(failed["error"]["code"], "LEDGER_STORAGE_UNAVAILABLE")
        self.assertEqual(failed["error"]["allowed_actions"], [])

    def test_unborn_repository_recovers_and_requests_initial_commit_before_assignment(self):
        self.git("update-ref", "-d", "refs/heads/feat/ledger")
        code, detected=self.command("project", "init", repo=str(self.root))
        self.assertEqual(code, 0)
        self.assertFalse(detected["data"]["has_commits"])
        code, initialized=self.command("project", "init", repo=str(self.root), confirm="feat/ledger")
        self.assertEqual(code, 0)
        self.assertTrue(initialized["data"]["initial_commit_required"])
        project=initialized["data"]["project_id"]
        code, recovery=self.command("project", "recover", {}, project=project)
        self.assertEqual(code, 0)
        self.assertIsNone(recovery["data"]["project"]["canonical_head_oid"])
        self.assertTrue(recovery["data"]["project"]["initial_commit_required"])
        self.assertIn("INITIAL_COMMIT_REQUIRED", {reason["code"] for reason in recovery["data"]["completion"]["blocking_reasons"]})
        _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        code, recovery=self.command("project", "recover", {}, project=project)
        self.assertEqual(code, 0)
        self.assertEqual(recovery["data"]["tasks"]["eligible"], [])
        code, blocked=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"complex"}, project=project)
        self.assertEqual(code, 5)
        self.assertEqual(blocked["error"]["code"], "INITIAL_COMMIT_REQUIRED")
        self.assertIn("first commit", blocked["error"]["message"])

    def test_full_task_backed_flow_and_worker_scope(self):
        project=self.init()
        rid, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        code, missing_profile=self.command("assignment", "create", {"task_id":task_id}, project=project)
        self.assertEqual(code,2);self.assertEqual(missing_profile["error"]["code"],"INVALID_REQUEST")
        code, invalid_profile=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"default"}, project=project)
        self.assertEqual(code,2);self.assertEqual(invalid_profile["error"]["code"],"INVALID_REQUEST")
        _, assignment=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"routine"}, project=project)
        worker_token=self.assignment_token(assignment)
        self.assertEqual(assignment["data"]["worker_profile"],"routine")
        self.assertNotIn("context",assignment["data"])
        self.assertIn("context_hash",assignment["data"])
        _,context=self.command("worker","context",{},token=worker_token)
        self.assertEqual(context["data"]["context_hash"],assignment["data"]["context_hash"])
        self.assertEqual(context["data"]["context"]["assignment"]["worker_profile"],"routine")
        self.assertEqual(
            context["data"]["context"]["registered_specifications"],
            [{"id": self.db_scalar("SELECT id FROM specifications"), "relative_path": "spec.md"}],
        )
        _,active_resume=self.command("project","resume",{},project=project)
        self.assertEqual(active_resume["data"]["schema_version"],4)
        self.assertEqual(active_resume["data"]["active_assignments"][0]["worker_profile"],"routine")
        _,unchanged=self.command("worker","context",{"if_none_match":context["data"]["context_hash"],"if_dynamic_none_match":context["data"]["dynamic_hash"]},token=worker_token)
        self.assertTrue(unchanged["data"]["context_not_modified"]);self.assertTrue(unchanged["data"]["dynamic_not_modified"])
        # A worker credential cannot reach an orchestrator operation.
        code, denied=self.command("plan", "validate", {}, project=project, token=worker_token)
        self.assertEqual(code, 4); self.assertEqual(denied["error"]["code"], "AUTHORIZATION_DENIED")
        worktree=Path(assignment["data"]["worktree_path"])
        (worktree / "README").write_text("done\n")
        _, submission=self.command("worker", "submit", {"summary":"Done", "evidence":[{"label":"test","details":"passed"}], "risks":[], "unresolved_questions":[], "follow_up_work":[]}, token=worker_token)
        checkpoint_identity=subprocess.run(
            ["git","-C",str(worktree),"show","-s","--format=%an%x00%ae%x00%cn%x00%ce",submission["data"]["head_commit_oid"]],
            check=True,text=True,capture_output=True,
        ).stdout.strip().split("\0")
        self.assertEqual(checkpoint_identity,["Test","test@example.invalid","Test","test@example.invalid"])
        _,review=self.command("submission","review-context",{"submission_id":submission["data"]["submission_id"]},project=project)
        self.assertEqual(review["data"]["submission"]["head_commit_oid"],submission["data"]["head_commit_oid"])
        self.assertEqual(review["data"]["submission"]["worker_profile"],"routine")
        self.assertTrue(review["data"]["diff"]["complete"]);self.assertFalse(review["data"]["diff"]["patch_included"])
        audit_payload=json.loads(self.db_scalar("SELECT payload_json FROM audit_events WHERE event_type='ASSIGNMENT_ACTIVATED'"))
        self.assertEqual(audit_payload["worker_profile"],"routine")
        criterion_id=self.db_scalar("select id from task_acceptance_criteria")
        _, accepted=self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion_id,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"verified"}, project=project)
        integrated_head=subprocess.run(
            ["git","-C",str(self.root),"rev-parse","HEAD"],check=True,text=True,capture_output=True,
        ).stdout.strip()
        integration_identity=subprocess.run(
            ["git","-C",str(self.root),"show","-s","--format=%an%x00%ae%x00%cn%x00%ce",integrated_head],
            check=True,text=True,capture_output=True,
        ).stdout.strip().split("\0")
        self.assertEqual(integration_identity,["Test","test@example.invalid","Test","test@example.invalid"])
        _,ready=self.command("requirement","list",{"filter":"ready"},project=project)
        self.assertEqual([x["id"] for x in ready["data"]["requirements"]],[rid])
        _,resume=self.command("project","resume",{},project=project)
        self.assertEqual(resume["data"]["schema_version"],4)
        self.assertEqual(resume["data"]["active_assignments"],[])
        _,same=self.command("project","resume",{"cursor":resume["data"]["cursor"]},project=project)
        self.assertTrue(same["data"]["not_modified"])
        self.command("requirement", "verify", {"requirement_id":rid,"evidence":[{"label":"manual","details":"works"}],"notes":"complete"}, project=project)
        code, completed=self.command("project", "complete", {}, project=project)
        self.assertEqual(code, 0); self.assertTrue(completed["data"]["completed"])
        self.assertEqual(completed["data"]["worktree_cleanup"]["removed_count"],1)
        self.assertTrue(completed["data"]["worktree_cleanup"]["branches_retained"])
        self.assertFalse(worktree.exists())
        branch_exists=subprocess.run(["git","-C",str(self.root),"show-ref","--verify","--quiet",f"refs/heads/{assignment['data']['branch_name']}"]).returncode
        self.assertEqual(branch_exists,0)
        _, repeated_cleanup=self.command("project","cleanup",{},project=project)
        self.assertEqual(repeated_cleanup["data"]["removed_count"],0)
        self.assertEqual(repeated_cleanup["data"]["already_absent_count"],1)

    def test_completed_project_cleanup_preserves_dirty_worktree_for_retry(self):
        project=self.init()
        code,not_completed=self.command("project","cleanup",{},project=project)
        self.assertEqual(code,3);self.assertEqual(not_completed["error"]["code"],"PROJECT_NOT_COMPLETED")
        rid,task_id=self.setup_task(project);self.command("plan","validate",{},project=project)
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"routine"},project=project)
        worktree=Path(assignment["data"]["worktree_path"]);(worktree/"README").write_text("done\n")
        _,submission=self.command("worker","submit",{"summary":"Done","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=self.assignment_token(assignment))
        criterion_id=self.db_scalar("SELECT id FROM task_acceptance_criteria WHERE task_id=?",(task_id,))
        self.command("submission","verify",{"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion_id,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"verified"},project=project)
        self.command("requirement","verify",{"requirement_id":rid,"evidence":[{"label":"manual","details":"works"}],"notes":"complete"},project=project)
        (worktree/"keep-me.txt").write_text("uncommitted recovery state\n")
        _,completed=self.command("project","complete",{},project=project)
        cleanup=completed["data"]["worktree_cleanup"]
        self.assertEqual(cleanup["removed_count"],0);self.assertEqual(cleanup["skipped"][0]["reason"],"WORKTREE_DIRTY")
        self.assertTrue(worktree.exists())
        (worktree/"keep-me.txt").unlink()
        _,retried=self.command("project","cleanup",{},project=project)
        self.assertEqual(retried["data"]["removed_count"],1);self.assertFalse(worktree.exists())

    def test_direct_requirement_and_specification_review_gate(self):
        project=self.init()
        _, spec=self.command("spec", "register", {"relative_path":"spec.md"}, project=project)
        sid=spec["data"]["specification_id"]
        _, direct=self.command("requirement", "create", {"statement":"Document is present", "details":"The registered document is observable", "implementation_required":False, "sources":[{"specification_id":sid,"locator":"1"}]}, project=project)
        self.assertEqual(self.command("plan", "validate", {}, project=project)[1]["data"]["valid"], True)
        self.assertEqual(self.command("requirement", "verify", {"requirement_id":direct["data"]["requirement_id"],"evidence":[{"label":"inspection","details":"present"}],"notes":"directly satisfied"}, project=project)[0], 0)
        # Once planning has started, a byte change creates a review gate rather than silently changing the baseline.
        (self.root / "spec.md").write_text("Changed specification\n")
        _, check=self.command("spec", "check", {}, project=project)
        self.assertEqual(len(check["data"]["pending_reviews"]), 1)
        code, blocked=self.command("requirement", "verify", {"requirement_id":direct["data"]["requirement_id"],"evidence":[{"label":"inspection","details":"present"}],"notes":"again"}, project=project)
        self.assertEqual(code, 3); self.assertEqual(blocked["error"]["code"], "PLAN_INVALID")
        review=check["data"]["pending_reviews"][0]
        self.assertEqual(self.command("spec", "review", {"review_id":review["id"],"resolution":"APPROVE_REVISION","affected_requirement_ids":[],"affected_task_ids":[],"no_existing_items_affected":True,"summary":"No existing requirement is affected."}, project=project)[0], 0)

    def test_dependency_is_not_satisfied_until_integration(self):
        project=self.init()
        rid_a, task_a=self.setup_task(project, objective="A")
        # A second independently traced requirement prevents a direct-coverage contradiction.
        spec_id=self.db_scalar("select id from specifications")
        _, req_b=self.command("requirement", "create", {"statement":"B", "details":"B visibly", "implementation_required":True, "sources":[{"specification_id":spec_id,"locator":"1"}]}, project=project)
        _, task_b=self.command("task", "create", {"objective":"B", "implementation_scope":"README", "acceptance_criteria":["B done"], "requirement_ids":[req_b["data"]["requirement_id"]], "dependency_task_ids":[task_a]}, project=project)
        self.command("plan", "validate", {}, project=project)
        code, output=self.command("assignment", "create", {"task_id":task_b["data"]["task_id"],"worker_profile":"routine"}, project=project)
        self.assertEqual(code, 3); self.assertEqual(output["error"]["code"], "DEPENDENCIES_UNSATISFIED")
        _, assignment=self.command("assignment", "create", {"task_id":task_a,"worker_profile":"complex"}, project=project)
        (Path(assignment["data"]["worktree_path"]) / "README").write_text("A complete\n")
        _, submission=self.command("worker", "submit", {"summary":"A","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=self.assignment_token(assignment))
        criterion=self.db_scalar("select id from task_acceptance_criteria where task_id=?",(task_a,))
        self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"ok"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"A accepted"}, project=project)
        code, eligible=self.command("assignment", "create", {"task_id":task_b["data"]["task_id"],"worker_profile":"routine"}, project=project)
        self.assertEqual(code, 0); self.assertEqual(eligible["data"]["task_id"], task_b["data"]["task_id"])

    def test_detached_head_is_rejected_without_project_row(self):
        self.git("checkout", "--detach", "-q")
        code, output=self.command("project", "init", repo=str(self.root))
        self.assertEqual(code, 5)
        self.assertEqual(output["error"]["code"], "DETACHED_HEAD")
        self.assertFalse((self.home / "credentials").exists())

    def test_rejection_preserves_history_and_allows_correction(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"routine"}, project=project)
        worker_token=self.assignment_token(assignment); worktree=Path(assignment["data"]["worktree_path"])
        (worktree / "README").write_text("first\n")
        _, first=self.command("worker", "submit", {"summary":"first", "evidence":[{"label":"test","details":"first"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        criterion=self.db_scalar("select id from task_acceptance_criteria")
        code, rejected=self.command("submission", "verify", {"submission_id":first["data"]["submission_id"],"outcome":"REJECTED","criterion_results":[{"criterion_id":criterion,"satisfied":False,"evidence":"not sufficient"}],"behavior_matches_intent":False,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":"Implement the missing behavior.","notes":"rejected"}, project=project)
        self.assertEqual(code, 0); self.assertEqual(rejected["data"]["outcome"], "REJECTED")
        _,correction_context=self.command("worker","context",{},token=worker_token)
        packet=correction_context["data"]["dynamic"]["correction_packet"]
        self.assertEqual(packet["reviewed_submission_id"],first["data"]["submission_id"])
        self.assertEqual(packet["reviewed_commit_oid"],first["data"]["head_commit_oid"])
        self.assertEqual(packet["corrections"],"Implement the missing behavior.")
        self.assertEqual(packet["failed_criteria"][0]["criterion_id"],criterion)
        self.assertEqual(len(packet["packet_hash"]),64)
        (worktree / "README").write_text("corrected\n")
        code, corrected=self.command("worker", "submit", {"summary":"corrected", "evidence":[{"label":"test","details":"corrected"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        self.assertEqual(code, 0); self.assertEqual(corrected["data"]["state"], "PENDING")
        _,superseded=self.command("worker","context",{},token=worker_token)
        self.assertIsNone(superseded["data"]["dynamic"]["correction_packet"])
        self.assertEqual(self.db_scalar("select count(*) from submissions"), 2)

    def test_required_checks_must_be_reported_as_successful_submission_evidence(self):
        project=self.init()
        _,spec=self.command("spec","register",{"relative_path":"spec.md"},project=project)
        _,req=self.command("requirement","create",{"statement":"Checked change","details":"The change passes its required check","implementation_required":True,"sources":[{"specification_id":spec["data"]["specification_id"],"locator":"1"}]},project=project)
        required="python3 -c \"from pathlib import Path; assert Path('README').read_text() == 'checked\\n'\""
        _,task=self.command("task","create",{"objective":"Checked change","implementation_scope":"README","acceptance_criteria":["File changed"],"required_checks":[required],"requirement_ids":[req["data"]["requirement_id"]],"dependency_task_ids":[]},project=project)
        self.command("plan","validate",{},project=project)
        _,assignment=self.command("assignment","create",{"task_id":task["data"]["task_id"],"worker_profile":"routine"},project=project)
        token=self.assignment_token(assignment);(Path(assignment["data"]["worktree_path"])/"README").write_text("checked\n")
        code,missing=self.command("worker","submit",{"summary":"done","evidence":[{"label":"tests","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
        self.assertEqual(code,3);self.assertEqual(missing["error"]["code"],"REQUIRED_CHECK_EVIDENCE_MISSING")
        code,claim_only=self.command("worker","submit",{"summary":"done","evidence":[{"label":"tests","details":"reported","command":required,"exit_code":0,"artifact_path":None}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
        self.assertEqual(code,3);self.assertEqual(claim_only["error"]["code"],"REQUIRED_CHECK_EVIDENCE_MISSING")
        _,receipt=self.command("worker","check",{"command":required,"timeout_seconds":30},token=token)
        self.assertEqual(receipt["data"]["status"],"SUCCEEDED")
        code,submitted=self.command("worker","submit",{"summary":"done","evidence":[{"label":"tests","details":"observed","receipt_id":receipt["data"]["receipt_id"]}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
        self.assertEqual(code,0)
        _,review=self.command("submission","review-context",{"submission_id":submitted["data"]["submission_id"]},project=project)
        self.assertEqual(review["data"]["required_checks"],[required])
        self.assertEqual(review["data"]["execution_receipts"][0]["execution_role"],"WORKER")
        criterion=self.db_scalar("SELECT id FROM task_acceptance_criteria WHERE task_id=?",(task["data"]["task_id"],))
        verification={"submission_id":submitted["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"accepted"}
        code,missing_review=self.command("submission","verify",verification,project=project)
        self.assertEqual(code,3);self.assertEqual(missing_review["error"]["code"],"INDEPENDENT_CHECK_EVIDENCE_MISSING")
        _,reviewer_receipt=self.command("submission","check",{"submission_id":submitted["data"]["submission_id"],"command":required,"timeout_seconds":30},project=project)
        self.assertEqual(reviewer_receipt["data"]["role"],"REVIEWER")
        self.assertEqual(self.command("submission","verify",verification,project=project)[0],0)

    def test_correction_packets_are_assignment_scoped(self):
        project=self.init();_,first_task=self.setup_task(project)
        spec_id=self.db_scalar("SELECT id FROM specifications")
        _,requirement=self.command("requirement","create",{"statement":"Second","details":"Second visibly","implementation_required":True,"sources":[{"specification_id":spec_id,"locator":"2"}]},project=project)
        _,second_task=self.command("task","create",{"objective":"Second","implementation_scope":"code.txt","acceptance_criteria":["Second done"],"requirement_ids":[requirement["data"]["requirement_id"]],"dependency_task_ids":[]},project=project)
        self.command("plan","validate",{},project=project)
        _,first_assignment=self.command("assignment","create",{"task_id":first_task,"worker_profile":"complex"},project=project)
        _,second_assignment=self.command("assignment","create",{"task_id":second_task["data"]["task_id"],"worker_profile":"complex"},project=project)
        first_token=self.assignment_token(first_assignment);second_token=self.assignment_token(second_assignment)
        (Path(first_assignment["data"]["worktree_path"])/"README").write_text("first\n")
        _,submission=self.command("worker","submit",{"summary":"first","evidence":[{"label":"test","details":"claim"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=first_token)
        criterion=self.db_scalar("SELECT id FROM task_acceptance_criteria WHERE task_id=?",(first_task,))
        self.command("submission","verify",{"submission_id":submission["data"]["submission_id"],"outcome":"REJECTED","criterion_results":[{"criterion_id":criterion,"satisfied":False,"evidence":"failed"}],"behavior_matches_intent":False,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":"Private correction","notes":"rejected"},project=project)
        _,first_context=self.command("worker","context",{},token=first_token)
        _,second_context=self.command("worker","context",{},token=second_token)
        self.assertEqual(first_context["data"]["dynamic"]["correction_packet"]["corrections"],"Private correction")
        self.assertIsNone(second_context["data"]["dynamic"]["correction_packet"])

    def test_second_routine_rejection_requires_a_complex_assignment(self):
        project=self.init();_,task_id=self.setup_task(project);self.command("plan","validate",{},project=project)
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"routine"},project=project)
        token=self.assignment_token(assignment);worktree=Path(assignment["data"]["worktree_path"])
        criterion=self.db_scalar("select id from task_acceptance_criteria where task_id=?",(task_id,))
        for number in (1,2):
            (worktree/"README").write_text(f"attempt {number}\n")
            _,submission=self.command("worker","submit",{"summary":f"attempt {number}","evidence":[{"label":"test","details":"failed review"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
            code,rejected=self.command("submission","verify",{"submission_id":submission["data"]["submission_id"],"outcome":"REJECTED","criterion_results":[{"criterion_id":criterion,"satisfied":False,"evidence":"not sufficient"}],"behavior_matches_intent":False,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":"Correct it.","notes":"rejected"},project=project)
            self.assertEqual(code,0)
        self.assertTrue(rejected["data"]["escalation_required"]);self.assertEqual(rejected["data"]["required_worker_profile"],"complex")
        code,blocked=self.command("assignment","create",{"task_id":task_id,"worker_profile":"routine"},project=project)
        self.assertEqual(code,3);self.assertEqual(blocked["error"]["code"],"WORKER_PROFILE_ESCALATION_REQUIRED")
        code,complex_assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"complex"},project=project)
        self.assertEqual(code,0);self.assertEqual(complex_assignment["data"]["worker_profile"],"complex")

    def test_plan_apply_batches_new_requirements_tasks_and_local_dependencies(self):
        project=self.init();_,spec=self.command("spec","register",{"relative_path":"spec.md"},project=project);sid=spec["data"]["specification_id"]
        code,applied=self.command("plan","apply",{"requirements":[{"ref":"foundation","statement":"Foundation exists","details":"Foundation is observable","implementation_required":True,"sources":[{"specification_id":sid,"locator":"1"}]},{"ref":"consumer","statement":"Consumer exists","details":"Consumer is observable","implementation_required":True,"sources":[{"specification_id":sid,"locator":"1"}]}],"tasks":[{"ref":"foundation-task","objective":"Build foundation","implementation_scope":"code.txt","acceptance_criteria":["Foundation built"],"required_checks":["python3 -m unittest"],"requirement_refs":["foundation"],"dependency_refs":[]},{"ref":"consumer-task","objective":"Build consumer","implementation_scope":"README","acceptance_criteria":["Consumer built"],"requirement_refs":["consumer"],"dependency_refs":["foundation-task"]}]},project=project)
        self.assertEqual(code,0);self.assertEqual(applied["data"]["requirements_created"],2);self.assertEqual(applied["data"]["tasks_created"],2)
        code,validated=self.command("plan","validate",{},project=project)
        self.assertEqual(code,0);self.assertTrue(validated["data"]["valid"])

    def test_plan_apply_rejects_invalid_refs_without_partial_writes(self):
        project=self.init();_,spec=self.command("spec","register",{"relative_path":"spec.md"},project=project);sid=spec["data"]["specification_id"]
        code,rejected=self.command("plan","apply",{"requirements":[{"ref":"created","statement":"Would exist","details":"Must roll back","implementation_required":True,"sources":[{"specification_id":sid,"locator":"1"}]}],"tasks":[{"ref":"broken","objective":"Broken","implementation_scope":"README","acceptance_criteria":["Never created"],"requirement_refs":["missing"],"dependency_refs":[]}]},project=project)
        self.assertEqual(code,2);self.assertEqual(rejected["error"]["code"],"INVALID_REQUEST")
        self.assertEqual(self.db_scalar("select count(*) from requirements"),0)
        self.assertEqual(self.db_scalar("select count(*) from tasks"),0)

    def test_requirement_supersession_preserves_completed_historical_tasks(self):
        project=self.init();rid,task_id=self.setup_task(project);self.command("plan","validate",{},project=project)
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"complex"},project=project);token=self.assignment_token(assignment);(Path(assignment["data"]["worktree_path"])/"README").write_text("complete\n")
        _,submission=self.command("worker","submit",{"summary":"done","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
        criterion=self.db_scalar("select id from task_acceptance_criteria where task_id=?",(task_id,));sid=self.db_scalar("select id from specifications")
        self.command("submission","verify",{"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"accepted"},project=project)
        self.command("requirement","verify",{"requirement_id":rid,"evidence":[{"label":"test","details":"passed"}],"notes":"complete"},project=project);self.command("project","complete",{},project=project)
        code,superseded=self.command("requirement","supersede",{"requirement_id":rid,"reason":"New behavior replaces old behavior","replacement":{"statement":"Replacement behavior","details":"Replacement is observable","implementation_required":True,"sources":[{"specification_id":sid,"locator":"1"}]}},project=project)
        self.assertEqual(code,0);replacement=superseded["data"]["replacement_requirement_id"]
        _,new_task=self.command("task","create",{"objective":"Implement replacement","implementation_scope":"code.txt","acceptance_criteria":["Replacement implemented"],"requirement_ids":[replacement],"dependency_task_ids":[]},project=project)
        code,validated=self.command("plan","validate",{},project=project)
        self.assertEqual(code,0);self.assertTrue(validated["data"]["valid"]);self.assertEqual(self.db_scalar("select state from tasks where id=?",(task_id,)),"COMPLETED")

    def test_recovery_snapshot_and_external_rewrite_remove_credit(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"complex"}, project=project)
        code, recovery=self.command("project", "recover", {}, project=project)
        self.assertEqual(code, 0)
        self.assertEqual(recovery["data"]["tasks"]["assignments"][0]["worktree_path"], assignment["data"]["worktree_path"])
        self.assertEqual(recovery["data"]["tasks"]["assignments"][0]["worker_profile"],"complex")
        worker_token=self.assignment_token(assignment); worktree=Path(assignment["data"]["worktree_path"])
        (worktree / "README").write_text("integrate\n")
        _, submission=self.command("worker", "submit", {"summary":"done","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        criterion=self.db_scalar("select id from task_acceptance_criteria")
        self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"verified"}, project=project)
        self.assertEqual(self.db_scalar("select state from tasks where id=?",(task_id,)), "COMPLETED")
        # Simulate an external canonical rewrite; Taskledger must only remove ledger credit.
        self.git("reset", "--hard", "HEAD~1")
        self.command("project", "show", {}, project=project)
        self.assertEqual(self.db_scalar("select state from tasks where id=?",(task_id,)), "ACCEPTED")
        self.assertEqual(self.db_scalar("select state from integration_attempts"), "INVALIDATED")

    def test_pending_review_retargets_latest_observation(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        (self.root / "spec.md").write_text("B\n")
        _, first=self.command("spec", "check", {}, project=project)
        review_id=first["data"]["pending_reviews"][0]["id"]
        first_target=first["data"]["pending_reviews"][0]["to_revision_id"]
        (self.root / "spec.md").write_text("C\n")
        _, second=self.command("spec", "check", {}, project=project)
        self.assertEqual(len(second["data"]["pending_reviews"]), 1)
        self.assertEqual(second["data"]["pending_reviews"][0]["id"], review_id)
        self.assertNotEqual(second["data"]["pending_reviews"][0]["to_revision_id"], first_target)

    def test_worker_blocker_scope_and_question_resolution(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"routine"}, project=project)
        token=self.assignment_token(assignment)
        code, denied=self.command("worker", "blocker", {"category":"EXTERNAL_DEPENDENCY","scope_type":"PROJECT","description":"no"}, token=token)
        self.assertEqual(code, 4); self.assertEqual(denied["error"]["code"], "WORKER_SCOPE_VIOLATION")
        _, question=self.command("worker", "question", {"body":"Need decision", "blocking":True}, token=token)
        code, answer=self.command("question", "answer", {"question_id":question["data"]["question_id"],"answer":"Use A","resolve_blocker":True,"resolution":"Decision supplied"}, project=project)
        self.assertEqual(code, 0); self.assertEqual(answer["data"]["state"], "ANSWERED")

    def test_cli_flag_errors_are_json_only(self):
        env={**os.environ, "PYTHONPATH":str(ROOT / "src")}
        env.pop("TASKLEDGER_HOME", None)
        result=subprocess.run([sys.executable,"-m","taskledger","project","show","--token"], text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_REQUEST")

    def test_merge_conflict_is_safely_aborted_and_task_stays_accepted(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id,"worker_profile":"complex"}, project=project)
        worktree=Path(assignment["data"]["worktree_path"]); worker_token=self.assignment_token(assignment)
        (worktree / "code.txt").write_text("worker\n")
        # This external canonical commit is deliberate test setup; Taskledger must
        # never create it or resolve the subsequent conflict itself.
        (self.root / "code.txt").write_text("canonical\n")
        self.git("add", "code.txt"); self.git("commit", "-qm", "canonical edit")
        _, submission=self.command("worker", "submit", {"summary":"conflicts","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        criterion=self.db_scalar("select id from task_acceptance_criteria")
        code, result=self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"accepted"}, project=project)
        self.assertEqual(code, 0)
        self.assertTrue(result["data"]["accepted"]); self.assertFalse(result["data"]["integrated"])
        self.assertEqual(result["data"]["task_state"], "ACCEPTED")
        self.assertEqual(self.db_scalar("select state from integration_attempts"), "FAILED")
        self.assertEqual(self.git("status", "--porcelain") or b"", b"")

    def test_lightweight_assignment_uses_durable_gated_checkpoints(self):
        project=self.init();rid,task_id=self.setup_task(project);self.command("plan","validate",{},project=project)
        checkpoints=[{"label":"Vertical slice one","criteria":["First behavior works"]},{"label":"Vertical slice two","criteria":["Second behavior works"]}]
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"complex","execution_mode":"lightweight","checkpoints":checkpoints},project=project)
        token=self.assignment_token(assignment);worktree=Path(assignment["data"]["worktree_path"])
        (worktree/"README").write_text("slice one\n")
        _,first=self.command("worker","checkpoint",{"summary":"first slice","evidence":[{"label":"focused","details":"checked"}]},token=token)
        self.assertEqual(first["data"]["state"],"PENDING")
        code,pending=self.command("worker","checkpoint",{"summary":"cannot advance","evidence":[{"label":"focused","details":"unchecked"}]},token=token)
        self.assertEqual(code,3);self.assertEqual(pending["error"]["code"],"CHECKPOINT_ALREADY_PENDING")
        code,blocked=self.command("worker","submit",{"summary":"too early","evidence":[{"label":"test","details":"checked"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
        self.assertEqual(code,3);self.assertEqual(blocked["error"]["code"],"CHECKPOINT_APPROVAL_REQUIRED")
        _,review=self.command("checkpoint","review-context",{"checkpoint_id":first["data"]["checkpoint_id"]},project=project)
        self.assertTrue(review["data"]["review_required"]["approval_is_intermediate_only"])
        approved={"checkpoint_id":first["data"]["checkpoint_id"],"outcome":"APPROVED","criterion_results":[{"position":1,"satisfied":True,"evidence":"exact commit inspected"}],"corrections":None,"notes":"approved"}
        self.assertEqual(self.command("checkpoint","verify",approved,project=project)[0],0)
        _,rotated=self.command("assignment","rotate-token",{"assignment_id":assignment["data"]["assignment_id"]},project=project)
        replacement_token=Path(rotated["data"]["worker_token_path"]).read_text().strip()
        self.assertEqual(self.command("worker","context",{},token=token)[0],4)
        _,resumed=self.command("worker","context",{},token=replacement_token)
        self.assertEqual(resumed["data"]["dynamic"]["checkpoint_progress"]["approved_count"],1)
        (worktree/"README").write_text("slice one\nslice two incomplete\n")
        _,second=self.command("worker","checkpoint",{"summary":"second slice","evidence":[{"label":"focused","details":"checked"}]},token=replacement_token)
        rejected={"checkpoint_id":second["data"]["checkpoint_id"],"outcome":"REJECTED","criterion_results":[{"position":1,"satisfied":False,"evidence":"missing edge"}],"corrections":"Add the missing edge case.","notes":"repair"}
        self.command("checkpoint","verify",rejected,project=project)
        _,repair_context=self.command("worker","context",{},token=replacement_token)
        history=repair_context["data"]["dynamic"]["checkpoint_progress"]["history"]
        self.assertEqual(history[-1]["corrections"],"Add the missing edge case.")
        (worktree/"README").write_text("slice one\nslice two complete\n")
        _,repair=self.command("worker","checkpoint",{"summary":"second slice repaired","evidence":[{"label":"focused","details":"checked"}]},token=replacement_token)
        self.assertEqual(self.db_scalar("SELECT state FROM assignment_checkpoints WHERE id=?",(second["data"]["checkpoint_id"],)),"SUPERSEDED")
        self.command("checkpoint","verify",{"checkpoint_id":repair["data"]["checkpoint_id"],"outcome":"APPROVED","criterion_results":[{"position":1,"satisfied":True,"evidence":"exact commit inspected"}],"corrections":None,"notes":"approved"},project=project)
        _,artifact=self.command("worker","artifact-register",{"path":"README"},token=replacement_token)
        retained=Path(artifact["data"]["stored_path"]);self.assertTrue(retained.is_file())
        _,submission=self.command("worker","submit",{"summary":"complete","evidence":[{"label":"checkpoints","details":"both approved","artifact_id":artifact["data"]["artifact_id"]}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=replacement_token)
        criterion=self.db_scalar("SELECT id FROM task_acceptance_criteria WHERE task_id=?",(task_id,))
        self.command("submission","verify",{"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"accepted"},project=project)
        _,export_one=self.command("evidence","export",{"task_id":task_id},project=project)
        _,export_two=self.command("evidence","export",{"task_id":task_id},project=project)
        self.assertEqual(export_one["data"]["sha256"],export_two["data"]["sha256"])
        exported=json.loads(Path(export_one["data"]["path"]).read_text())
        self.assertTrue(any(item["provenance"]=="CHECKPOINT_REVIEWER_CONCLUSION" for item in exported["reviewer_conclusions"]))
        self.command("requirement","verify",{"requirement_id":rid,"evidence":[{"label":"review","details":"complete"}],"notes":"complete"},project=project)
        self.command("project","complete",{},project=project)
        self.assertTrue(retained.is_file())

    def test_preflight_and_event_wait_expose_host_boundary_and_cursor(self):
        project=self.init();_,task_id=self.setup_task(project);self.command("plan","validate",{},project=project)
        profile=self.root/".codex"/"agents"/"taskledger-worker-routine.toml";profile.parent.mkdir(parents=True);profile.write_text('name = "taskledger_worker_routine"\nmodel = "configured-model"\nmodel_reasoning_effort = "high"\n')
        _,preflight=self.command("project","preflight",{"profiles":["routine"],"local_inputs":[{"name":"spec","path":str(self.root/"spec.md"),"required":True}],"services":[],"host_agent_availability":{"routine":True}},project=project)
        self.assertTrue(preflight["data"]["ready_for_local_preparation"])
        self.assertEqual(preflight["data"]["profiles"][0]["runtime_capability"],"HOST_REPORTED_UNVERIFIED")
        self.assertTrue(preflight["data"]["host_contract"]["configuration_is_not_runtime_proof"])
        _,baseline=self.command("project","resume",{},project=project);cursor=baseline["data"]["latest_event_sequence"]
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"routine"},project=project)
        token=self.assignment_token(assignment)
        _,question=self.command("worker","question",{"body":"Need a decision","blocking":True},token=token)
        _,events=self.command("project","wait",{"cursor":cursor,"event_types":["WORKER_QUESTION_CREATED"],"timeout_ms":0},project=project)
        self.assertEqual(events["data"]["events"][0]["entity_id"],question["data"]["question_id"])
        _,timeout=self.command("project","wait",{"cursor":events["data"]["event_cursor"],"event_types":["WORKER_QUESTION_CREATED"],"timeout_ms":10},project=project)
        self.assertTrue(timeout["data"]["timed_out"])
        self.assertEqual(timeout["data"]["cursor_gap_status"],"NO_PRUNING_CONFIGURED")
        self.assertFalse(timeout["data"]["missed_events"])
        code,future=self.command("project","wait",{"cursor":timeout["data"]["latest_event_sequence"]+1,"event_types":["WORKER_QUESTION_CREATED"],"timeout_ms":0},project=project)
        self.assertEqual(code,3);self.assertEqual(future["error"]["code"],"EVENT_CURSOR_INVALID")

    def test_task_revision_supersedes_checkpoint_approval(self):
        project=self.init();rid,task_id=self.setup_task(project);self.command("plan","validate",{},project=project)
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"complex","execution_mode":"lightweight","checkpoints":[{"label":"slice","criteria":["slice works"]}]},project=project)
        token=self.assignment_token(assignment);worktree=Path(assignment["data"]["worktree_path"]);(worktree/"README").write_text("slice\n")
        _,checkpoint=self.command("worker","checkpoint",{"summary":"slice","evidence":[{"label":"test","details":"checked"}]},token=token)
        self.command("checkpoint","verify",{"checkpoint_id":checkpoint["data"]["checkpoint_id"],"outcome":"APPROVED","criterion_results":[{"position":1,"satisfied":True,"evidence":"checked"}],"corrections":None,"notes":"approved"},project=project)
        self.command("task","update",{"task_id":task_id,"objective":"Changed objective","implementation_scope":"README","acceptance_criteria":["Changed behavior"],"required_checks":[],"requirement_ids":[rid],"dependency_task_ids":[],"assignment_action":"CONTINUE"},project=project)
        self.command("plan","validate",{},project=project)
        _,context=self.command("worker","context",{},token=token)
        self.assertEqual(context["data"]["context"]["task"]["revision"],2)
        progress=context["data"]["dynamic"]["checkpoint_progress"]
        self.assertEqual(progress["approved_count"],0);self.assertFalse(progress["final_submission_unlocked"])
        self.assertEqual(self.db_scalar("SELECT state FROM assignment_checkpoints WHERE id=?",(checkpoint["data"]["checkpoint_id"],)),"SUPERSEDED")

    def test_source_changing_and_timed_out_checks_are_retained_but_not_valid(self):
        changing="python3 -c \"open('generated.txt','w').write('x')\"";timing_out="python3 -c \"import time; time.sleep(2)\""
        project=self.init();_,spec=self.command("spec","register",{"relative_path":"spec.md"},project=project)
        _,req=self.command("requirement","create",{"statement":"Check behavior","details":"Observable","implementation_required":True,"sources":[{"specification_id":spec["data"]["specification_id"],"locator":"1"}]},project=project)
        _,task=self.command("task","create",{"objective":"Check behavior","implementation_scope":"README","acceptance_criteria":["done"],"required_checks":[changing,timing_out],"requirement_ids":[req["data"]["requirement_id"]],"dependency_task_ids":[]},project=project)
        self.command("plan","validate",{},project=project);_,assignment=self.command("assignment","create",{"task_id":task["data"]["task_id"],"worker_profile":"complex"},project=project)
        token=self.assignment_token(assignment);(Path(assignment["data"]["worktree_path"])/"README").write_text("done\n")
        _,changed=self.command("worker","check",{"command":changing,"timeout_seconds":1},token=token)
        self.assertEqual(changed["data"]["status"],"SUCCEEDED");self.assertTrue(changed["data"]["source_changed_during_execution"])
        _,timed_out=self.command("worker","check",{"command":timing_out,"timeout_seconds":1},token=token)
        self.assertEqual(timed_out["data"]["status"],"TIMED_OUT")
        code,blocked=self.command("worker","submit",{"summary":"done","evidence":[{"label":"changed","details":"observed","receipt_id":changed["data"]["receipt_id"]},{"label":"timeout","details":"observed","receipt_id":timed_out["data"]["receipt_id"]}],"risks":[],"unresolved_questions":[],"follow_up_work":[]},token=token)
        self.assertEqual(code,3);self.assertEqual(blocked["error"]["code"],"REQUIRED_CHECK_EVIDENCE_MISSING")

    def test_controller_real_adapter_runs_fake_runtime_through_integration(self):
        import asyncio

        from taskledger.controller.fakes import FakeRuntime, TurnScript, accepted_verdict
        from taskledger.controller.journal import Journal
        from taskledger.controller.supervisor import Supervisor, SupervisorConfig
        from taskledger.controller.taskledger_adapter import TaskledgerLedgerAdapter
        from taskledger.db import connect
        from taskledger.service import Service

        project_id=self.init();_,task_id=self.setup_task(project_id);self.command("plan","validate",{},project=project_id)
        _,created=self.command("assignment","create",{"task_id":task_id,"worker_profile":"routine"},project=project_id)
        worker_token=self.assignment_token(created);assignment_id=created["data"]["assignment_id"]
        service=Service(connect(self.home),self.home);project,orchestrator=service.auth_orchestrator(project_id,None)
        worker=service.authenticate(None,worker_token,"WORKER");worktree=Path(created["data"]["worktree_path"])
        adapter=TaskledgerLedgerAdapter(service,project,orchestrator)
        before=adapter.progress_fingerprint(assignment_id)

        def implement_and_submit():
            (worktree/"README").write_text("implemented\n")
            subprocess.run(["git","-C",str(worktree),"add","README"],check=True,capture_output=True)
            subprocess.run(["git","-C",str(worktree),"-c","user.name=Controller Test","-c","user.email=controller@example.invalid","commit","-qm","implement"],check=True,capture_output=True)
            service.worker_submit(worker,{"summary":"implemented","evidence":[{"label":"fake runtime","details":"implemented in the assignment worktree"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]})

        criterion=service.con.execute("SELECT id FROM task_acceptance_criteria WHERE task_id=?",(task_id,)).fetchone()[0]
        runtime=FakeRuntime(worker_turns=[TurnScript(effect=implement_and_submit)],reviewer_turns=[TurnScript(structured_output=accepted_verdict((criterion,)))])
        config=SupervisorConfig();journal=Journal(service.con,self.home);run_id=journal.create_run(project_id,mode="ASSIGNMENT",config=config.__dict__)
        result=asyncio.run(Supervisor(project_id=project_id,run_id=run_id,ledger=adapter,runtime=runtime,journal=journal,config=config).run_assignment(assignment_id))
        self.assertEqual(result.status.value,"INTEGRATED")
        self.assertNotEqual(before,adapter.progress_fingerprint(assignment_id))
        self.assertEqual(service.con.execute("SELECT state FROM tasks WHERE id=?",(task_id,)).fetchone()[0],"COMPLETED")
        service.con.close()

    def test_controller_reviews_lightweight_checkpoint_before_continuing_worker(self):
        import asyncio

        from taskledger.controller.fakes import FakeRuntime, TurnScript, accepted_verdict
        from taskledger.controller.journal import Journal
        from taskledger.controller.supervisor import Supervisor, SupervisorConfig
        from taskledger.controller.taskledger_adapter import TaskledgerLedgerAdapter
        from taskledger.db import connect
        from taskledger.service import Service

        project_id=self.init();_,task_id=self.setup_task(project_id);self.command("plan","validate",{},project=project_id)
        _,created=self.command("assignment","create",{
            "task_id":task_id,"worker_profile":"routine","execution_mode":"lightweight",
            "checkpoints":[{"label":"slice","criteria":["slice exists"]}],
        },project=project_id)
        token=self.assignment_token(created);assignment_id=created["data"]["assignment_id"]
        service=Service(connect(self.home),self.home);project,orchestrator=service.auth_orchestrator(project_id,None)
        worker=service.authenticate(None,token,"WORKER");worktree=Path(created["data"]["worktree_path"])

        def checkpoint():
            (worktree/"slice.txt").write_text("slice\n")
            service.worker_checkpoint(worker,{"summary":"slice","evidence":[{"label":"file","details":"slice exists"}]})

        def submit():
            (worktree/"README").write_text("complete\n")
            service.worker_submit(worker,{"summary":"complete","evidence":[{"label":"file","details":"complete"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]})

        def checkpoint_verdict():
            return accepted_verdict(("checkpoint:1",))

        def submission_verdict():
            criterion=service.con.execute("SELECT id FROM task_acceptance_criteria WHERE task_id=?",(task_id,)).fetchone()[0]
            return accepted_verdict((criterion,))

        runtime=FakeRuntime(
            worker_turns=[TurnScript(effect=checkpoint),TurnScript(effect=submit)],
            reviewer_turns=[TurnScript(structured_output=checkpoint_verdict),TurnScript(structured_output=submission_verdict)],
        )
        config=SupervisorConfig();journal=Journal(service.con,self.home);run_id=journal.create_run(project_id,mode="ASSIGNMENT",config=config.__dict__)
        result=asyncio.run(Supervisor(
            project_id=project_id,run_id=run_id,ledger=TaskledgerLedgerAdapter(service,project,orchestrator),
            runtime=runtime,journal=journal,config=config,
        ).run_assignment(assignment_id))
        self.assertEqual(result.status.value,"INTEGRATED")
        self.assertEqual(result.worker_turns,2)
        self.assertEqual(result.reviewer_turns,2)
        self.assertEqual(service.con.execute("SELECT state FROM assignment_checkpoints WHERE assignment_id=?",(assignment_id,)).fetchone()[0],"APPROVED")
        service.con.close()

    def test_controller_cli_requires_live_opt_in_and_tracks_budget_grants(self):
        from taskledger.controller.journal import Journal
        from taskledger.db import connect
        from taskledger.service import Service

        project_id=self.init();_,task_id=self.setup_task(project_id);self.command("plan","validate",{},project=project_id)
        _,assignment=self.command("assignment","create",{"task_id":task_id,"worker_profile":"routine"},project=project_id)
        code,refused=self.command("controller","run-assignment",{"assignment_id":assignment["data"]["assignment_id"],"live":False},project=project_id)
        self.assertEqual(code,2);self.assertEqual(refused["error"]["code"],"INVALID_REQUEST")

        service=Service(connect(self.home),self.home);project,principal=service.auth_orchestrator(project_id,None)
        journal=Journal(service.con,self.home);run_id=journal.create_run(project_id,mode="ASSIGNMENT",config={"assignment_id":assignment["data"]["assignment_id"],"limits":{}})
        journal.finish_run(run_id,"PAUSED",reason="MAX_TURNS",detail="test pause");service.con.close()
        code,shown=self.command("controller","show",{"run_id":run_id},project=project_id)
        self.assertEqual(code,0);self.assertEqual(shown["data"]["run"]["pause_reason"],"MAX_TURNS")
        code,extended=self.command("controller","extend-budget",{"run_id":run_id,"kind":"WORKER_TURNS","amount":2,"reason":"approved continuation"},project=project_id)
        self.assertEqual(code,0);self.assertEqual(extended["data"]["amount"],2)
        self.assertEqual(self.db_scalar("SELECT SUM(amount) FROM controller_budget_grants WHERE run_id=?",(run_id,)),2)


if __name__ == "__main__": unittest.main()
