from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class TaskledgerAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.home = Path(self.tmp.name) / "home"
        self.root.mkdir()
        self.git("init", "-q", "-b", "feat/ledger")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / "spec.md").write_text("Specification\n")
        (self.root / "code.txt").write_text("base\n")
        self.git("add", "spec.md", "code.txt")
        self.git("commit", "-qm", "initial")

    def tearDown(self): self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def command(self, resource, action, data=None, *, token=None, project=None, repo=None, confirm=None):
        args=[sys.executable, "-m", "taskledger", resource, action]
        args += ["--input", "-"]
        if project: args += ["--project", project]
        if token: args += ["--token", token]
        if repo: args += ["--repo", repo]
        if confirm: args += ["--confirm-branch", confirm]
        env={**os.environ, "PYTHONPATH":str(ROOT / "src"), "TASKLEDGER_HOME":str(self.home)}
        result=subprocess.run(args, input=json.dumps(data or {}), text=True, capture_output=True, env=env, cwd=self.root)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def init(self):
        code, first=self.command("project", "init", repo=str(self.root))
        self.assertEqual(code, 0); self.assertTrue(first["data"]["confirmation_required"])
        code, second=self.command("project", "init", repo=str(self.root), confirm="feat/ledger")
        self.assertEqual(code, 0)
        return second["data"]["project_id"]

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

    def test_full_task_backed_flow_and_worker_scope(self):
        project=self.init()
        rid, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id}, project=project)
        worker_token=assignment["data"]["worker_token"]
        # A worker credential cannot reach an orchestrator operation.
        code, denied=self.command("plan", "validate", {}, project=project, token=worker_token)
        self.assertEqual(code, 4); self.assertEqual(denied["error"]["code"], "AUTHORIZATION_DENIED")
        worktree=Path(assignment["data"]["worktree_path"])
        (worktree / "README").write_text("done\n")
        _, submission=self.command("worker", "submit", {"summary":"Done", "evidence":[{"label":"test","details":"passed"}], "risks":[], "unresolved_questions":[], "follow_up_work":[]}, token=worker_token)
        import sqlite3
        con=sqlite3.connect(self.home / "taskledger.sqlite3")
        criterion_id=con.execute("select id from task_acceptance_criteria").fetchone()[0]
        self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion_id,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"verified"}, project=project)
        self.command("requirement", "verify", {"requirement_id":rid,"evidence":[{"label":"manual","details":"works"}],"notes":"complete"}, project=project)
        code, completed=self.command("project", "complete", {}, project=project)
        self.assertEqual(code, 0); self.assertTrue(completed["data"]["completed"])

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
        import sqlite3
        con=sqlite3.connect(self.home / "taskledger.sqlite3")
        spec_id=con.execute("select id from specifications").fetchone()[0]
        _, req_b=self.command("requirement", "create", {"statement":"B", "details":"B visibly", "implementation_required":True, "sources":[{"specification_id":spec_id,"locator":"1"}]}, project=project)
        _, task_b=self.command("task", "create", {"objective":"B", "implementation_scope":"README", "acceptance_criteria":["B done"], "requirement_ids":[req_b["data"]["requirement_id"]], "dependency_task_ids":[task_a]}, project=project)
        self.command("plan", "validate", {}, project=project)
        code, output=self.command("assignment", "create", {"task_id":task_b["data"]["task_id"]}, project=project)
        self.assertEqual(code, 3); self.assertEqual(output["error"]["code"], "DEPENDENCIES_UNSATISFIED")
        _, assignment=self.command("assignment", "create", {"task_id":task_a}, project=project)
        (Path(assignment["data"]["worktree_path"]) / "README").write_text("A complete\n")
        _, submission=self.command("worker", "submit", {"summary":"A","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=assignment["data"]["worker_token"])
        criteria=con.execute("select id from task_acceptance_criteria where task_id=?",(task_a,)).fetchall()
        self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criteria[0][0],"satisfied":True,"evidence":"ok"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"A accepted"}, project=project)
        code, eligible=self.command("assignment", "create", {"task_id":task_b["data"]["task_id"]}, project=project)
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
        _, assignment=self.command("assignment", "create", {"task_id":task_id}, project=project)
        worker_token=assignment["data"]["worker_token"]; worktree=Path(assignment["data"]["worktree_path"])
        (worktree / "README").write_text("first\n")
        _, first=self.command("worker", "submit", {"summary":"first", "evidence":[{"label":"test","details":"first"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        import sqlite3
        con=sqlite3.connect(self.home / "taskledger.sqlite3");criterion=con.execute("select id from task_acceptance_criteria").fetchone()[0]
        code, rejected=self.command("submission", "verify", {"submission_id":first["data"]["submission_id"],"outcome":"REJECTED","criterion_results":[{"criterion_id":criterion,"satisfied":False,"evidence":"not sufficient"}],"behavior_matches_intent":False,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":"Implement the missing behavior.","notes":"rejected"}, project=project)
        self.assertEqual(code, 0); self.assertEqual(rejected["data"]["outcome"], "REJECTED")
        (worktree / "README").write_text("corrected\n")
        code, corrected=self.command("worker", "submit", {"summary":"corrected", "evidence":[{"label":"test","details":"corrected"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        self.assertEqual(code, 0); self.assertEqual(corrected["data"]["state"], "PENDING")
        self.assertEqual(con.execute("select count(*) from submissions").fetchone()[0], 2)

    def test_recovery_snapshot_and_external_rewrite_remove_credit(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id}, project=project)
        code, recovery=self.command("project", "recover", {}, project=project)
        self.assertEqual(code, 0)
        self.assertEqual(recovery["data"]["tasks"]["assignments"][0]["worktree_path"], assignment["data"]["worktree_path"])
        worker_token=assignment["data"]["worker_token"]; worktree=Path(assignment["data"]["worktree_path"])
        (worktree / "README").write_text("integrate\n")
        _, submission=self.command("worker", "submit", {"summary":"done","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        import sqlite3
        con=sqlite3.connect(self.home / "taskledger.sqlite3"); criterion=con.execute("select id from task_acceptance_criteria").fetchone()[0]
        self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"verified"}, project=project)
        self.assertEqual(con.execute("select state from tasks where id=?",(task_id,)).fetchone()[0], "COMPLETED")
        # Simulate an external canonical rewrite; Taskledger must only remove ledger credit.
        self.git("reset", "--hard", "HEAD~1")
        self.command("project", "show", {}, project=project)
        self.assertEqual(con.execute("select state from tasks where id=?",(task_id,)).fetchone()[0], "ACCEPTED")
        self.assertEqual(con.execute("select state from integration_attempts").fetchone()[0], "INVALIDATED")

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
        _, assignment=self.command("assignment", "create", {"task_id":task_id}, project=project)
        token=assignment["data"]["worker_token"]
        code, denied=self.command("worker", "blocker", {"category":"EXTERNAL_DEPENDENCY","scope_type":"PROJECT","description":"no"}, token=token)
        self.assertEqual(code, 4); self.assertEqual(denied["error"]["code"], "WORKER_SCOPE_VIOLATION")
        _, question=self.command("worker", "question", {"body":"Need decision", "blocking":True}, token=token)
        code, answer=self.command("question", "answer", {"question_id":question["data"]["question_id"],"answer":"Use A","resolve_blocker":True,"resolution":"Decision supplied"}, project=project)
        self.assertEqual(code, 0); self.assertEqual(answer["data"]["state"], "ANSWERED")

    def test_cli_flag_errors_are_json_only(self):
        env={**os.environ, "PYTHONPATH":str(ROOT / "src"), "TASKLEDGER_HOME":str(self.home)}
        result=subprocess.run([sys.executable,"-m","taskledger","project","show","--token"], text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_REQUEST")

    def test_merge_conflict_is_safely_aborted_and_task_stays_accepted(self):
        project=self.init(); _, task_id=self.setup_task(project)
        self.command("plan", "validate", {}, project=project)
        _, assignment=self.command("assignment", "create", {"task_id":task_id}, project=project)
        worktree=Path(assignment["data"]["worktree_path"]); worker_token=assignment["data"]["worker_token"]
        (worktree / "code.txt").write_text("worker\n")
        # This external canonical commit is deliberate test setup; Taskledger must
        # never create it or resolve the subsequent conflict itself.
        (self.root / "code.txt").write_text("canonical\n")
        self.git("add", "code.txt"); self.git("commit", "-qm", "canonical edit")
        _, submission=self.command("worker", "submit", {"summary":"conflicts","evidence":[{"label":"test","details":"passed"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}, token=worker_token)
        import sqlite3
        con=sqlite3.connect(self.home / "taskledger.sqlite3"); criterion=con.execute("select id from task_acceptance_criteria").fetchone()[0]
        code, result=self.command("submission", "verify", {"submission_id":submission["data"]["submission_id"],"outcome":"ACCEPTED","criterion_results":[{"criterion_id":criterion,"satisfied":True,"evidence":"checked"}],"behavior_matches_intent":True,"required_evidence_present":True,"blocking_issues_remaining":False,"corrections":None,"notes":"accepted"}, project=project)
        self.assertEqual(code, 0)
        self.assertTrue(result["data"]["accepted"]); self.assertFalse(result["data"]["integrated"])
        self.assertEqual(result["data"]["task_state"], "ACCEPTED")
        self.assertEqual(con.execute("select state from integration_attempts").fetchone()[0], "FAILED")
        self.assertEqual(self.git("status", "--porcelain") or b"", b"")


if __name__ == "__main__": unittest.main()
