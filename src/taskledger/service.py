from __future__ import annotations

import hmac
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import git
from .core import (LedgerError, MAX_SPEC, array, atomic_secret, canonical, canonical_bytes,
                   new_id, now, require_object, sha256, text, token)
from .db import transaction


CATEGORIES = {"MISSING_PRODUCT_DECISION", "AMBIGUOUS_REQUIREMENT", "EXTERNAL_DEPENDENCY",
              "REPOSITORY_STATE", "VERIFICATION_FAILURE", "SPECIFICATION_STATE", "INTERRUPTED_OPERATION"}
WORKER_PROFILES = {"routine", "complex"}
ACTIONABLE_EVENTS = {
    "SUBMISSION_RECORDED", "SUBMISSION_VERIFIED", "CHECKPOINT_RECORDED",
    "CHECKPOINT_REVIEWED", "WORKER_QUESTION_CREATED", "BLOCKER_CREATED",
    "ASSIGNMENT_ESCALATION_REQUIRED", "CHECK_EXECUTED", "INTEGRATION_FAILED",
}
MAX_RECEIPT_OUTPUT = 10 * 1024 * 1024
MAX_REGISTERED_ARTIFACT = 50 * 1024 * 1024


class Service:
    def __init__(self, con, home: Path): self.con, self.home = con, home

    def audit(self, project: str, principal: str | None, event: str, entity: str, entity_id: str, payload: Any = None) -> None:
        self.con.execute("INSERT INTO audit_events(project_id,principal_id,event_type,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                         (project, principal, event, entity, entity_id, canonical(payload or {}), now()))

    def tree_fingerprint(self, repo: str) -> str:
        """Fingerprint HEAD plus exact staged, unstaged, and untracked content state."""
        head = git.oid_or_none(repo, "HEAD")
        status = git.run(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout
        staged=git.run(repo,["diff","--binary","--cached","HEAD"]).stdout
        unstaged=git.run(repo,["diff","--binary","HEAD"]).stdout
        untracked=[]
        for relative in git.run(repo,["ls-files","--others","--exclude-standard","-z"]).stdout.split("\0"):
            if relative:untracked.append({"path":relative,"object_hash":git.run(repo,["hash-object","--",relative]).stdout.strip()})
        return sha256(canonical({"head":head,"status":status,"staged":staged,"unstaged":unstaged,"untracked":untracked}))

    def artifact_root(self, project_id: str) -> Path:
        return self.home / "projects" / project_id / "artifacts"

    def _store_artifact(self, project, principal, source: Path, *, assignment_id: str | None,
                        provenance_kind: str, original_path: str | None = None,
                        source_revision: str | None = None) -> dict[str, Any]:
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            raise LedgerError("ARTIFACT_NOT_FOUND", "Artifact must be a present readable regular file.")
        if source.is_symlink() or not resolved.is_file():
            raise LedgerError("ARTIFACT_INVALID", "Artifact must resolve to a regular non-symlink file.")
        size = resolved.stat().st_size
        if size > MAX_REGISTERED_ARTIFACT:
            raise LedgerError("ARTIFACT_TOO_LARGE", "Artifact exceeds the 50 MiB retention limit.")
        raw = resolved.read_bytes()
        digest = sha256(raw)
        artifact_id = new_id()
        root = self.artifact_root(project["id"])
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = root / f"{artifact_id}-{digest[:16]}.bin"
        temporary = root / f".{artifact_id}.tmp"
        temporary.write_bytes(raw)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        with transaction(self.con):
            self.con.execute(
                "INSERT INTO evidence_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, project["id"], assignment_id, principal["id"], provenance_kind,
                 original_path, str(destination), digest, size, source_revision, now()),
            )
            self.audit(project["id"], principal["id"], "ARTIFACT_REGISTERED", "ARTIFACT", artifact_id,
                       {"provenance_kind": provenance_kind, "sha256": digest, "size_bytes": size})
        return {"artifact_id": artifact_id, "sha256": digest, "size_bytes": size,
                "stored_path": str(destination), "provenance_kind": provenance_kind}

    def register_artifact(self, project, principal, data, *, worker: bool = False):
        require_object(data,{"path"},{"path"})
        supplied=text(data["path"],"path")
        assignment=None
        if worker:
            _,assignment=self.worker_assignment(principal)
            allowed_root=Path(assignment["worktree_path"]).resolve()
        else:
            allowed_root=Path(project["repository_root"]).resolve()
        candidate=(allowed_root/supplied) if not Path(supplied).is_absolute() else Path(supplied)
        try:resolved=candidate.resolve(strict=True)
        except OSError:raise LedgerError("ARTIFACT_NOT_FOUND","Artifact must be a present readable regular file.")
        lexical=candidate.absolute();symlink_component=False
        current=lexical
        while current!=allowed_root and allowed_root in current.parents:
            symlink_component=symlink_component or current.is_symlink();current=current.parent
        if symlink_component or not resolved.is_relative_to(allowed_root):
            raise LedgerError("ARTIFACT_PATH_INVALID","Artifact path must remain inside the authorized repository worktree.")
        source_revision=git.oid_or_none(str(allowed_root),"HEAD")
        return self._store_artifact(project,principal,resolved,assignment_id=assignment["id"] if assignment else None,
                                    provenance_kind="WORKER_REGISTERED" if worker else "ORCHESTRATOR_REGISTERED",
                                    original_path=str(candidate.relative_to(allowed_root)),source_revision=source_revision)

    def _execute_check(self, project, principal, assignment, command: str, timeout_seconds: int,
                       *, role: str, submission_id: str | None = None):
        cwd=assignment["worktree_path"]
        source_oid=git.oid(cwd)
        before=self.tree_fingerprint(cwd)
        started=now();timed_out=False;interrupted=False
        process=subprocess.Popen(command,cwd=cwd,shell=True,text=False,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,start_new_session=True)
        try:
            output,_=process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(process.pid,signal.SIGTERM)
            try:output,_=process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);output,_=process.communicate()
        except KeyboardInterrupt:
            interrupted=True;os.killpg(process.pid,signal.SIGTERM);output,_=process.communicate()
        finally:
            ended=now()
        output=output or b""
        truncated=len(output)>MAX_RECEIPT_OUTPUT
        if truncated:output=output[:MAX_RECEIPT_OUTPUT]+b"\n[taskledger output truncated]\n"
        staging=self.artifact_root(project["id"])/"staging"
        staging.mkdir(mode=0o700,parents=True,exist_ok=True)
        receipt_id=new_id();output_path=staging/f"{receipt_id}.log";output_path.write_bytes(output);os.chmod(output_path,0o600)
        artifact=self._store_artifact(project,principal,output_path,assignment_id=assignment["id"],
                                      provenance_kind=f"{role}_EXECUTION_OUTPUT",source_revision=source_oid)
        output_path.unlink(missing_ok=True)
        after=self.tree_fingerprint(cwd);current_oid=git.oid(cwd)
        status="INTERRUPTED" if interrupted else ("TIMED_OUT" if timed_out else ("SUCCEEDED" if process.returncode==0 else "FAILED"))
        stale=before!=after or current_oid!=source_oid
        with transaction(self.con):
            self.con.execute("INSERT INTO execution_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (receipt_id,project["id"],assignment["id"],submission_id,principal["id"],role,command,cwd,source_oid,before,after,started,ended,status,process.returncode,artifact["artifact_id"],int(stale),canonical({"output_truncated":truncated,"timeout_seconds":timeout_seconds,"measured_execution":True})))
            self.audit(project["id"],principal["id"],"CHECK_EXECUTED","EXECUTION_RECEIPT",receipt_id,{"role":role,"status":status,"source_revision":source_oid,"stale":stale})
        return {"receipt_id":receipt_id,"role":role,"command":command,"source_revision":source_oid,
                "started_at":started,"ended_at":ended,"status":status,"exit_code":process.returncode,
                "source_changed_during_execution":stale,"output_artifact":artifact}

    def worker_check(self, principal, data):
        require_object(data,{"command","timeout_seconds"},{"command"})
        project,a=self.worker_assignment(principal);self.preflight(project)
        if a["state"]!="ACTIVE":raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Worker assignment is not active.")
        command=text(data["command"],"command")
        allowed=[x[0] for x in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=?",(a["task_id"],a["task_revision"]))]
        if command not in allowed:raise LedgerError("CHECK_NOT_DECLARED","Worker may execute only an exact required check for this assignment.")
        timeout=data.get("timeout_seconds",900)
        if not isinstance(timeout,int) or isinstance(timeout,bool) or not 1<=timeout<=3600:raise LedgerError("INVALID_REQUEST","timeout_seconds must be between 1 and 3600.")
        if not git.clean(a["worktree_path"]):
            task=self.con.execute("SELECT * FROM tasks WHERE id=?",(a["task_id"],)).fetchone()
            self._commit_worker_checkpoint(project,a,task,f"taskledger: evidence checkpoint {task['id']}")
        if git.oid(a["worktree_path"])==a["base_commit_oid"]:raise LedgerError("GIT_COMMAND_FAILED","A worker check requires a non-empty committed assignment change.")
        return self._execute_check(project,principal,a,command,timeout,role="WORKER")

    def reviewer_check(self, project, principal, data):
        require_object(data,{"submission_id","command","timeout_seconds"},{"submission_id","command"})
        sub=self.con.execute("SELECT s.*,a.task_id,a.task_revision,a.worktree_path,a.id assignment_id FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.id=? AND s.project_id=?",(data["submission_id"],project["id"])).fetchone()
        if not sub:raise LedgerError("SUBMISSION_NOT_FOUND","Submission was not found.")
        command=text(data["command"],"command")
        allowed=[x[0] for x in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=?",(sub["task_id"],sub["task_revision"]))]
        if command not in allowed:raise LedgerError("CHECK_NOT_DECLARED","Reviewer may execute only an exact required check for this submission.")
        timeout=data.get("timeout_seconds",900)
        if not isinstance(timeout,int) or isinstance(timeout,bool) or not 1<=timeout<=3600:raise LedgerError("INVALID_REQUEST","timeout_seconds must be between 1 and 3600.")
        if git.oid(sub["worktree_path"])!=sub["head_commit_oid"] or not git.clean(sub["worktree_path"]):
            raise LedgerError("SUBMISSION_CHECKOUT_STALE","Reviewer checks require the clean assignment worktree at the exact submitted commit.")
        assignment=self.con.execute("SELECT * FROM assignments WHERE id=?",(sub["assignment_id"],)).fetchone()
        return self._execute_check(project,principal,assignment,command,timeout,role="REVIEWER",submission_id=sub["id"])

    def project(self, project_id: str | None = None, cwd: str | None = None):
        if project_id:
            row = self.con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        else:
            try: root = git.inspect(cwd or os.getcwd())["root"]
            except LedgerError: root = ""
            row = self.con.execute("SELECT * FROM projects WHERE repository_root=?", (root,)).fetchone()
        if not row: raise LedgerError("PROJECT_REQUIRED" if not project_id else "PROJECT_NOT_FOUND", "A managed project could not be resolved.")
        return row

    def authenticate(self, project_id: str | None, supplied: str | None, role: str | None = None):
        # Worker tokens resolve solely to their assignment/project.
        if not supplied and project_id:
            credential = self.home / "credentials" / f"{project_id}.orchestrator-token"
            if credential.exists(): supplied = credential.read_text(encoding="utf-8").strip()
        if not supplied: raise LedgerError("AUTHENTICATION_FAILED", "A credential token is required.")
        digest = sha256(supplied)
        rows = self.con.execute("SELECT p.*,c.token_hash FROM principals p JOIN principal_credentials c ON c.principal_id=p.id WHERE p.active=1").fetchall()
        principal = next((r for r in rows if hmac.compare_digest(r["token_hash"], digest)), None)
        if not principal: raise LedgerError("AUTHENTICATION_FAILED", "Credential is invalid or inactive.")
        if project_id and principal["project_id"] != project_id:
            raise LedgerError("AUTHORIZATION_DENIED", "Credential does not belong to this project.")
        if role and principal["role"] != role:
            raise LedgerError("AUTHORIZATION_DENIED", "This action requires an orchestrator credential." if role == "ORCHESTRATOR" else "This action requires a worker credential.")
        return principal

    def auth_orchestrator(self, project_id: str | None, supplied: str | None):
        project = self.project(project_id)
        return project, self.authenticate(project["id"], supplied, "ORCHESTRATOR")

    def ensure_no_operation(self, project_id: str) -> None:
        if self.con.execute("SELECT 1 FROM operations WHERE project_id=? AND state IN ('STARTED','UNCERTAIN')", (project_id,)).fetchone():
            raise LedgerError("RECOVERY_REQUIRED", "A Git operation requires recovery before this action.", actions=["project.recover"])

    def invalidate_completion(self, project_id: str, reason: str) -> None:
        row = self.con.execute("SELECT lifecycle FROM projects WHERE id=?", (project_id,)).fetchone()
        if row and row[0] == "COMPLETED":
            self.con.execute("UPDATE projects SET lifecycle='ACTIVE',completed_at=NULL,completion_head_oid=NULL,updated_at=? WHERE id=?", (now(), project_id))
            self.audit(project_id, None, "COMPLETION_INVALIDATED", "PROJECT", project_id, {"reason": reason})

    def invalid_requirements(self, ids: list[str], reason: str) -> None:
        if not ids: return
        marks = ",".join("?" * len(ids))
        self.con.execute(f"UPDATE requirement_verifications SET state='INVALIDATED',invalidated_at=?,invalidation_reason=? WHERE state='CURRENT' AND requirement_id IN ({marks})", (now(), reason, *ids))

    def current_links(self, task_id: str) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT requirement_id FROM task_requirement_links l JOIN tasks t ON t.id=l.task_id AND t.current_revision=l.task_revision WHERE l.task_id=? ORDER BY requirement_id", (task_id,))]

    def current_tasks_for_requirement(self, requirement_id: str):
        return self.con.execute("SELECT t.* FROM tasks t JOIN task_requirement_links l ON l.task_id=t.id AND l.task_revision=t.current_revision WHERE l.requirement_id=? AND t.state<>'CANCELLED' ORDER BY t.id", (requirement_id,)).fetchall()

    # ---- project ----
    def init(self, repo: str, confirm: str | None) -> dict[str, Any]:
        info = git.inspect(repo)
        existing = self.con.execute(
            "SELECT id,canonical_branch,lifecycle FROM projects WHERE repository_root=?",
            (info["root"],),
        ).fetchone()
        if existing:
            return {
                "project_id": existing["id"],
                "repository_root": info["root"],
                "canonical_branch": existing["canonical_branch"],
                "current_branch": info["branch"],
                "completed": existing["lifecycle"] == "COMPLETED",
                "already_initialized": True,
                "confirmation_required": False,
                "next_action": "project.recover",
                "message": "Taskledger is already initialized for this repository. Continue with the existing project.",
            }
        if not confirm:
            return {
                "repository_root": info["root"],
                "detected_branch": info["branch"],
                "has_commits": info["has_commits"],
                "confirmation_required": True,
            }
        if confirm != info["branch"]:
            raise LedgerError("BRANCH_CONFIRMATION_MISMATCH", "Confirmation must exactly match the detected branch.", details={"detected_branch": info["branch"]})
        pid, principal, raw = new_id(), new_id(), token()
        stamp = now()
        path = self.home / "credentials" / f"{pid}.orchestrator-token"
        with transaction(self.con):
            self.con.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)", (pid, info["root"], info["common"], info["branch"], stamp, "ACTIVE", None, None, None, stamp, stamp))
            self.con.execute("INSERT INTO principals VALUES(?,?,?,?,?,?,?)", (principal, pid, "ORCHESTRATOR", None, 1, stamp, None))
            self.con.execute("INSERT INTO principal_credentials VALUES(?,?,?,?)", (principal, sha256(raw), stamp, None))
            self.audit(pid, principal, "PROJECT_INITIALIZED", "PROJECT", pid, {"canonical_branch": info["branch"]})
        try: atomic_secret(path, raw)
        except Exception:
            # Credential file is required; leave no unusable project.
            with transaction(self.con): self.con.execute("DELETE FROM projects WHERE id=?", (pid,))
            raise
        return {
            "project_id": pid,
            "repository_root": info["root"],
            "canonical_branch": info["branch"],
            "orchestrator_token_path": str(path),
            "already_initialized": False,
            "initial_commit_required": not info["has_commits"],
            "message": (
                "Taskledger is initialized. Create the repository's first commit before starting implementation."
                if not info["has_commits"] else "Taskledger is initialized."
            ),
        }

    def preflight(self, project):
        self.reconcile_operations(project)
        self.reconcile_integrations(project)
        self.check_specs(project, None, preflight=True)

    def reconcile_operations(self, project):
        """Resolve only journal outcomes that repository facts prove without interpretation."""
        operations = self.con.execute("SELECT * FROM operations WHERE project_id=? AND state='STARTED' ORDER BY started_at,id", (project["id"],)).fetchall()
        for op in operations:
            expected = json.loads(op["expected_state_json"])
            root = project["repository_root"]
            if op["kind"] == "SUBMISSION_CHECKPOINT":
                worktree = expected["worktree"]
                try:
                    head, clean = git.oid(worktree), git.clean(worktree)
                    known = clean and not git.operation_in_progress(worktree)
                except LedgerError:
                    known = False
                with transaction(self.con):
                    if known:
                        result = "NO_CHECKPOINT" if head == expected["head"] else "CHECKPOINT_CREATED_WITHOUT_SUBMISSION"
                        self.con.execute("UPDATE operations SET state='FAILED',result_json=?,finished_at=? WHERE id=?", (canonical({"result": result, "head": head}), now(), op["id"]))
                        self.audit(project["id"], None, "SUBMISSION_OPERATION_RECONCILED", "OPERATION", op["id"], {"result": result})
                    else:
                        self.con.execute("UPDATE operations SET state='UNCERTAIN',finished_at=? WHERE id=?", (now(), op["id"]))
                        self.system_blocker(project["id"], "INTERRUPTED_OPERATION", "OPERATION", op["id"], "Submission checkpoint outcome cannot be proven")
                continue
            if op["kind"] == "INTEGRATION":
                before, accepted = expected["before"], expected["accepted"]
                head = git.oid(root)
                attempt = self.con.execute("SELECT * FROM integration_attempts WHERE project_id=? AND task_id=? AND state='STARTED' ORDER BY started_at DESC LIMIT 1", (project["id"], op["entity_id"])).fetchone()
                with transaction(self.con):
                    if head == before and not git.operation_in_progress(root):
                        self.con.execute("UPDATE operations SET state='FAILED',result_json=?,finished_at=? WHERE id=?", (canonical({"result":"NO_CHANGE"}), now(), op["id"]))
                        if attempt: self.con.execute("UPDATE integration_attempts SET state='FAILED',finished_at=? WHERE id=?", (now(), attempt["id"]))
                    elif git.ancestor(root, accepted, head) and not git.operation_in_progress(root):
                        self.con.execute("UPDATE operations SET state='SUCCEEDED',result_json=?,finished_at=? WHERE id=?", (canonical({"after":head,"reconciled":True}), now(), op["id"]))
                        if attempt:
                            self.con.execute("UPDATE integration_attempts SET state='SUCCEEDED',canonical_after_oid=?,finished_at=? WHERE id=?", (head, now(), attempt["id"]))
                            self.con.execute("UPDATE tasks SET state='COMPLETED',completed_at=?,updated_at=? WHERE id=?", (now(), now(), attempt["task_id"]))
                    else:
                        self.con.execute("UPDATE operations SET state='UNCERTAIN',finished_at=? WHERE id=?", (now(), op["id"]))
                        if attempt: self.con.execute("UPDATE integration_attempts SET state='UNCERTAIN',finished_at=? WHERE id=?", (now(), attempt["id"]))
                        self.system_blocker(project["id"], "INTERRUPTED_OPERATION", "OPERATION", op["id"], "Integration outcome cannot be proven")
                continue
            # Assignment preparation has a complete Git proof. If the process died
            # before credential activation, create a fresh scoped credential; it is
            # deliberately recoverable through assignment rotate-token.
            branch, worktree, base = expected["branch"], expected["worktree"], expected["base"]
            exists = git.run(root, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0
            assignment = self.con.execute("SELECT * FROM assignments WHERE id=?", (op["entity_id"],)).fetchone()
            worker = assignment and self.con.execute("SELECT 1 FROM principals WHERE assignment_id=? AND role='WORKER'", (assignment["id"],)).fetchone()
            safe = exists and Path(worktree).exists() and git.oid(root, f"refs/heads/{branch}") == base and git.current_branch(worktree) == branch and git.clean(worktree)
            absent = not exists and not Path(worktree).exists()
            recovered_token = token() if safe and not worker else None
            with transaction(self.con):
                if safe:
                    self.con.execute("UPDATE assignments SET state='ACTIVE',activated_at=COALESCE(activated_at,?),context_json=? WHERE id=?", (now(), self.context(assignment), assignment["id"]))
                    self.con.execute("UPDATE tasks SET state='ASSIGNED',updated_at=? WHERE id=?", (now(), assignment["task_id"]))
                    if recovered_token:
                        worker_id = new_id()
                        self.con.execute("INSERT INTO principals VALUES(?,?,?,?,?,?,?)", (worker_id, project["id"], "WORKER", assignment["id"], 1, now(), None))
                        self.con.execute("INSERT INTO principal_credentials VALUES(?,?,?,?)", (worker_id, sha256(recovered_token), now(), None))
                    self.con.execute("UPDATE operations SET state='SUCCEEDED',finished_at=? WHERE id=?", (now(), op["id"]))
                elif absent:
                    self.con.execute("UPDATE assignments SET state='REVOKED',revoked_at=?,revocation_reason=? WHERE id=?", (now(), "assignment preparation did not create Git state", assignment["id"]))
                    self.con.execute("UPDATE operations SET state='FAILED',finished_at=? WHERE id=?", (now(), op["id"]))
                else:
                    self.con.execute("UPDATE assignments SET state='UNCERTAIN' WHERE id=?", (assignment["id"],))
                    self.con.execute("UPDATE operations SET state='UNCERTAIN',finished_at=? WHERE id=?", (now(), op["id"]))
                    self.system_blocker(project["id"], "INTERRUPTED_OPERATION", "OPERATION", op["id"], "Assignment preparation outcome cannot be proven")
            if recovered_token:
                atomic_secret(self.home / "projects" / project["id"] / "assignments" / assignment["id"] / "worker-token", recovered_token)

    def phase(self, project) -> str:
        if self.con.execute("SELECT 1 FROM specification_reviews WHERE project_id=? AND state='PENDING'", (project["id"],)).fetchone(): return "SPECIFICATION_REVIEW_REQUIRED"
        if self.con.execute("SELECT 1 FROM operations WHERE project_id=? AND state IN ('STARTED','UNCERTAIN')", (project["id"],)).fetchone(): return "RECOVERY_REQUIRED"
        if project["lifecycle"] == "COMPLETED": return "COMPLETED"
        return "EXECUTION" if self.plan_current(project["id"])[0] else "PLANNING"

    def set_branch(self, project, principal, data):
        require_object(data, {"new_branch", "confirm_branch"}, {"new_branch", "confirm_branch"})
        branch, confirm = text(data["new_branch"], "new_branch"), text(data["confirm_branch"], "confirm_branch")
        if branch != confirm: raise LedgerError("BRANCH_CONFIRMATION_MISMATCH", "New branch confirmation must exactly match.")
        self.ensure_no_operation(project["id"])
        if self.con.execute("SELECT 1 FROM specification_reviews WHERE project_id=? AND state='PENDING'", (project["id"],)).fetchone():
            raise LedgerError("SPECIFICATION_REVIEW_REQUIRED", "Canonical branch cannot change while specification review is pending.")
        if git.run(project["repository_root"], ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode:
            raise LedgerError("CANONICAL_BRANCH_NOT_FOUND", "The named local branch does not exist.")
        if self.con.execute("SELECT 1 FROM tasks WHERE project_id=? AND state IN ('ASSIGNED','SUBMITTED','ACCEPTED')", (project["id"],)).fetchone():
            raise LedgerError("TASK_STATE_INVALID", "Canonical branch cannot change while work is active.")
        bad = [r[0] for r in self.con.execute("SELECT canonical_after_oid FROM integration_attempts WHERE project_id=? AND state='SUCCEEDED'", (project["id"],)) if not git.ancestor(project["repository_root"], r[0], f"refs/heads/{branch}")]
        if bad: raise LedgerError("STALE_STATE", "New branch omits recorded integrations.", details={"integration_oids": bad})
        with transaction(self.con):
            self.con.execute("UPDATE projects SET canonical_branch=?,updated_at=? WHERE id=?", (branch, now(), project["id"]))
            self.invalidate_completion(project["id"], "canonical branch changed")
            self.audit(project["id"], principal["id"], "CANONICAL_BRANCH_CHANGED", "PROJECT", project["id"], {"branch": branch})
        return {"canonical_branch": branch}

    # ---- specifications ----
    def observe_spec(self, project, spec, *, allow_baseline: bool) -> tuple[str, str | None, bytes | None, str | None]:
        path = (Path(project["repository_root"]) / spec["relative_path"])
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(Path(project["repository_root"])) or not resolved.is_file():
                return "MISSING", None, None, "Specification is missing or not a regular file."
            raw = resolved.read_bytes()
            if len(raw) > MAX_SPEC: return "UNREADABLE", None, None, "Specification exceeds the 50 MiB limit."
            return "PRESENT", sha256(raw), raw, None
        except FileNotFoundError: return "MISSING", None, None, "Specification file is missing."
        except OSError as exc: return "UNREADABLE", None, None, f"Specification cannot be read: {exc.__class__.__name__}"

    def register_spec(self, project, principal, data):
        require_object(data, {"relative_path"}, {"relative_path"})
        relative = text(data["relative_path"], "relative_path")
        candidate = (Path(project["repository_root"]) / relative)
        try: resolved = candidate.resolve(strict=True)
        except OSError: raise LedgerError("SPECIFICATION_NOT_FOUND", "Specification must be a present readable file.")
        if not resolved.is_relative_to(Path(project["repository_root"])): raise LedgerError("SPECIFICATION_OUTSIDE_REPOSITORY", "Specification must remain inside repository root.")
        if not resolved.is_file(): raise LedgerError("SPECIFICATION_NOT_FOUND", "Specification must be a regular file.")
        if self.con.execute("SELECT 1 FROM specifications WHERE project_id=? AND relative_path=?", (project["id"], relative)).fetchone(): raise LedgerError("STALE_STATE", "Specification is already registered.")
        spec, rev, stamp = new_id(), new_id(), now()
        raw = resolved.read_bytes()
        if len(raw) > MAX_SPEC: raise LedgerError("INVALID_REQUEST", "Specification exceeds the 50 MiB limit.")
        with transaction(self.con):
            self.con.execute("INSERT INTO specifications VALUES(?,?,?,?,?,?,?)", (spec, project["id"], relative, "ACTIVE", None, stamp, None))
            self.con.execute("INSERT INTO specification_revisions VALUES(?,?,?,?,?,?,?,?,?)", (rev, spec, 1, "PRESENT", sha256(raw), raw, None, stamp, stamp if not project["planning_started_at"] else None))
            if project["planning_started_at"]:
                review = new_id()
                self.con.execute("INSERT INTO specification_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (review, project["id"], spec, None, rev, "ADDED", "PENDING", None, None, None, None, None, None, stamp, None))
                self.invalidate_completion(project["id"], "specification registered after planning")
            else:
                self.con.execute("UPDATE specifications SET active_revision_id=? WHERE id=?", (rev, spec))
            self.audit(project["id"], principal["id"], "SPECIFICATION_REGISTERED", "SPECIFICATION", spec, {"path": relative})
        return {"specification_id": spec, "relative_path": relative, "review_required": bool(project["planning_started_at"])}

    def check_specs(self, project, principal, *, preflight: bool = False):
        observed = []
        specs = self.con.execute("SELECT * FROM specifications WHERE project_id=? AND lifecycle='ACTIVE' ORDER BY id", (project["id"],)).fetchall()
        for spec in specs:
            state, digest, raw, error = self.observe_spec(project, spec, allow_baseline=not bool(project["planning_started_at"]))
            reference = self.con.execute("SELECT r.* FROM specification_revisions r WHERE r.id=COALESCE((SELECT to_revision_id FROM specification_reviews WHERE specification_id=? AND state='PENDING'),?)", (spec["id"], spec["active_revision_id"])).fetchone()
            if reference and reference["file_state"] == state and reference["content_hash"] == digest: continue
            with transaction(self.con):
                sequence = self.con.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM specification_revisions WHERE specification_id=?", (spec["id"],)).fetchone()[0]
                rev, stamp = new_id(), now()
                self.con.execute("INSERT INTO specification_revisions VALUES(?,?,?,?,?,?,?,?,?)", (rev, spec["id"], sequence, state, digest, raw, error, stamp, stamp if not project["planning_started_at"] and state=='PRESENT' else None))
                if not project["planning_started_at"] and state == "PRESENT":
                    self.con.execute("UPDATE specifications SET active_revision_id=? WHERE id=?", (rev, spec["id"]))
                else:
                    old = self.con.execute("SELECT * FROM specification_reviews WHERE specification_id=? AND state='PENDING'", (spec["id"],)).fetchone()
                    active = self.con.execute("SELECT * FROM specification_revisions WHERE id=?", (spec["active_revision_id"],)).fetchone()
                    kind = "RESTORED" if state == "PRESENT" and active and active["file_state"] != "PRESENT" else ("MODIFIED" if state == "PRESENT" else state)
                    if old:
                        self.con.execute("UPDATE specification_reviews SET to_revision_id=?,change_kind=? WHERE id=?", (rev, kind, old["id"]))
                        self.audit(project["id"], None, "SPECIFICATION_REVIEW_RETARGETED", "SPECIFICATION_REVIEW", old["id"], {"previous": old["to_revision_id"], "latest": rev})
                    else:
                        review = new_id(); self.con.execute("INSERT INTO specification_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (review, project["id"], spec["id"], spec["active_revision_id"], rev, kind, "PENDING", None, None, None, None, None, None, stamp, None))
                    if state != "PRESENT": self.system_blocker(project["id"], "SPECIFICATION_STATE", "SPECIFICATION", spec["id"], error or "Specification unavailable")
                    self.invalidate_completion(project["id"], "specification change requires review")
                self.audit(project["id"], principal["id"] if principal else None, "SPECIFICATION_OBSERVED", "SPECIFICATION", spec["id"], {"state": state, "hash": digest})
            observed.append(spec["id"])
        pending = [dict(r) for r in self.con.execute("SELECT * FROM specification_reviews WHERE project_id=? AND state='PENDING' ORDER BY id", (project["id"],))]
        return {"observed": observed, "pending_reviews": pending}

    def review_spec(self, project, principal, data):
        require_object(data, {"review_id", "resolution", "affected_requirement_ids", "affected_task_ids", "no_existing_items_affected", "summary"},
                       {"review_id", "resolution", "affected_requirement_ids", "affected_task_ids", "no_existing_items_affected", "summary"})
        review = self.con.execute("SELECT * FROM specification_reviews WHERE id=? AND project_id=? AND state='PENDING'", (data["review_id"], project["id"])).fetchone()
        if not review: raise LedgerError("SPECIFICATION_NOT_FOUND", "Pending specification review was not found.")
        req_ids, task_ids = array(data["affected_requirement_ids"], "affected_requirement_ids"), array(data["affected_task_ids"], "affected_task_ids")
        none = data["no_existing_items_affected"]
        if not isinstance(none, bool) or ((not req_ids and not task_ids) != none):
            raise LedgerError("INVALID_REQUEST", "Use exactly one affected-items assertion.")
        if data["resolution"] not in {"APPROVE_REVISION", "RETIRE_SPECIFICATION"}: raise LedgerError("INVALID_REQUEST", "Invalid review resolution.")
        rev = self.con.execute("SELECT * FROM specification_revisions WHERE id=?", (review["to_revision_id"],)).fetchone()
        if data["resolution"] == "APPROVE_REVISION" and rev["file_state"] != "PRESENT":
            raise LedgerError("INVALID_REQUEST", "Only a present readable specification revision can be approved.")
        for rid in req_ids:
            if not self.con.execute("SELECT 1 FROM requirements WHERE id=? AND project_id=? AND lifecycle='ACTIVE'", (rid, project["id"])).fetchone(): raise LedgerError("REQUIREMENT_NOT_ACTIVE", "Affected requirement is not active.", details={"requirement_id": rid})
        for tid in task_ids:
            if not self.con.execute("SELECT 1 FROM tasks WHERE id=? AND project_id=? AND state<>'CANCELLED'", (tid, project["id"])).fetchone(): raise LedgerError("TASK_NOT_FOUND", "Affected task is not current and non-cancelled.", details={"task_id": tid})
        affected = set(req_ids)
        for tid in task_ids: affected.update(self.current_links(tid))
        checkpoint_tasks=set(task_ids)
        for rid in req_ids:checkpoint_tasks.update(task["id"] for task in self.current_tasks_for_requirement(rid))
        with transaction(self.con):
            stamp = now()
            self.con.execute("UPDATE specification_reviews SET state='COMPLETED',resolution=?,affected_requirement_ids_json=?,affected_task_ids_json=?,no_existing_items_affected=?,review_summary=?,reviewer_principal_id=?,completed_at=? WHERE id=?", (data["resolution"], canonical(req_ids), canonical(task_ids), int(none), text(data["summary"], "summary"), principal["id"], stamp, review["id"]))
            if data["resolution"] == "RETIRE_SPECIFICATION":
                self.con.execute("UPDATE specifications SET lifecycle='RETIRED',retired_at=? WHERE id=?", (stamp, review["specification_id"]))
            else:
                self.con.execute("UPDATE specifications SET active_revision_id=? WHERE id=?", (rev["id"], review["specification_id"]))
            self.invalid_requirements(sorted(affected), "affected specification review")
            if checkpoint_tasks:
                placeholders=",".join("?" for _ in checkpoint_tasks)
                self.con.execute(f"UPDATE assignment_checkpoints SET state='SUPERSEDED' WHERE assignment_id IN (SELECT id FROM assignments WHERE project_id=? AND task_id IN ({placeholders}) AND state='ACTIVE') AND state IN ('PENDING','APPROVED','REJECTED')",(project["id"],*sorted(checkpoint_tasks)))
            self.invalidate_completion(project["id"], "specification review completed")
            self.audit(project["id"], principal["id"], "SPECIFICATION_REVIEW_COMPLETED", "SPECIFICATION_REVIEW", review["id"], {"affected_requirements": sorted(affected)})
        return {"review_id": review["id"], "state": "COMPLETED", "plan_revalidation_required": True}

    # ---- requirements/tasks/plan ----
    def requirement_definition(self, data):
        require_object(data, {"statement", "details", "implementation_required", "sources", "requirement_id", "assignment_dispositions", "reason"},
                       {"statement", "details", "implementation_required", "sources"})
        if not isinstance(data["implementation_required"], bool): raise LedgerError("INVALID_REQUEST", "implementation_required must be boolean.")
        sources = array(data["sources"], "sources")
        if not sources: raise LedgerError("INVALID_REQUEST", "At least one source is required.")
        cleaned=[]
        for item in sources:
            require_object(item, {"specification_id", "locator", "excerpt"}, {"specification_id", "locator"})
            cleaned.append({"specification_id": text(item["specification_id"], "specification_id"), "locator": text(item["locator"], "locator"), "excerpt": item.get("excerpt")})
        return text(data["statement"], "statement"), text(data["details"], "details"), data["implementation_required"], cleaned

    def assignment_dispositions(self, project, data, affected_tasks):
        active = self.con.execute("SELECT a.* FROM assignments a WHERE a.project_id=? AND a.state IN ('PREPARING','ACTIVE') AND a.task_id IN (%s)" % ",".join("?" * max(1,len(affected_tasks))), (project["id"], *(affected_tasks or [""]))).fetchall()
        supplied = {x.get("assignment_id"): x.get("action") for x in data.get("assignment_dispositions", []) if isinstance(x, dict)}
        if {a["id"] for a in active} != set(supplied): raise LedgerError("INVALID_REQUEST", "A disposition is required for every active affected assignment.")
        for assignment in active:
            if supplied[assignment["id"]] not in {"CONTINUE", "REVOKE"}: raise LedgerError("INVALID_REQUEST", "Assignment disposition must be CONTINUE or REVOKE.")
            if supplied[assignment["id"]] == "REVOKE": self.revoke_assignment(project, None, assignment, "definition revised")
            else:
                self.con.execute("UPDATE assignment_checkpoints SET state='SUPERSEDED' WHERE assignment_id=? AND state IN ('PENDING','APPROVED','REJECTED')",(assignment["id"],))
                self.con.execute("UPDATE assignments SET context_json=? WHERE id=?", (self.context(assignment), assignment["id"]))

    def create_requirement(self, project, principal, data):
        require_object(data,{"statement","details","implementation_required","sources"},{"statement","details","implementation_required","sources"})
        statement, details, required, sources = self.requirement_definition(data)
        rid, stamp = new_id(), now()
        with transaction(self.con):
            for source in sources:
                if not self.con.execute("SELECT 1 FROM specifications WHERE id=? AND project_id=? AND lifecycle='ACTIVE'", (source["specification_id"], project["id"])).fetchone(): raise LedgerError("SPECIFICATION_NOT_FOUND", "Requirement source specification is not active.")
            self.con.execute("INSERT INTO requirements VALUES(?,?,?,?,?,?,?)", (rid, project["id"], 1, "ACTIVE", stamp, stamp, None))
            self.con.execute("INSERT INTO requirement_revisions VALUES(?,?,?,?,?,?,?)", (rid, 1, statement, details, int(required), principal["id"], stamp))
            for source in sources: self.con.execute("INSERT INTO requirement_source_refs VALUES(?,?,?,?,?)", (rid,1,source["specification_id"],source["locator"],source["excerpt"]))
            self.con.execute("UPDATE projects SET planning_started_at=COALESCE(planning_started_at,?),updated_at=? WHERE id=?", (stamp,stamp,project["id"]))
            self.invalidate_completion(project["id"], "requirement created")
            self.audit(project["id"], principal["id"], "REQUIREMENT_CREATED", "REQUIREMENT", rid)
        return {"requirement_id": rid, "revision": 1}

    def update_requirement(self, project, principal, data, retire=False):
        allowed={"requirement_id","statement","details","implementation_required","sources","assignment_dispositions","reason"}
        require_object(data, allowed, {"requirement_id","reason"} if retire else {"requirement_id","statement","details","implementation_required","sources"})
        row=self.con.execute("SELECT * FROM requirements WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(data["requirement_id"],project["id"])).fetchone()
        if not row: raise LedgerError("REQUIREMENT_NOT_ACTIVE","Requirement is not active.")
        affected=[t["id"] for t in self.current_tasks_for_requirement(row["id"])]
        with transaction(self.con):
            self.assignment_dispositions(project,data,affected)
            if retire:
                self.con.execute("UPDATE requirements SET lifecycle='RETIRED',retired_at=?,updated_at=? WHERE id=?",(now(),now(),row["id"]))
            else:
                statement,details,required,sources=self.requirement_definition(data); revision=row["current_revision"]+1
                for source in sources:
                    if not self.con.execute("SELECT 1 FROM specifications WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(source["specification_id"],project["id"])).fetchone(): raise LedgerError("SPECIFICATION_NOT_FOUND","Requirement source specification is not active.")
                self.con.execute("INSERT INTO requirement_revisions VALUES(?,?,?,?,?,?,?)",(row["id"],revision,statement,details,int(required),principal["id"],now()))
                for source in sources:self.con.execute("INSERT INTO requirement_source_refs VALUES(?,?,?,?,?)",(row["id"],revision,source["specification_id"],source["locator"],source["excerpt"]))
                self.con.execute("UPDATE requirements SET current_revision=?,updated_at=? WHERE id=?",(revision,now(),row["id"]))
            self.invalid_requirements([row["id"]],"requirement revised or retired")
            for assignment in self.con.execute("SELECT * FROM assignments WHERE project_id=? AND task_id IN (%s) AND state='ACTIVE'" % ",".join("?"*max(1,len(affected))),(project["id"],*(affected or [""]))):
                self.con.execute("UPDATE assignments SET context_json=? WHERE id=?",(self.context(assignment),assignment["id"]))
            self.invalidate_completion(project["id"],"requirement changed")
            self.audit(project["id"],principal["id"],"REQUIREMENT_RETIRED" if retire else "REQUIREMENT_UPDATED","REQUIREMENT",row["id"])
        return {"requirement_id":row["id"],"lifecycle":"RETIRED" if retire else "ACTIVE"}

    def supersede_requirement(self,project,principal,data):
        require_object(data,{"requirement_id","reason","replacement","assignment_dispositions"},{"requirement_id","reason","replacement"})
        old=self.con.execute("SELECT * FROM requirements WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(data["requirement_id"],project["id"])).fetchone()
        if not old:raise LedgerError("REQUIREMENT_NOT_ACTIVE","Requirement is not active.")
        replacement=data["replacement"]
        if not isinstance(replacement,dict):raise LedgerError("INVALID_REQUEST","replacement must be an object.")
        statement,details,required,sources=self.requirement_definition(replacement)
        for source in sources:
            if not self.con.execute("SELECT 1 FROM specifications WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(source["specification_id"],project["id"])).fetchone():raise LedgerError("SPECIFICATION_NOT_FOUND","Replacement requirement source specification is not active.")
        replacement_id,stamp=new_id(),now();reason=text(data["reason"],"reason")
        affected=[t["id"] for t in self.current_tasks_for_requirement(old["id"])]
        with transaction(self.con):
            self.assignment_dispositions(project,data,affected)
            self.con.execute("INSERT INTO requirements VALUES(?,?,?,?,?,?,?)",(replacement_id,project["id"],1,"ACTIVE",stamp,stamp,None))
            self.con.execute("INSERT INTO requirement_revisions VALUES(?,?,?,?,?,?,?)",(replacement_id,1,statement,details,int(required),principal["id"],stamp))
            for source in sources:self.con.execute("INSERT INTO requirement_source_refs VALUES(?,?,?,?,?)",(replacement_id,1,source["specification_id"],source["locator"],source["excerpt"]))
            self.con.execute("UPDATE requirements SET lifecycle='RETIRED',retired_at=?,updated_at=? WHERE id=?",(stamp,stamp,old["id"]))
            self.con.execute("INSERT INTO requirement_supersessions VALUES(?,?,?,?,?)",(old["id"],replacement_id,reason,principal["id"],stamp))
            self.invalid_requirements([old["id"]],"requirement superseded")
            self.invalidate_completion(project["id"],"requirement superseded")
            self.audit(project["id"],principal["id"],"REQUIREMENT_CREATED","REQUIREMENT",replacement_id,{"supersedes":old["id"]})
            self.audit(project["id"],principal["id"],"REQUIREMENT_SUPERSEDED","REQUIREMENT",old["id"],{"replacement_requirement_id":replacement_id,"reason":reason})
        return {"superseded_requirement_id":old["id"],"replacement_requirement_id":replacement_id,"lifecycle":"RETIRED"}

    def task_definition(self, data):
        require_object(data,{"objective","implementation_scope","acceptance_criteria","required_checks","requirement_ids","dependency_task_ids","task_id","assignment_action","reason","submission_action","revoke_assignment"},
                       {"objective","implementation_scope","acceptance_criteria","requirement_ids","dependency_task_ids"})
        criteria=[text(x,"acceptance criterion") for x in array(data["acceptance_criteria"],"acceptance_criteria")]
        checks=[text(x,"required check") for x in array(data.get("required_checks",[]),"required_checks")]
        if len(checks)!=len(set(checks)):raise LedgerError("INVALID_REQUEST","required_checks must not contain duplicates.")
        return text(data["objective"],"objective"),text(data["implementation_scope"],"implementation_scope"),criteria,checks,[text(x,"requirement id") for x in array(data["requirement_ids"],"requirement_ids")],[text(x,"dependency task id") for x in array(data["dependency_task_ids"],"dependency_task_ids")]

    def put_task_revision(self, project, principal, tid, revision, definition):
        objective,scope,criteria,checks,requirements,deps=definition
        stamp=now(); self.con.execute("INSERT INTO task_revisions VALUES(?,?,?,?,?,?)",(tid,revision,objective,scope,principal["id"],stamp))
        for n,item in enumerate(criteria): self.con.execute("INSERT INTO task_acceptance_criteria VALUES(?,?,?,?,?)",(new_id(),tid,revision,n,item))
        for n,command in enumerate(checks):self.con.execute("INSERT INTO task_required_checks VALUES(?,?,?,?)",(tid,revision,n,command))
        for rid in requirements: self.con.execute("INSERT INTO task_requirement_links VALUES(?,?,?)",(tid,revision,rid))
        for dep in deps:self.con.execute("INSERT INTO task_dependencies VALUES(?,?,?)",(tid,revision,dep))

    def task_creation_visible(self, project_id, tid, definition):
        objective,scope,criteria,checks,requirements,deps=definition
        con=None
        try:
            con=sqlite3.connect(self.home/"taskledger.sqlite3",timeout=5)
            task=con.execute("SELECT project_id,current_revision,state FROM tasks WHERE id=?",(tid,)).fetchone()
            revision=con.execute("SELECT objective,implementation_scope FROM task_revisions WHERE task_id=? AND revision=1",(tid,)).fetchone()
            stored_criteria=[r[0] for r in con.execute("SELECT criterion_text FROM task_acceptance_criteria WHERE task_id=? AND task_revision=1 ORDER BY position",(tid,))]
            stored_checks=[r[0] for r in con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=1 ORDER BY position",(tid,))]
            stored_requirements=[r[0] for r in con.execute("SELECT requirement_id FROM task_requirement_links WHERE task_id=? AND task_revision=1 ORDER BY requirement_id",(tid,))]
            stored_deps=[r[0] for r in con.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? AND task_revision=1 ORDER BY depends_on_task_id",(tid,))]
            audited=con.execute("SELECT 1 FROM audit_events WHERE project_id=? AND event_type='TASK_CREATED' AND entity_type='TASK' AND entity_id=?",(project_id,tid)).fetchone()
            return task==(project_id,1,"PLANNED") and revision==(objective,scope) and stored_criteria==criteria and stored_checks==checks and stored_requirements==sorted(requirements) and stored_deps==sorted(deps) and bool(audited)
        except (OSError,sqlite3.Error):
            return False
        finally:
            if con is not None:con.close()

    def create_task(self, project, principal, data):
        require_object(data,{"objective","implementation_scope","acceptance_criteria","required_checks","requirement_ids","dependency_task_ids"},{"objective","implementation_scope","acceptance_criteria","requirement_ids","dependency_task_ids"})
        definition=self.task_definition(data); tid,stamp=new_id(),now()
        try:
            with transaction(self.con):
                self.con.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",(tid,project["id"],1,"PLANNED",None,stamp,stamp,None,None))
                self.put_task_revision(project,principal,tid,1,definition)
                self.con.execute("UPDATE projects SET planning_started_at=COALESCE(planning_started_at,?),updated_at=? WHERE id=?",(stamp,stamp,project["id"]))
                self.invalidate_completion(project["id"],"task created");self.audit(project["id"],principal["id"],"TASK_CREATED","TASK",tid)
        except Exception:
            try:self.con.rollback()
            except sqlite3.Error:pass
            if not self.task_creation_visible(project["id"],tid,definition):raise
        return {"task_id":tid,"revision":1,"state":"PLANNED"}

    def apply_plan(self,project,principal,data):
        require_object(data,{"requirements","tasks"},{"requirements","tasks"})
        requirement_items=array(data["requirements"],"requirements");task_items=array(data["tasks"],"tasks")
        if not requirement_items and not task_items:raise LedgerError("INVALID_REQUEST","Plan batch must create at least one requirement or task.")
        requirement_ids={};requirement_definitions={}
        for item in requirement_items:
            require_object(item,{"ref","statement","details","implementation_required","sources"},{"ref","statement","details","implementation_required","sources"})
            ref=text(item["ref"],"requirement ref")
            if ref in requirement_ids:raise LedgerError("INVALID_REQUEST","Requirement refs must be unique.")
            requirement_ids[ref]=new_id();requirement_definitions[ref]=self.requirement_definition({k:v for k,v in item.items() if k!="ref"})
        task_ids={}
        for item in task_items:
            if not isinstance(item,dict):raise LedgerError("INVALID_REQUEST","Each task must be an object.")
            ref=text(item.get("ref"),"task ref")
            if ref in task_ids:raise LedgerError("INVALID_REQUEST","Task refs must be unique.")
            task_ids[ref]=new_id()
        task_definitions={}
        allowed_task={"ref","objective","implementation_scope","acceptance_criteria","required_checks","requirement_refs","requirement_ids","dependency_refs","dependency_task_ids"}
        for item in task_items:
            require_object(item,allowed_task,{"ref","objective","implementation_scope","acceptance_criteria","requirement_refs","dependency_refs"})
            ref=item["ref"]
            requirement_refs=[text(x,"requirement ref") for x in array(item["requirement_refs"],"requirement_refs")]
            dependency_refs=[text(x,"dependency ref") for x in array(item["dependency_refs"],"dependency_refs")]
            unknown_requirements=sorted(set(requirement_refs)-set(requirement_ids));unknown_dependencies=sorted(set(dependency_refs)-set(task_ids))
            if unknown_requirements or unknown_dependencies:raise LedgerError("INVALID_REQUEST","Plan references an unknown local ref.",details={"requirement_refs":unknown_requirements,"dependency_refs":unknown_dependencies})
            existing_requirements=[text(x,"requirement id") for x in array(item.get("requirement_ids",[]),"requirement_ids")]
            existing_dependencies=[text(x,"dependency task id") for x in array(item.get("dependency_task_ids",[]),"dependency_task_ids")]
            for rid in existing_requirements:
                if not self.con.execute("SELECT 1 FROM requirements WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(rid,project["id"])).fetchone():raise LedgerError("REQUIREMENT_NOT_ACTIVE","An existing requirement reference is not active.",details={"requirement_id":rid})
            for tid in existing_dependencies:
                if not self.con.execute("SELECT 1 FROM tasks WHERE id=? AND project_id=?",(tid,project["id"])).fetchone():raise LedgerError("TASK_NOT_FOUND","An existing dependency task was not found.",details={"task_id":tid})
            resolved_requirements=existing_requirements+[requirement_ids[x] for x in requirement_refs]
            resolved_dependencies=existing_dependencies+[task_ids[x] for x in dependency_refs]
            if len(resolved_requirements)!=len(set(resolved_requirements)):raise LedgerError("INVALID_REQUEST","A task must not repeat a requirement link.",details={"task_ref":ref})
            if len(resolved_dependencies)!=len(set(resolved_dependencies)):raise LedgerError("INVALID_REQUEST","A task must not repeat a dependency.",details={"task_ref":ref})
            if task_ids[ref] in resolved_dependencies:raise LedgerError("INVALID_REQUEST","A task must not depend on itself.",details={"task_ref":ref})
            normalized={"objective":item["objective"],"implementation_scope":item["implementation_scope"],"acceptance_criteria":item["acceptance_criteria"],"required_checks":item.get("required_checks",[]),"requirement_ids":resolved_requirements,"dependency_task_ids":resolved_dependencies}
            task_definitions[ref]=self.task_definition(normalized)
        stamp=now()
        with transaction(self.con):
            for ref,(statement,details,required,sources) in requirement_definitions.items():
                rid=requirement_ids[ref]
                for source in sources:
                    if not self.con.execute("SELECT 1 FROM specifications WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(source["specification_id"],project["id"])).fetchone():raise LedgerError("SPECIFICATION_NOT_FOUND","Requirement source specification is not active.")
                self.con.execute("INSERT INTO requirements VALUES(?,?,?,?,?,?,?)",(rid,project["id"],1,"ACTIVE",stamp,stamp,None));self.con.execute("INSERT INTO requirement_revisions VALUES(?,?,?,?,?,?,?)",(rid,1,statement,details,int(required),principal["id"],stamp))
                for source in sources:self.con.execute("INSERT INTO requirement_source_refs VALUES(?,?,?,?,?)",(rid,1,source["specification_id"],source["locator"],source["excerpt"]))
                self.audit(project["id"],principal["id"],"REQUIREMENT_CREATED","REQUIREMENT",rid,{"plan_ref":ref})
            for ref,tid in task_ids.items():self.con.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",(tid,project["id"],1,"PLANNED",None,stamp,stamp,None,None))
            for ref,definition in task_definitions.items():
                self.put_task_revision(project,principal,task_ids[ref],1,definition);self.audit(project["id"],principal["id"],"TASK_CREATED","TASK",task_ids[ref],{"plan_ref":ref})
            self.con.execute("UPDATE projects SET planning_started_at=COALESCE(planning_started_at,?),updated_at=? WHERE id=?",(stamp,stamp,project["id"]));self.invalidate_completion(project["id"],"plan batch applied")
        return {"requirement_ids":requirement_ids,"task_ids":task_ids,"requirements_created":len(requirement_ids),"tasks_created":len(task_ids)}

    def update_task(self, project, principal, data, reopen=False):
        if "task_id" not in data: raise LedgerError("INVALID_REQUEST", "task_id is required.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=? AND project_id=?",(data["task_id"],project["id"])).fetchone()
        if not task: raise LedgerError("TASK_NOT_FOUND","Task was not found.")
        allowed={"COMPLETED","CANCELLED"} if reopen else {"PLANNED","ASSIGNED"}
        if task["state"] not in allowed: raise LedgerError("TASK_STATE_INVALID","Task cannot be revised in its current state.",details={"state":task["state"]})
        definition=self.task_definition(data)
        prior_links=self.current_links(task["id"])
        active=self.con.execute("SELECT * FROM assignments WHERE task_id=? AND state IN ('PREPARING','ACTIVE')",(task["id"],)).fetchone()
        if active and data.get("assignment_action") not in {"CONTINUE","REVOKE"}: raise LedgerError("INVALID_REQUEST","assignment_action is required for an active assignment.")
        with transaction(self.con):
            revision=task["current_revision"]+1
            self.put_task_revision(project,principal,task["id"],revision,definition)
            state="PLANNED" if reopen else task["state"]
            self.con.execute("UPDATE tasks SET current_revision=?,state=?,cancellation_reason=NULL,cancelled_at=NULL,updated_at=? WHERE id=?",(revision,state,now(),task["id"]))
            if active:
                if data["assignment_action"] == "REVOKE": self.revoke_assignment(project,principal,active,"task revised")
                else:
                    self.con.execute("UPDATE assignment_checkpoints SET state='SUPERSEDED' WHERE assignment_id=? AND state IN ('PENDING','APPROVED','REJECTED')",(active["id"],))
                    self.con.execute("UPDATE assignments SET task_revision=?,context_json=? WHERE id=?",(revision,self.context(active, task_revision=revision),active["id"]))
            affected=sorted(set(prior_links) | set(self.current_links(task["id"])));self.invalid_requirements(affected,"supporting task revised")
            self.invalidate_completion(project["id"],"task revised")
            self.audit(project["id"],principal["id"],"TASK_REOPENED" if reopen else "TASK_UPDATED","TASK",task["id"],{"revision":revision})
        return {"task_id":task["id"],"revision":revision,"state":state}

    def cancel_task(self, project, principal, data):
        require_object(data,{"task_id","reason","revoke_assignment","submission_action"},{"task_id","reason"})
        task=self.con.execute("SELECT * FROM tasks WHERE id=? AND project_id=?",(data["task_id"],project["id"])).fetchone()
        if not task: raise LedgerError("TASK_NOT_FOUND","Task was not found.")
        if task["state"]=="COMPLETED": raise LedgerError("TASK_STATE_INVALID","Completed tasks must be reopened before cancellation.")
        assignment=self.con.execute("SELECT * FROM assignments WHERE task_id=? AND state IN ('PREPARING','ACTIVE')",(task["id"],)).fetchone()
        pending=self.con.execute("SELECT * FROM submissions WHERE assignment_id IN (SELECT id FROM assignments WHERE task_id=?) AND state IN ('PENDING','BLOCKED')",(task["id"],)).fetchone()
        if assignment and data.get("revoke_assignment") is not True: raise LedgerError("INVALID_REQUEST","Active assignment requires revoke_assignment=true.")
        if pending and data.get("submission_action")!="SUPERSEDE": raise LedgerError("INVALID_REQUEST","Pending submission requires submission_action=SUPERSEDE.")
        with transaction(self.con):
            if assignment:self.revoke_assignment(project,principal,assignment,"task cancelled")
            if pending:self.con.execute("UPDATE submissions SET state='SUPERSEDED',resolved_at=? WHERE id=?",(now(),pending["id"]))
            self.con.execute("UPDATE tasks SET state='CANCELLED',cancellation_reason=?,cancelled_at=?,updated_at=? WHERE id=?",(text(data["reason"],"reason"),now(),now(),task["id"]))
            self.invalid_requirements(self.current_links(task["id"]),"supporting task cancelled")
            self.invalidate_completion(project["id"],"task cancelled"); self.audit(project["id"],principal["id"],"TASK_CANCELLED","TASK",task["id"])
        return {"task_id":task["id"],"state":"CANCELLED"}

    def plan_object(self, project_id: str):
        requirements=[]
        for r in self.con.execute("SELECT r.*,v.statement,v.details,v.implementation_required FROM requirements r JOIN requirement_revisions v ON v.requirement_id=r.id AND v.revision=r.current_revision WHERE r.project_id=? AND r.lifecycle='ACTIVE' ORDER BY r.id",(project_id,)):
            sources=[dict(x) for x in self.con.execute("SELECT specification_id,locator,excerpt FROM requirement_source_refs WHERE requirement_id=? AND requirement_revision=? ORDER BY specification_id,locator,COALESCE(excerpt,'')",(r["id"],r["current_revision"]))]
            requirements.append({"id":r["id"],"revision":r["current_revision"],"statement":r["statement"],"details":r["details"],"implementation_required":bool(r["implementation_required"]),"sources":sources})
        tasks=[]
        for t in self.con.execute("SELECT t.*,v.objective,v.implementation_scope FROM tasks t JOIN task_revisions v ON v.task_id=t.id AND v.revision=t.current_revision WHERE t.project_id=? ORDER BY t.id",(project_id,)):
            criteria=[dict(x) for x in self.con.execute("SELECT id,position,criterion_text FROM task_acceptance_criteria WHERE task_id=? AND task_revision=? ORDER BY position",(t["id"],t["current_revision"]))]
            checks=[x[0] for x in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=? ORDER BY position",(t["id"],t["current_revision"]))]
            links=[x[0] for x in self.con.execute("SELECT requirement_id FROM task_requirement_links WHERE task_id=? AND task_revision=? ORDER BY requirement_id",(t["id"],t["current_revision"]))]
            deps=[x[0] for x in self.con.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? AND task_revision=? ORDER BY depends_on_task_id",(t["id"],t["current_revision"]))]
            tasks.append({"id":t["id"],"revision":t["current_revision"],"state":t["state"],"objective":t["objective"],"implementation_scope":t["implementation_scope"],"criteria":criteria,"required_checks":checks,"requirements":links,"dependencies":deps})
        specs=[]
        for s in self.con.execute("SELECT s.id,s.relative_path,s.lifecycle,r.content_hash,r.file_state FROM specifications s LEFT JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.project_id=? ORDER BY s.id",(project_id,)): specs.append(dict(s))
        return {"requirements":requirements,"tasks":tasks,"specifications":specs}

    def validate_plan(self, project, principal):
        obj=self.plan_object(project["id"]); diagnostics=[]; reqs={r["id"]:r for r in obj["requirements"]}; tasks={t["id"]:t for t in obj["tasks"]}
        current_tasks=[t for t in obj["tasks"] if t["state"]!="CANCELLED" and not (t["state"]=="COMPLETED" and not any(rid in reqs for rid in t["requirements"]))]
        if not reqs: diagnostics.append({"code":"NO_ACTIVE_REQUIREMENTS","message":"At least one active requirement is required."})
        for r in obj["requirements"]:
            if not r["sources"]: diagnostics.append({"code":"REQUIREMENT_SOURCE_MISSING","requirement_id":r["id"]})
            for src in r["sources"]:
                spec=next((s for s in obj["specifications"] if s["id"]==src["specification_id"]),None)
                if not spec or spec["lifecycle"]!="ACTIVE" or not spec["content_hash"]: diagnostics.append({"code":"REQUIREMENT_SOURCE_INACTIVE","requirement_id":r["id"],"specification_id":src["specification_id"]})
            covering=[t for t in obj["tasks"] if t["state"]!="CANCELLED" and r["id"] in t["requirements"]]
            if r["implementation_required"] and not covering: diagnostics.append({"code":"TASK_COVERAGE_MISSING","requirement_id":r["id"]})
            if not r["implementation_required"] and covering: diagnostics.append({"code":"DIRECT_REQUIREMENT_HAS_TASK","requirement_id":r["id"]})
        for t in current_tasks:
            active_links=[rid for rid in t["requirements"] if rid in reqs]
            if not t["objective"]: diagnostics.append({"code":"TASK_OBJECTIVE_MISSING","task_id":t["id"]})
            if not t["implementation_scope"]: diagnostics.append({"code":"TASK_SCOPE_MISSING","task_id":t["id"]})
            if not t["criteria"] or any(not x["criterion_text"] for x in t["criteria"]):diagnostics.append({"code":"TASK_CRITERIA_MISSING","task_id":t["id"]})
            if not active_links:diagnostics.append({"code":"TASK_REQUIREMENT_LINK_MISSING","task_id":t["id"]})
            for rid in t["requirements"]:
                if rid not in reqs and t["state"]!="COMPLETED":diagnostics.append({"code":"TASK_LINK_INVALID","task_id":t["id"],"requirement_id":rid})
            for dep in t["dependencies"]:
                if dep not in tasks or tasks[dep]["state"]=="CANCELLED":diagnostics.append({"code":"DEPENDENCY_INVALID","task_id":t["id"],"depends_on_task_id":dep})
        # Kahn detects all cycle participants deterministically.
        current_task_ids={t["id"] for t in current_tasks}
        graph={t["id"]:[d for d in t["dependencies"] if d in current_task_ids] for t in current_tasks}; remaining={k:set(v) for k,v in graph.items()}; ready=sorted(k for k,v in remaining.items() if not v)
        while ready:
            node=ready.pop(0)
            for key in sorted(remaining):
                if node in remaining[key]:
                    remaining[key].remove(node)
                    if not remaining[key]: ready.append(key);ready.sort()
        for node,deps in remaining.items():
            if deps: diagnostics.append({"code":"DEPENDENCY_CYCLE","task_id":node})
        if self.con.execute("SELECT 1 FROM specification_reviews WHERE project_id=? AND state='PENDING'",(project["id"],)).fetchone(): diagnostics.append({"code":"SPECIFICATION_REVIEW_REQUIRED"})
        fingerprint=sha256(canonical_bytes(self.fingerprint_object(obj))); snapshot=canonical(obj["specifications"])
        with transaction(self.con):
            self.con.execute("INSERT INTO plan_validations VALUES(?,?,?,?,?,?,?,?)",(new_id(),project["id"],fingerprint,int(not diagnostics),canonical(diagnostics),snapshot,principal["id"],now()))
            self.audit(project["id"],principal["id"],"PLAN_VALIDATED","PROJECT",project["id"],{"succeeded":not diagnostics,"fingerprint":fingerprint})
        return {"valid":not diagnostics,"fingerprint":fingerprint,"diagnostics":diagnostics}

    def plan_current(self, project_id: str) -> tuple[bool,str|None,list[Any]]:
        row=self.con.execute("SELECT * FROM plan_validations WHERE project_id=? AND succeeded=1 ORDER BY created_at DESC,id DESC LIMIT 1",(project_id,)).fetchone()
        if not row:return False,None,[]
        current=sha256(canonical_bytes(self.fingerprint_object(self.plan_object(project_id))))
        pending=self.con.execute("SELECT 1 FROM specification_reviews WHERE project_id=? AND state='PENDING'",(project_id,)).fetchone()
        return bool(row["fingerprint"]==current and not pending),current,json.loads(row["diagnostics_json"])

    @staticmethod
    def fingerprint_object(plan):
        """Only definition data participates; lifecycle/assignment progress never invalidates a plan."""
        return {"requirements": plan["requirements"], "tasks": [{k:v for k,v in task.items() if k != "state"} for task in plan["tasks"]], "specifications": plan["specifications"]}

    # ---- blockers, assignment, workers ----
    def system_blocker(self, project_id, category, scope_type, scope_id, description):
        row=self.con.execute("SELECT id FROM blockers WHERE project_id=? AND category=? AND scope_type=? AND scope_id IS ? AND description=? AND state='OPEN'",(project_id,category,scope_type,scope_id,description)).fetchone()
        if row:return row["id"]
        bid=new_id();self.con.execute("INSERT INTO blockers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,project_id,category,scope_type,scope_id,description,"OPEN",None,1,now(),None,None,None));return bid

    def blocker_create(self, project, principal, data, worker=False):
        require_object(data,{"category","scope_type","scope_id","description"},{"category","scope_type","description"})
        if worker: self.preflight(project)
        category=text(data["category"],"category"); scope=text(data["scope_type"],"scope_type"); scope_id=data.get("scope_id")
        if category not in CATEGORIES: raise LedgerError("INVALID_REQUEST","Unsupported blocker category.")
        if worker:
            assignment=self.con.execute("SELECT * FROM assignments WHERE id=?",(principal["assignment_id"],)).fetchone()
            if scope not in {"TASK","ASSIGNMENT"}: raise LedgerError("WORKER_SCOPE_VIOLATION","Worker may only block its own task or assignment.")
            expected=assignment["task_id"] if scope=="TASK" else assignment["id"]
            if scope_id is not None and scope_id!=expected: raise LedgerError("WORKER_SCOPE_VIOLATION","Worker blocker scope does not match assignment.")
            scope_id=expected
        elif scope not in {"PROJECT","SPECIFICATION","REQUIREMENT","TASK","ASSIGNMENT","SUBMISSION","INTEGRATION","OPERATION"}: raise LedgerError("INVALID_REQUEST","Unsupported blocker scope.")
        if scope!="PROJECT" and not isinstance(scope_id,str): raise LedgerError("INVALID_REQUEST","A non-project blocker requires scope_id.")
        bid=new_id()
        with transaction(self.con):
            self.con.execute("INSERT INTO blockers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(bid,project["id"],category,scope,scope_id,text(data["description"],"description"),"OPEN",principal["id"],0,now(),None,None,None))
            self.invalidate_completion(project["id"],"blocker created");self.audit(project["id"],principal["id"],"BLOCKER_CREATED","BLOCKER",bid)
        return {"blocker_id":bid,"state":"OPEN"}

    def resolve_blocker(self,project,principal,data):
        require_object(data,{"blocker_id","resolution"},{"blocker_id","resolution"})
        b=self.con.execute("SELECT * FROM blockers WHERE id=? AND project_id=?",(data["blocker_id"],project["id"])).fetchone()
        if not b:raise LedgerError("BLOCKER_OPEN","Blocker was not found.")
        resolution=text(data["resolution"],"resolution")
        if b["state"]=="RESOLVED":
            if b["resolution"]!=resolution: raise LedgerError("STALE_STATE","Blocker already has a different resolution.")
            return {"blocker_id":b["id"],"state":"RESOLVED"}
        with transaction(self.con):
            self.con.execute("UPDATE blockers SET state='RESOLVED',resolution=?,resolved_by_principal_id=?,resolved_at=? WHERE id=?",(resolution,principal["id"],now(),b["id"]))
            self.audit(project["id"],principal["id"],"BLOCKER_RESOLVED","BLOCKER",b["id"])
        return {"blocker_id":b["id"],"state":"RESOLVED"}

    def blocking_reasons(self, project_id, operation, task_id=None, assignment_id=None, submission_id=None, requirement_id=None):
        blocked=[]
        links=set(self.current_links(task_id)) if task_id else set()
        task_ids={task_id} if task_id else set()
        if requirement_id: task_ids.update(t["id"] for t in self.current_tasks_for_requirement(requirement_id))
        for b in self.con.execute("SELECT * FROM blockers WHERE project_id=? AND state='OPEN' ORDER BY id",(project_id,)):
            applies=b["scope_type"]=="PROJECT" or (b["scope_type"]=="REQUIREMENT" and b["scope_id"] in links|({requirement_id} if requirement_id else set())) or (b["scope_type"]=="TASK" and b["scope_id"] in task_ids) or (b["scope_type"]=="ASSIGNMENT" and b["scope_id"]==assignment_id) or (b["scope_type"]=="SUBMISSION" and b["scope_id"]==submission_id)
            if operation=="complete": applies=True
            if applies: blocked.append({"blocker_id":b["id"],"scope_type":b["scope_type"],"description":b["description"]})
        return blocked

    def task_eligible(self, project, task):
        valid,_,_=self.plan_current(project["id"])
        if not valid:return False,"PLAN_INVALID"
        if task["state"]!="PLANNED":return False,"TASK_STATE_INVALID"
        if git.oid_or_none(project["repository_root"],f"refs/heads/{project['canonical_branch']}") is None:return False,"INITIAL_COMMIT_REQUIRED"
        if self.ensure_operation_exists(project["id"]):return False,"RECOVERY_REQUIRED"
        if self.blocking_reasons(project["id"],"assign",task_id=task["id"]):return False,"TASK_BLOCKED"
        if self.con.execute("SELECT 1 FROM assignments WHERE task_id=? AND state IN ('PREPARING','ACTIVE')",(task["id"],)).fetchone():return False,"ASSIGNMENT_ALREADY_ACTIVE"
        for dep in self.con.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? AND task_revision=?",(task["id"],task["current_revision"])):
            d=self.con.execute("SELECT * FROM tasks WHERE id=?",(dep[0],)).fetchone()
            if not d or d["state"]!="COMPLETED" or not self.current_integration(project,d["id"]): return False,"DEPENDENCIES_UNSATISFIED"
        return True,None

    def ensure_operation_exists(self, project_id): return self.con.execute("SELECT 1 FROM operations WHERE project_id=? AND state IN ('STARTED','UNCERTAIN')",(project_id,)).fetchone() is not None

    def context(self, assignment, task_revision=None):
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(assignment["task_id"],)).fetchone(); revision=task_revision or assignment["task_revision"]
        rev=self.con.execute("SELECT * FROM task_revisions WHERE task_id=? AND revision=?",(task["id"],revision)).fetchone()
        criteria=[r["criterion_text"] for r in self.con.execute("SELECT criterion_text FROM task_acceptance_criteria WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],revision))]
        checks=[r["command"] for r in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],revision))]
        requirements=[]
        for rid in self.con.execute("SELECT requirement_id FROM task_requirement_links WHERE task_id=? AND task_revision=? ORDER BY requirement_id",(task["id"],revision)):
            r=self.con.execute("SELECT r.*,v.statement,v.details FROM requirements r JOIN requirement_revisions v ON v.requirement_id=r.id AND v.revision=r.current_revision WHERE r.id=?",(rid[0],)).fetchone()
            sources=[dict(x) for x in self.con.execute("SELECT specification_id,locator,excerpt FROM requirement_source_refs WHERE requirement_id=? AND requirement_revision=? ORDER BY specification_id,locator",(r["id"],r["current_revision"]))]
            requirements.append({"id":r["id"],"revision":r["current_revision"],"statement":r["statement"],"details":r["details"],"sources":sources})
        registered_specifications=[
            {"id":row["id"],"relative_path":row["relative_path"]}
            for row in self.con.execute(
                "SELECT id,relative_path FROM specifications WHERE project_id=? AND lifecycle='ACTIVE' ORDER BY relative_path,id",
                (assignment["project_id"],),
            )
        ]
        questions=[dict(x) for x in self.con.execute("SELECT id,body,is_blocking,state,answer FROM worker_questions WHERE assignment_id=? ORDER BY asked_at,id",(assignment["id"],))]
        return canonical({"assignment":{"id":assignment["id"],"attempt_number":assignment["attempt_number"],"worker_profile":assignment["worker_profile"],"execution_mode":assignment["execution_mode"],"checkpoint_plan":json.loads(assignment["checkpoint_plan_json"]),"base_commit_oid":assignment["base_commit_oid"],"branch_name":assignment["branch_name"],"worktree_path":assignment["worktree_path"]},"task":{"id":task["id"],"revision":revision,"objective":rev["objective"],"implementation_scope":rev["implementation_scope"],"acceptance_criteria":criteria,"required_checks":checks},"requirements":requirements,"registered_specifications":registered_specifications,"questions":questions})

    def assignment_create(self,project,principal,data):
        require_object(data,{"task_id","worker_profile","execution_mode","checkpoints"},{"task_id","worker_profile"})
        worker_profile=data["worker_profile"]
        if worker_profile not in WORKER_PROFILES:
            raise LedgerError("INVALID_REQUEST","worker_profile must be routine or complex.")
        execution_mode=data.get("execution_mode","isolated")
        if execution_mode not in {"isolated","lightweight"}:
            raise LedgerError("INVALID_REQUEST","execution_mode must be isolated or lightweight.")
        checkpoint_plan=array(data.get("checkpoints",[]),"checkpoints")
        normalized_checkpoints=[]
        for position,item in enumerate(checkpoint_plan,1):
            require_object(item,{"label","criteria"},{"label","criteria"})
            criteria=array(item["criteria"],"checkpoint criteria")
            if not criteria:raise LedgerError("INVALID_REQUEST","Each checkpoint needs at least one criterion.")
            normalized_checkpoints.append({"position":position,"label":text(item["label"],"checkpoint label"),"criteria":[text(x,"checkpoint criterion") for x in criteria]})
        if execution_mode=="lightweight" and not normalized_checkpoints:
            raise LedgerError("INVALID_REQUEST","Lightweight execution requires at least one durable checkpoint.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=? AND project_id=?",(data["task_id"],project["id"])).fetchone()
        if not task:raise LedgerError("TASK_NOT_FOUND","Task was not found.")
        if worker_profile=="routine":
            rejected=self.con.execute("SELECT COUNT(*) FROM submission_verifications sv JOIN submissions s ON s.id=sv.submission_id JOIN assignments a ON a.id=s.assignment_id WHERE a.task_id=? AND a.worker_profile='routine' AND sv.outcome='REJECTED'",(task["id"],)).fetchone()[0]
            if rejected>=2:raise LedgerError("WORKER_PROFILE_ESCALATION_REQUIRED","This task has reached the routine-worker rejection limit and requires a complex assignment.",details={"task_id":task["id"],"routine_rejections":rejected,"required_worker_profile":"complex"})
        self.preflight(project); allowed,why=self.task_eligible(project,task)
        if not allowed:
            if why == "INITIAL_COMMIT_REQUIRED":
                raise LedgerError(
                    why,
                    "This repository has no commits yet. Create the first commit on the canonical branch before starting implementation.",
                    details={"canonical_branch": project["canonical_branch"]},
                )
            raise LedgerError(why,"Task is not eligible for assignment.",details={"task_id":task["id"]})
        attempt=self.con.execute("SELECT COALESCE(MAX(attempt_number),0)+1 FROM assignments WHERE task_id=?",(task["id"],)).fetchone()[0]
        aid=new_id();branch=f"taskledger/p-{project['id']}/t-{task['id']}/a-{attempt}";worktree=self.home/"projects"/project["id"] / "worktrees" / aid;base=git.oid_or_none(project["repository_root"],f"refs/heads/{project['canonical_branch']}")
        if base is None:
            raise LedgerError(
                "INITIAL_COMMIT_REQUIRED",
                "This repository has no commits yet. Create the first commit on the canonical branch before starting implementation.",
                details={"canonical_branch": project["canonical_branch"]},
            )
        if worktree.exists() or git.run(project["repository_root"],["show-ref","--verify","--quiet",f"refs/heads/{branch}"],check=False).returncode==0: raise LedgerError("STALE_STATE","Generated assignment branch or worktree already exists.")
        stamp=now()
        with transaction(self.con):
            self.ensure_no_operation(project["id"])
            self.con.execute("INSERT INTO assignments(id,project_id,task_id,task_revision,attempt_number,worker_profile,execution_mode,checkpoint_plan_json,state,base_commit_oid,branch_name,worktree_path,context_json,created_at,activated_at,closed_at,revoked_at,revocation_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(aid,project["id"],task["id"],task["current_revision"],attempt,worker_profile,execution_mode,canonical(normalized_checkpoints),"PREPARING",base,branch,str(worktree),"{}",stamp,None,None,None,None))
            op=new_id();self.con.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)",(op,project["id"],"ASSIGNMENT_PREPARATION","ASSIGNMENT",aid,"STARTED",canonical({"base":base,"branch":branch,"worktree":str(worktree)}),None,stamp,None))
        try: git.run(project["repository_root"],["worktree","add","-b",branch,str(worktree),base])
        except LedgerError:
            with transaction(self.con):self.con.execute("UPDATE assignments SET state='UNCERTAIN' WHERE id=?",(aid,));self.con.execute("UPDATE operations SET state='UNCERTAIN',finished_at=? WHERE id=?",(now(),op));self.system_blocker(project["id"],"INTERRUPTED_OPERATION","OPERATION",op,"Assignment preparation outcome cannot be proven")
            raise LedgerError("RECOVERY_REQUIRED","Assignment preparation needs recovery.")
        if git.oid(project["repository_root"],f"refs/heads/{branch}")!=base or git.current_branch(worktree)!=branch or git.operation_in_progress(worktree): raise LedgerError("RECOVERY_REQUIRED","Assignment activation proof failed.")
        raw=token();path=self.home/"projects"/project["id"] / "assignments" / aid / "worker-token"; worker=new_id()
        assignment=self.con.execute("SELECT * FROM assignments WHERE id=?",(aid,)).fetchone();ctx=self.context(assignment)
        with transaction(self.con):
            self.con.execute("UPDATE assignments SET state='ACTIVE',activated_at=?,context_json=? WHERE id=?",(now(),ctx,aid));self.con.execute("UPDATE tasks SET state='ASSIGNED',updated_at=? WHERE id=?",(now(),task["id"]));self.con.execute("INSERT INTO principals VALUES(?,?,?,?,?,?,?)",(worker,project["id"],"WORKER",aid,1,now(),None));self.con.execute("INSERT INTO principal_credentials VALUES(?,?,?,?)",(worker,sha256(raw),now(),None));self.con.execute("UPDATE operations SET state='SUCCEEDED',finished_at=? WHERE id=?",(now(),op));self.audit(project["id"],principal["id"],"ASSIGNMENT_ACTIVATED","ASSIGNMENT",aid,{"worker_profile":worker_profile,"execution_mode":execution_mode,"checkpoint_count":len(normalized_checkpoints)})
        atomic_secret(path,raw)
        return {"assignment_id":aid,"task_id":task["id"],"attempt_number":attempt,"worker_profile":worker_profile,"execution_mode":execution_mode,"checkpoint_count":len(normalized_checkpoints),"worktree_path":str(worktree),"branch_name":branch,"base_commit_oid":base,"worker_token_path":str(path),"context_hash":sha256(ctx)}

    def revoke_assignment(self,project,principal,assignment,reason):
        if assignment["state"]=="CLOSED":raise LedgerError("TASK_STATE_INVALID","Closed assignments cannot be revoked.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(assignment["task_id"],)).fetchone()
        if task["state"] in {"ACCEPTED","COMPLETED"}:raise LedgerError("TASK_STATE_INVALID","Accepted or completed work cannot be revoked.")
        self.con.execute("UPDATE assignments SET state='REVOKED',revoked_at=?,revocation_reason=? WHERE id=?",(now(),reason,assignment["id"]));self.con.execute("UPDATE principals SET active=0,deactivated_at=? WHERE assignment_id=?",(now(),assignment["id"]))
        if task["state"]=="ASSIGNED": self.con.execute("UPDATE tasks SET state='PLANNED',updated_at=? WHERE id=?",(now(),task["id"]))

    def assignment_revoke(self,project,principal,data):
        require_object(data,{"assignment_id","reason"},{"assignment_id","reason"});a=self.con.execute("SELECT * FROM assignments WHERE id=? AND project_id=?",(data["assignment_id"],project["id"])).fetchone()
        if not a:raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Assignment not found.")
        with transaction(self.con):self.revoke_assignment(project,principal,a,text(data["reason"],"reason"));self.audit(project["id"],principal["id"],"ASSIGNMENT_REVOKED","ASSIGNMENT",a["id"])
        return {"assignment_id":a["id"],"state":"REVOKED"}

    def rotate_token(self,project,principal,data):
        require_object(data,{"assignment_id"},{"assignment_id"});a=self.con.execute("SELECT * FROM assignments WHERE id=? AND project_id=?",(data["assignment_id"],project["id"])).fetchone()
        if not a:raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Assignment not found.")
        if a["state"] != "ACTIVE": raise LedgerError("ASSIGNMENT_NOT_ACTIVE", "Worker token rotation is only available for an active assignment.")
        worker=self.con.execute("SELECT * FROM principals WHERE assignment_id=?",(a["id"],)).fetchone()
        raw=token(); path=self.home/"projects"/project["id"] / "assignments" / a["id"] / "worker-token"
        with transaction(self.con):self.con.execute("UPDATE principals SET active=1,deactivated_at=NULL WHERE id=?",(worker["id"],));self.con.execute("UPDATE principal_credentials SET token_hash=?,rotated_at=? WHERE principal_id=?",(sha256(raw),now(),worker["id"]));self.audit(project["id"],principal["id"],"WORKER_TOKEN_ROTATED","ASSIGNMENT",a["id"])
        atomic_secret(path,raw);return {"assignment_id":a["id"],"worker_token_path":str(path)}

    def worker_assignment(self, principal):
        a=self.con.execute("SELECT * FROM assignments WHERE id=?",(principal["assignment_id"],)).fetchone()
        if not a:raise LedgerError("AUTHORIZATION_DENIED","Worker assignment no longer exists.")
        p=self.con.execute("SELECT * FROM projects WHERE id=?",(a["project_id"],)).fetchone();return p,a

    def correction_packet(self, project, assignment) -> dict[str, Any] | None:
        latest=self.con.execute(
            "SELECT * FROM submissions WHERE assignment_id=? ORDER BY sequence DESC LIMIT 1",
            (assignment["id"],),
        ).fetchone()
        if not latest or latest["state"] not in {"REJECTED","BLOCKED"}:
            return None
        verification=self.con.execute(
            "SELECT * FROM submission_verifications WHERE submission_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (latest["id"],),
        ).fetchone()
        if not verification:
            return None
        results=json.loads(verification["criterion_results_json"])
        failed=[result for result in results if not result["satisfied"]]
        blockers=[dict(x) for x in self.con.execute(
            "SELECT id,category,scope_type,scope_id,description FROM blockers WHERE project_id=? AND state='OPEN' "
            "AND (scope_type='PROJECT' OR (scope_type='TASK' AND scope_id=?) OR "
            "(scope_type='ASSIGNMENT' AND scope_id=?) OR (scope_type='SUBMISSION' AND scope_id=?)) ORDER BY id",
            (project["id"],assignment["task_id"],assignment["id"],latest["id"]),
        )]
        body={"revision":verification["id"],"reviewed_submission_id":latest["id"],
              "reviewed_commit_oid":latest["head_commit_oid"],"outcome":verification["outcome"],
              "failed_criteria":failed,"corrections":verification["corrections"],
              "notes":verification["notes"],"relevant_blockers":blockers,
              "created_at":verification["created_at"]}
        body["packet_hash"]=sha256(canonical(body))
        return body

    def checkpoint_progress(self, assignment) -> dict[str,Any]:
        plan=json.loads(assignment["checkpoint_plan_json"])
        rows=[dict(x) for x in self.con.execute(
            "SELECT id,sequence,state,label,head_commit_oid,summary,criterion_results_json,corrections,notes,submitted_at FROM assignment_checkpoints WHERE assignment_id=? ORDER BY sequence,submitted_at,id",
            (assignment["id"],),
        )]
        for row in rows:
            if row["criterion_results_json"]:row["criterion_results"]=json.loads(row.pop("criterion_results_json"))
            else:row.pop("criterion_results_json");row["criterion_results"]=None
        latest_by_sequence={}
        for row in rows:latest_by_sequence[row["sequence"]]=row
        approved=[]
        pending=None
        for step in plan:
            row=latest_by_sequence.get(step["position"])
            if row and row["state"]=="APPROVED":approved.append(row)
            elif row and row["state"]=="PENDING":pending=row;break
            else:break
        next_position=len(approved)+1
        next_step=plan[next_position-1] if next_position<=len(plan) else None
        latest_correction=None
        if rows and rows[-1]["state"]=="REJECTED":
            latest_correction={"checkpoint_id":rows[-1]["id"],"reviewed_commit_oid":rows[-1]["head_commit_oid"],
                               "sequence":rows[-1]["sequence"],"failed_criteria":[x for x in rows[-1]["criterion_results"] or [] if not x["satisfied"]],
                               "corrections":rows[-1]["corrections"],"notes":rows[-1]["notes"]}
            latest_correction["packet_hash"]=sha256(canonical(latest_correction))
        return {"mode":assignment["execution_mode"],"approved_count":len(approved),"total":len(plan),
                "pending":pending,"next":next_step if pending is None else None,
                "final_submission_unlocked":pending is None and len(approved)==len(plan),
                "latest_correction":latest_correction,
                "history":rows[-20:],"history_truncated":len(rows)>20}

    def worker_context(self, principal, data=None):
        project,a=self.worker_assignment(principal)
        self.preflight(project)
        data=data or {};require_object(data,{"if_none_match","if_dynamic_none_match"})
        context_json=a["context_json"] or self.context(a); context_hash=sha256(context_json)
        dynamic={"questions":[dict(x) for x in self.con.execute("SELECT id,body,is_blocking,state,answer FROM worker_questions WHERE assignment_id=? ORDER BY asked_at,id",(a["id"],))],"open_blockers":[dict(x) for x in self.con.execute("SELECT id,category,scope_type,scope_id,description FROM blockers WHERE project_id=? AND state='OPEN' AND (scope_type='PROJECT' OR (scope_type='ASSIGNMENT' AND scope_id=?) OR (scope_type='TASK' AND scope_id=?)) ORDER BY id",(project["id"],a["id"],a["task_id"]))],"submissions":[dict(x) for x in self.con.execute("SELECT id,state,head_commit_oid,submitted_at FROM submissions WHERE assignment_id=? ORDER BY sequence",(a["id"],))],"correction_packet":self.correction_packet(project,a),"checkpoint_progress":self.checkpoint_progress(a)}
        dynamic_hash=sha256(canonical(dynamic)); result={"assignment_id":a["id"],"context_hash":context_hash,"dynamic_hash":dynamic_hash}
        if data.get("if_none_match") != context_hash:result["context"]=json.loads(context_json)
        else:result["context_not_modified"]=True
        if data.get("if_dynamic_none_match") != dynamic_hash:result["dynamic"]=dynamic
        else:result["dynamic_not_modified"]=True
        return result

    def submission_review_context(self,project,submission_id):
        sub=self.con.execute("SELECT s.*,a.task_id,a.task_revision,a.worker_profile,a.base_commit_oid,a.worktree_path FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.id=? AND s.project_id=?",(submission_id,project["id"])).fetchone()
        if not sub:raise LedgerError("SUBMISSION_NOT_FOUND","Submission was not found.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(sub["task_id"],)).fetchone()
        criteria=[dict(x) for x in self.con.execute("SELECT id,position,criterion_text FROM task_acceptance_criteria WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],task["current_revision"]))]
        required_checks=[x[0] for x in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],task["current_revision"]))]
        requirements=[]
        for row in self.con.execute("SELECT r.id,r.current_revision,v.statement,v.details FROM requirements r JOIN task_requirement_links l ON l.requirement_id=r.id AND l.task_id=? AND l.task_revision=? JOIN requirement_revisions v ON v.requirement_id=r.id AND v.revision=r.current_revision ORDER BY r.id",(task["id"],task["current_revision"])):requirements.append(dict(row))
        blockers=[dict(x) for x in self.con.execute("SELECT id,category,scope_type,scope_id,description FROM blockers WHERE project_id=? AND state='OPEN' AND (scope_type='PROJECT' OR (scope_type='TASK' AND scope_id=?) OR (scope_type='ASSIGNMENT' AND scope_id=?) OR (scope_type='SUBMISSION' AND scope_id=?)) ORDER BY id",(project["id"],task["id"],sub["assignment_id"],sub["id"]))]
        head=sub["head_commit_oid"];base=sub["base_commit_oid"];repo=project["repository_root"]
        exists=git.run(repo,["cat-file","-e",f"{head}^{{commit}}"],check=False).returncode==0
        raw_diffstat=git.run(repo,["diff","--stat","--no-renames",base,head]).stdout if exists else None
        diffstat=raw_diffstat[:16384] if raw_diffstat is not None else None
        prior=[dict(x) for x in self.con.execute("SELECT id,outcome,notes,created_at FROM submission_verifications WHERE submission_id=? ORDER BY created_at,id",(sub["id"],))]
        receipts=[]
        for receipt in self.con.execute("SELECT * FROM execution_receipts WHERE assignment_id=? AND (submission_id IS NULL OR submission_id=?) ORDER BY started_at,id",(sub["assignment_id"],sub["id"])):
            item=dict(receipt);item["telemetry"]=json.loads(item.pop("telemetry_json"));item["currently_stale"]=bool(item["stale"]) or item["source_revision"]!=head
            receipts.append(item)
        changed=json.loads(sub["changed_files_json"]);page=changed[:500]
        return {"submission":{"id":sub["id"],"state":sub["state"],"assignment_id":sub["assignment_id"],"worker_profile":sub["worker_profile"],"task_id":task["id"],"assigned_task_revision":sub["task_revision"],"current_task_revision":task["current_revision"],"definition_stale":sub["task_revision"]!=task["current_revision"],"base_commit_oid":base,"head_commit_oid":head,"head_object_exists":exists,"canonical_head_oid":git.oid_or_none(repo,f"refs/heads/{project['canonical_branch']}")},"diff":{"diffstat":diffstat,"diffstat_truncated":raw_diffstat is not None and len(raw_diffstat)>len(diffstat),"changed_files":page,"changed_files_total":len(changed),"changed_files_omitted":len(changed)-len(page),"complete":len(page)==len(changed),"patch_included":False},"criteria":criteria,"required_checks":required_checks,"requirements":requirements,"worker_claims":{"summary":sub["summary"],"evidence":json.loads(sub["evidence_json"]),"risks":json.loads(sub["risks_json"]),"unresolved_questions":json.loads(sub["unresolved_questions_json"]),"follow_up_work":json.loads(sub["follow_up_work_json"])},"execution_receipts":receipts,"receipt_classes":{"worker":"supporting claim only","reviewer":"independent observed execution"},"open_blockers":blockers,"prior_verifications":prior,"review_required":{"inspect_exact_diff":True,"run_independent_checks":True,"worker_evidence_is_claim_only":True,"worker_receipts_do_not_satisfy_reviewer_obligations":True,"accept_from_incomplete_packet":False}}

    def worker_question(self,principal,data):
        require_object(data,{"body","blocking"},{"body","blocking"}); project,a=self.worker_assignment(principal)
        self.preflight(project)
        if not isinstance(data["blocking"],bool):raise LedgerError("INVALID_REQUEST","blocking must be boolean.")
        qid=new_id()
        with transaction(self.con):
            bid=None
            if data["blocking"]:bid=self.system_blocker(project["id"],"MISSING_PRODUCT_DECISION","ASSIGNMENT",a["id"],f"Blocking worker question: {text(data['body'],'body')}")
            self.con.execute("INSERT INTO worker_questions VALUES(?,?,?,?,?,?,?,?,?,?)",(qid,a["id"],text(data["body"],"body"),int(data["blocking"]),"OPEN",bid,None,now(),None,None));self.audit(project["id"],principal["id"],"WORKER_QUESTION_CREATED","QUESTION",qid)
        return {"question_id":qid,"blocker_id":bid}

    def answer_question(self,project,principal,data):
        require_object(data,{"question_id","answer","resolve_blocker","resolution"},{"question_id","answer"});q=self.con.execute("SELECT q.*,a.project_id FROM worker_questions q JOIN assignments a ON a.id=q.assignment_id WHERE q.id=? AND a.project_id=?",(data["question_id"],project["id"])).fetchone()
        if not q:raise LedgerError("INVALID_REQUEST","Question was not found.")
        with transaction(self.con):
            self.con.execute("UPDATE worker_questions SET state='ANSWERED',answer=?,answered_by_principal_id=?,answered_at=? WHERE id=?",(text(data["answer"],"answer"),principal["id"],now(),q["id"]))
            if data.get("resolve_blocker"):
                if not q["blocker_id"] or not data.get("resolution"):raise LedgerError("INVALID_REQUEST","A blocker resolution is required.")
                blocker=self.con.execute("SELECT * FROM blockers WHERE id=? AND project_id=?",(q["blocker_id"],project["id"])).fetchone()
                if not blocker or blocker["state"] != "OPEN": raise LedgerError("BLOCKER_OPEN","Question blocker is not open.")
                self.con.execute("UPDATE blockers SET state='RESOLVED',resolution=?,resolved_by_principal_id=?,resolved_at=? WHERE id=?",(text(data["resolution"],"resolution"),principal["id"],now(),blocker["id"]))
            self.audit(project["id"],principal["id"],"QUESTION_ANSWERED","QUESTION",q["id"])
        return {"question_id":q["id"],"state":"ANSWERED"}

    def worker_followup(self,principal,data):
        require_object(data,{"body"},{"body"});project,a=self.worker_assignment(principal);fid=new_id()
        self.preflight(project)
        with transaction(self.con):self.con.execute("INSERT INTO follow_up_proposals VALUES(?,?,?,?,?,?,?,?)",(fid,a["id"],None,text(data["body"],"body"),"PROPOSED",now(),None,None));self.audit(project["id"],principal["id"],"FOLLOW_UP_PROPOSED","FOLLOW_UP",fid)
        return {"follow_up_id":fid}

    def _commit_worker_checkpoint(self, project, assignment, task, message: str) -> tuple[str, list[dict[str,str]]]:
        pre=git.oid(assignment["worktree_path"]);op=new_id()
        with transaction(self.con):
            self.con.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)",(op,project["id"],"SUBMISSION_CHECKPOINT","ASSIGNMENT",assignment["id"],"STARTED",canonical({"head":pre,"worktree":assignment["worktree_path"]}),None,now(),None))
        try:
            if git.current_branch(assignment["worktree_path"])!=assignment["branch_name"] or git.operation_in_progress(assignment["worktree_path"]):
                raise LedgerError("GIT_COMMAND_FAILED","Assignment worktree is not in a safe state.")
            git.run(assignment["worktree_path"],["add","-A"])
            if git.run(assignment["worktree_path"],["diff","--cached","--quiet"],check=False).returncode:
                git.run(assignment["worktree_path"],["-c","commit.gpgSign=false","commit","-m",message])
            if not git.clean(assignment["worktree_path"]):raise LedgerError("GIT_COMMAND_FAILED","Worktree remains dirty after checkpoint.")
            head=git.oid(assignment["worktree_path"])
            if not git.ancestor(assignment["worktree_path"],assignment["base_commit_oid"],head) or head==assignment["base_commit_oid"]:
                raise LedgerError("GIT_COMMAND_FAILED","Checkpoint must contain a non-empty descendant diff.")
            raw=git.run(assignment["worktree_path"],["diff","--name-status","-z",assignment["base_commit_oid"],head]).stdout
            changed=[];parts=raw.split("\0");i=0
            while i<len(parts)-1:
                status=parts[i];i+=1
                if not status:continue
                path=parts[i];i+=1
                if status[:1] in {"R","C"} and i<len(parts):path=parts[i];i+=1
                changed.append({"status":status,"path":path})
        except LedgerError:
            with transaction(self.con):self.con.execute("UPDATE operations SET state='FAILED',finished_at=? WHERE id=?",(now(),op))
            raise
        with transaction(self.con):self.con.execute("UPDATE operations SET state='SUCCEEDED',finished_at=? WHERE id=?",(now(),op))
        return head,changed

    def worker_checkpoint(self, principal, data):
        require_object(data,{"summary","evidence"},{"summary","evidence"})
        project,a=self.worker_assignment(principal);self.preflight(project)
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(a["task_id"],)).fetchone()
        if a["state"]!="ACTIVE" or task["state"]!="ASSIGNED":raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Worker assignment is not active.")
        if task["current_revision"]!=a["task_revision"]:raise LedgerError("TASK_REVISION_STALE","Assignment task revision is stale.")
        progress=self.checkpoint_progress(a)
        if progress["pending"]:raise LedgerError("CHECKPOINT_ALREADY_PENDING","A checkpoint is awaiting review.")
        step=progress["next"]
        if not step:raise LedgerError("CHECKPOINT_SEQUENCE_COMPLETE","All planned checkpoints are approved; final submission is available.")
        evidence=array(data["evidence"],"evidence")
        for item in evidence:
            require_object(item,{"label","details","receipt_id"},{"label","details"});text(item["label"],"evidence label");text(item["details"],"evidence details")
            if item.get("receipt_id") and not self.con.execute("SELECT 1 FROM execution_receipts WHERE id=? AND assignment_id=? AND execution_role='WORKER'",(item["receipt_id"],a["id"])).fetchone():raise LedgerError("EVIDENCE_RECEIPT_INVALID","Checkpoint receipt does not belong to this worker assignment.")
        head,changed=self._commit_worker_checkpoint(project,a,task,f"taskledger: checkpoint {step['position']} {task['id']}")
        prior=self.con.execute("SELECT * FROM assignment_checkpoints WHERE assignment_id=? AND sequence=? AND state='REJECTED' ORDER BY submitted_at DESC,id DESC LIMIT 1",(a["id"],step["position"])).fetchone()
        claim={"summary":data["summary"],"evidence":evidence,"step":step};payload=sha256(canonical_bytes(claim))
        existing=self.con.execute("SELECT * FROM assignment_checkpoints WHERE assignment_id=? AND sequence=? AND head_commit_oid=? AND payload_hash=?",(a["id"],step["position"],head,payload)).fetchone()
        if existing:return {"checkpoint_id":existing["id"],"state":existing["state"],"idempotent":True}
        checkpoint_id=new_id()
        with transaction(self.con):
            if prior:self.con.execute("UPDATE assignment_checkpoints SET state='SUPERSEDED' WHERE id=?",(prior["id"],))
            self.con.execute("INSERT INTO assignment_checkpoints(id,assignment_id,sequence,task_revision,label,criteria_json,state,summary,head_commit_oid,evidence_json,payload_hash,submitted_by_principal_id,submitted_at,resolved_at,reviewer_principal_id,criterion_results_json,corrections,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (checkpoint_id,a["id"],step["position"],a["task_revision"],step["label"],canonical(step["criteria"]),"PENDING",text(data["summary"],"summary"),head,canonical(evidence),payload,principal["id"],now(),None,None,None,None,None))
            self.audit(project["id"],principal["id"],"CHECKPOINT_RECORDED","CHECKPOINT",checkpoint_id,{"sequence":step["position"],"head_commit_oid":head})
        return {"checkpoint_id":checkpoint_id,"sequence":step["position"],"state":"PENDING","head_commit_oid":head,"changed_files":changed}

    def checkpoint_review_context(self, project, checkpoint_id):
        row=self.con.execute("SELECT c.*,a.task_id,a.base_commit_oid,a.worktree_path,a.task_revision assigned_revision FROM assignment_checkpoints c JOIN assignments a ON a.id=c.assignment_id WHERE c.id=? AND a.project_id=?",(checkpoint_id,project["id"])).fetchone()
        if not row:raise LedgerError("CHECKPOINT_NOT_FOUND","Checkpoint was not found.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(row["task_id"],)).fetchone()
        exists=git.run(project["repository_root"],["cat-file","-e",f"{row['head_commit_oid']}^{{commit}}"],check=False).returncode==0
        return {"checkpoint":{"id":row["id"],"assignment_id":row["assignment_id"],"sequence":row["sequence"],"state":row["state"],"label":row["label"],"criteria":json.loads(row["criteria_json"]),"base_commit_oid":row["base_commit_oid"],"head_commit_oid":row["head_commit_oid"],"head_object_exists":exists,"assigned_task_revision":row["assigned_revision"],"current_task_revision":task["current_revision"],"definition_stale":row["assigned_revision"]!=task["current_revision"]},"worker_claims":{"summary":row["summary"],"evidence":json.loads(row["evidence_json"])},"review_required":{"inspect_exact_commit":True,"independent_check":True,"approval_is_intermediate_only":True,"integration_permitted":False}}

    def verify_checkpoint(self, project, principal, data):
        require_object(data,{"checkpoint_id","outcome","criterion_results","corrections","notes"},{"checkpoint_id","outcome","criterion_results","corrections","notes"})
        row=self.con.execute("SELECT c.*,a.project_id,a.task_id,a.task_revision assigned_revision,a.state assignment_state FROM assignment_checkpoints c JOIN assignments a ON a.id=c.assignment_id WHERE c.id=? AND a.project_id=?",(data["checkpoint_id"],project["id"])).fetchone()
        if not row:raise LedgerError("CHECKPOINT_NOT_FOUND","Checkpoint was not found.")
        if row["state"]!="PENDING" or row["assignment_state"]!="ACTIVE":raise LedgerError("CHECKPOINT_STATE_INVALID","Checkpoint is not awaiting review.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(row["task_id"],)).fetchone()
        if task["current_revision"]!=row["assigned_revision"]:raise LedgerError("TASK_REVISION_STALE","Checkpoint cannot be approved against a changed task definition.")
        outcome=data["outcome"]
        if outcome not in {"APPROVED","REJECTED"}:raise LedgerError("INVALID_REQUEST","Checkpoint outcome must be APPROVED or REJECTED.")
        criteria=json.loads(row["criteria_json"]);results=array(data["criterion_results"],"criterion_results")
        seen=set()
        for result in results:
            require_object(result,{"position","satisfied","evidence"},{"position","satisfied","evidence"})
            position=result["position"]
            if not isinstance(position,int) or isinstance(position,bool) or position<1 or position>len(criteria) or position in seen or not isinstance(result["satisfied"],bool):raise LedgerError("VERIFICATION_CRITERIA_INCOMPLETE","Checkpoint criterion result is invalid.")
            seen.add(position);text(result["evidence"],"criterion evidence")
        if seen!=set(range(1,len(criteria)+1)):raise LedgerError("VERIFICATION_CRITERIA_INCOMPLETE","Every checkpoint criterion needs exactly one result.")
        satisfied=all(x["satisfied"] for x in results)
        if outcome=="APPROVED" and not satisfied:raise LedgerError("VERIFICATION_OUTCOME_INVALID","Approved checkpoint has unmet criteria.")
        if outcome=="REJECTED" and (satisfied or not data["corrections"]):raise LedgerError("VERIFICATION_OUTCOME_INVALID","Rejected checkpoint requires failed criteria and corrections.")
        with transaction(self.con):
            self.con.execute("UPDATE assignment_checkpoints SET state=?,corrections=?,reviewer_principal_id=?,criterion_results_json=?,resolved_at=?,notes=? WHERE id=?",(outcome,data["corrections"],principal["id"],canonical(results),now(),text(data["notes"],"notes"),row["id"]))
            self.audit(project["id"],principal["id"],"CHECKPOINT_REVIEWED","CHECKPOINT",row["id"],{"outcome":outcome,"sequence":row["sequence"],"head_commit_oid":row["head_commit_oid"]})
        return {"checkpoint_id":row["id"],"outcome":outcome,"assignment_id":row["assignment_id"],"next_action":"worker.checkpoint" if outcome=="REJECTED" else ("worker.submit" if self.checkpoint_progress(self.con.execute("SELECT * FROM assignments WHERE id=?",(row["assignment_id"],)).fetchone())["final_submission_unlocked"] else "worker.checkpoint")}

    def worker_submit(self,principal,data):
        require_object(data,{"summary","evidence","risks","unresolved_questions","follow_up_work"},{"summary","evidence","risks","unresolved_questions","follow_up_work"})
        project,a=self.worker_assignment(principal);task=self.con.execute("SELECT * FROM tasks WHERE id=?",(a["task_id"],)).fetchone()
        self.preflight(project)
        if a["state"]!="ACTIVE" or task["state"]!="ASSIGNED":raise LedgerError("ASSIGNMENT_NOT_ACTIVE","Worker assignment is not active.")
        progress=self.checkpoint_progress(a)
        if not progress["final_submission_unlocked"]:raise LedgerError("CHECKPOINT_APPROVAL_REQUIRED","Every planned checkpoint must be approved before final submission.",details={"checkpoint_progress":progress})
        if self.ensure_operation_exists(project["id"]):raise LedgerError("RECOVERY_REQUIRED","Submission is blocked while a Git operation is unresolved.")
        evidence=array(data["evidence"],"evidence")
        if not evidence:raise LedgerError("SUBMISSION_EVIDENCE_REQUIRED","At least one evidence item is required.")
        for item in evidence:
            require_object(item,{"label","details","command","exit_code","artifact_path","receipt_id","artifact_id"},{"label","details"});text(item["label"],"evidence label");text(item["details"],"evidence details")
            if "command" in item and item["command"] is not None:text(item["command"],"evidence command")
            if "exit_code" in item and item["exit_code"] is not None and (not isinstance(item["exit_code"],int) or isinstance(item["exit_code"],bool)):raise LedgerError("INVALID_REQUEST","evidence exit_code must be an integer or null.")
            if "artifact_path" in item and item["artifact_path"] is not None:text(item["artifact_path"],"evidence artifact_path")
            if "receipt_id" in item and item["receipt_id"] is not None:text(item["receipt_id"],"evidence receipt_id")
            if "artifact_id" in item and item["artifact_id"] is not None:
                text(item["artifact_id"],"evidence artifact_id")
                if not self.con.execute("SELECT 1 FROM evidence_artifacts WHERE id=? AND assignment_id=?",(item["artifact_id"],a["id"])).fetchone():raise LedgerError("EVIDENCE_ARTIFACT_INVALID","Artifact does not belong to this worker assignment.")
        required_checks=[x[0] for x in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],a["task_revision"]))]
        current_head=git.oid(a["worktree_path"]);current_fingerprint=self.tree_fingerprint(a["worktree_path"])
        successful_commands=set()
        for item in evidence:
            receipt_id=item.get("receipt_id")
            if not receipt_id:continue
            receipt=self.con.execute("SELECT * FROM execution_receipts WHERE id=? AND assignment_id=? AND execution_role='WORKER'",(receipt_id,a["id"])).fetchone()
            if not receipt:raise LedgerError("EVIDENCE_RECEIPT_INVALID","Receipt does not belong to this worker assignment.")
            if receipt["status"]=="SUCCEEDED" and receipt["exit_code"]==0 and not receipt["stale"] and receipt["source_revision"]==current_head and receipt["tree_fingerprint_after"]==current_fingerprint:
                successful_commands.add(receipt["command"])
        missing_checks=[command for command in required_checks if command not in successful_commands]
        if missing_checks:raise LedgerError("REQUIRED_CHECK_EVIDENCE_MISSING","Submission evidence is missing a current observed worker receipt for a required check.",details={"commands":missing_checks,"prose_claims_do_not_satisfy_checks":True})
        risks=array(data["risks"],"risks");questions=array(data["unresolved_questions"],"unresolved_questions");followups=array(data["follow_up_work"],"follow_up_work")
        for item,body in [(i,"description") for i in risks]+[(i,"body") for i in questions]:
            require_object(item,{body,"blocking","blocker_category"},{body,"blocking","blocker_category"});text(item[body],body)
            if not isinstance(item["blocking"],bool) or (item["blocking"] and item["blocker_category"] not in CATEGORIES) or (not item["blocking"] and item["blocker_category"] is not None):raise LedgerError("INVALID_REQUEST","Invalid blocking item category.")
        for item in followups:require_object(item,{"body"},{"body"});text(item["body"],"body")
        pre=git.oid(a["worktree_path"]);op=new_id()
        with transaction(self.con):self.con.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)",(op,project["id"],"SUBMISSION_CHECKPOINT","ASSIGNMENT",a["id"],"STARTED",canonical({"head":pre,"worktree":a["worktree_path"]}),None,now(),None))
        try:
            if git.current_branch(a["worktree_path"])!=a["branch_name"] or git.operation_in_progress(a["worktree_path"]):raise LedgerError("GIT_COMMAND_FAILED","Assignment worktree is not in a safe state.")
            git.run(a["worktree_path"],["add","-A"])
            if git.run(a["worktree_path"],["diff","--cached","--quiet"],check=False).returncode:
                git.run(a["worktree_path"],["-c","commit.gpgSign=false","commit","-m",f"taskledger: submit {task['id']} attempt {a['attempt_number']}"])
            if not git.clean(a["worktree_path"]):raise LedgerError("GIT_COMMAND_FAILED","Worktree remains dirty after checkpoint.")
            head=git.oid(a["worktree_path"])
            if not git.ancestor(a["worktree_path"],a["base_commit_oid"],head) or head==a["base_commit_oid"]:raise LedgerError("GIT_COMMAND_FAILED","Submission must contain a non-empty descendant diff.")
            raw=git.run(a["worktree_path"],["diff","--name-status","-z",a["base_commit_oid"],head]).stdout; changed=[]
            parts=raw.split("\0");i=0
            while i<len(parts)-1:
                status=parts[i];i+=1
                if not status:continue
                path=parts[i];i+=1
                if status[:1] in {"R","C"} and i<len(parts): path=parts[i];i+=1
                changed.append({"status":status,"path":path})
        except LedgerError:
            with transaction(self.con):self.con.execute("UPDATE operations SET state='FAILED',finished_at=? WHERE id=?",(now(),op))
            raise
        claim={k:data[k] for k in ("summary","evidence","risks","unresolved_questions","follow_up_work")}; payload=sha256(canonical_bytes(claim))
        existing=self.con.execute("SELECT * FROM submissions WHERE assignment_id=? AND head_commit_oid=? AND payload_hash=?",(a["id"],head,payload)).fetchone()
        if existing:
            with transaction(self.con):self.con.execute("UPDATE operations SET state='SUCCEEDED',finished_at=? WHERE id=?",(now(),op))
            return {"submission_id":existing["id"],"state":existing["state"],"idempotent":True}
        if self.con.execute("SELECT 1 FROM submissions WHERE assignment_id=? AND state IN ('PENDING','BLOCKED')",(a["id"],)).fetchone():raise LedgerError("SUBMISSION_ALREADY_PENDING","A pending or blocked submission already exists.")
        sid=new_id()
        with transaction(self.con):
            seq=self.con.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM submissions WHERE assignment_id=?",(a["id"],)).fetchone()[0]
            self.con.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(sid,project["id"],a["id"],seq,"PENDING",text(data["summary"],"summary"),head,canonical(changed),canonical(evidence),canonical(risks),canonical(questions),canonical(followups),payload,principal["id"],now(),None))
            for item,body in [(i,"description") for i in risks]+[(i,"body") for i in questions]:
                if item["blocking"]:self.system_blocker(project["id"],item["blocker_category"],"SUBMISSION",sid,f"Submission blocking item: {item[body]}")
            for item in followups:self.con.execute("INSERT INTO follow_up_proposals VALUES(?,?,?,?,?,?,?,?)",(new_id(),a["id"],sid,item["body"],"PROPOSED",now(),None,None))
            self.con.execute("UPDATE tasks SET state='SUBMITTED',updated_at=? WHERE id=?",(now(),task["id"]));self.con.execute("UPDATE operations SET state='SUCCEEDED',finished_at=? WHERE id=?",(now(),op));self.audit(project["id"],principal["id"],"SUBMISSION_RECORDED","SUBMISSION",sid)
        return {"submission_id":sid,"state":"PENDING","head_commit_oid":head,"changed_files":changed}

    # ---- verification and integration ----
    def verify_submission(self,project,principal,data):
        allowed={"submission_id","outcome","criterion_results","behavior_matches_intent","required_evidence_present","blocking_issues_remaining","corrections","notes","blocker_id","blocker"}
        require_object(data,allowed,{"submission_id","outcome","criterion_results","behavior_matches_intent","required_evidence_present","blocking_issues_remaining","corrections","notes"})
        sub=self.con.execute("SELECT s.*,a.task_id,a.id assignment_id,a.worker_profile FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.id=? AND s.project_id=?",(data["submission_id"],project["id"])).fetchone()
        if not sub:raise LedgerError("SUBMISSION_NOT_FOUND","Submission was not found.")
        task=self.con.execute("SELECT * FROM tasks WHERE id=?",(sub["task_id"],)).fetchone()
        if task["state"]!="SUBMITTED" or sub["state"] not in {"PENDING","BLOCKED"}:raise LedgerError("SUBMISSION_STATE_INVALID","Submission is not awaiting verification.")
        outcome=data["outcome"]
        if outcome not in {"ACCEPTED","REJECTED","BLOCKED"}:raise LedgerError("VERIFICATION_OUTCOME_INVALID","Unsupported verification outcome.")
        results=array(data["criterion_results"],"criterion_results"); criteria=self.con.execute("SELECT id FROM task_acceptance_criteria WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],task["current_revision"])).fetchall(); expected={r["id"] for r in criteria}
        seen=set()
        for result in results:
            require_object(result,{"criterion_id","satisfied","evidence"},{"criterion_id","satisfied","evidence"})
            if result["criterion_id"] not in expected or result["criterion_id"] in seen or not isinstance(result["satisfied"],bool):raise LedgerError("VERIFICATION_CRITERIA_INCOMPLETE","Criterion result is invalid.")
            seen.add(result["criterion_id"]);text(result["evidence"],"criterion evidence")
        if seen!=expected:raise LedgerError("VERIFICATION_CRITERIA_INCOMPLETE","Every current criterion needs exactly one result.")
        booleans=[data[x] for x in ("behavior_matches_intent","required_evidence_present","blocking_issues_remaining")]
        if not all(isinstance(x,bool) for x in booleans):raise LedgerError("INVALID_REQUEST","Verification assertions must be boolean.")
        satisfied=all(x["satisfied"] for x in results)
        blocker_id=None
        if outcome=="ACCEPTED":
            self.preflight(project)
            if not satisfied or not data["behavior_matches_intent"] or not data["required_evidence_present"] or data["blocking_issues_remaining"]:raise LedgerError("VERIFICATION_OUTCOME_INVALID","Accepted result has unmet verification assertions.")
            required_checks=[x[0] for x in self.con.execute("SELECT command FROM task_required_checks WHERE task_id=? AND task_revision=? ORDER BY position",(task["id"],task["current_revision"]))]
            reviewer_checks={x[0] for x in self.con.execute("SELECT command FROM execution_receipts WHERE submission_id=? AND execution_role='REVIEWER' AND status='SUCCEEDED' AND exit_code=0 AND stale=0 AND source_revision=?",(sub["id"],sub["head_commit_oid"]))}
            missing_reviewer_checks=[command for command in required_checks if command not in reviewer_checks]
            if missing_reviewer_checks:raise LedgerError("INDEPENDENT_CHECK_EVIDENCE_MISSING","Acceptance requires current orchestrator execution receipts for every required check.",details={"commands":missing_reviewer_checks})
            reasons=self.blocking_reasons(project["id"],"accept",task["id"],sub["assignment_id"],sub["id"])
            if reasons:raise LedgerError("BLOCKER_OPEN","An open blocker prevents acceptance.",details={"blockers":reasons})
            if not self.plan_current(project["id"])[0]:raise LedgerError("PLAN_INVALID","Plan must be valid before acceptance.")
        elif outcome=="REJECTED":
            if (satisfied and data["behavior_matches_intent"] and data["required_evidence_present"]) or not data.get("corrections"):raise LedgerError("VERIFICATION_OUTCOME_INVALID","Rejected outcome requires an unmet assertion and corrections.")
        else:
            if not data["blocking_issues_remaining"] or bool(data.get("blocker_id"))==bool(data.get("blocker")):raise LedgerError("VERIFICATION_OUTCOME_INVALID","Blocked outcome requires exactly one blocker reference or definition.")
            if data.get("blocker_id"):
                b=self.con.execute("SELECT * FROM blockers WHERE id=? AND project_id=? AND state='OPEN'",(data["blocker_id"],project["id"])).fetchone()
                if not b:raise LedgerError("BLOCKER_OPEN","Referenced blocker is not open.")
                blocker_id=b["id"]
            else:
                b=data["blocker"];require_object(b,{"category","description"},{"category","description"})
                if b["category"] not in CATEGORIES:raise LedgerError("INVALID_REQUEST","Unsupported blocker category.")
        routine_escalation=False
        if outcome=="REJECTED" and sub["worker_profile"]=="routine":
            prior_rejections=self.con.execute("SELECT COUNT(*) FROM submission_verifications sv JOIN submissions s ON s.id=sv.submission_id JOIN assignments a ON a.id=s.assignment_id WHERE a.task_id=? AND a.worker_profile='routine' AND sv.outcome='REJECTED'",(task["id"],)).fetchone()[0]
            routine_escalation=prior_rejections+1>=2
        with transaction(self.con):
            if outcome=="BLOCKED" and not blocker_id:
                blocker_id=self.system_blocker(project["id"],data["blocker"]["category"],"SUBMISSION",sub["id"],text(data["blocker"]["description"],"blocker description"))
            vid=new_id();self.con.execute("INSERT INTO submission_verifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(vid,sub["id"],principal["id"],outcome,canonical(results),int(data["behavior_matches_intent"]),int(data["required_evidence_present"]),int(data["blocking_issues_remaining"]),data.get("corrections"),text(data["notes"],"notes"),blocker_id,now()))
            if outcome=="ACCEPTED":
                self.con.execute("UPDATE submissions SET state='ACCEPTED',resolved_at=? WHERE id=?",(now(),sub["id"]));self.con.execute("UPDATE tasks SET state='ACCEPTED',updated_at=? WHERE id=?",(now(),task["id"]))
                a=self.con.execute("SELECT * FROM assignments WHERE id=?",(sub["assignment_id"],)).fetchone()
                self.con.execute("UPDATE principals SET active=0,deactivated_at=? WHERE assignment_id=?",(now(),a["id"]))
                if a["state"]=="ACTIVE":self.con.execute("UPDATE assignments SET state='CLOSED',closed_at=? WHERE id=?",(now(),a["id"]))
            elif outcome=="REJECTED":
                self.con.execute("UPDATE submissions SET state='REJECTED',resolved_at=? WHERE id=?",(now(),sub["id"])); a=self.con.execute("SELECT * FROM assignments WHERE id=?",(sub["assignment_id"],)).fetchone()
                if routine_escalation:
                    self.con.execute("UPDATE assignments SET state='REVOKED',revoked_at=?,revocation_reason=? WHERE id=?",(now(),"routine rejection limit reached",a["id"]))
                    self.con.execute("UPDATE principals SET active=0,deactivated_at=? WHERE assignment_id=?",(now(),a["id"]))
                    self.con.execute("UPDATE tasks SET state='PLANNED',updated_at=? WHERE id=?",(now(),task["id"]))
                    self.audit(project["id"],principal["id"],"ASSIGNMENT_ESCALATION_REQUIRED","ASSIGNMENT",a["id"],{"task_id":task["id"],"required_worker_profile":"complex","routine_rejections":2})
                else:self.con.execute("UPDATE tasks SET state=?,updated_at=? WHERE id=?",("ASSIGNED" if a["state"]=="ACTIVE" else "PLANNED",now(),task["id"]))
            else:self.con.execute("UPDATE submissions SET state='BLOCKED' WHERE id=?",(sub["id"],))
            self.audit(project["id"],principal["id"],"SUBMISSION_VERIFIED","SUBMISSION",sub["id"],{"outcome":outcome})
        if outcome=="ACCEPTED":
            result=self.integrate_task(project,principal,{"task_id":task["id"]},from_acceptance=True)
            return {"submission_id":sub["id"],"accepted":True,"integrated":result["integrated"],"task_state":result["task_state"],"blocker_id":result.get("blocker_id")}
        return {"submission_id":sub["id"],"outcome":outcome,"blocker_id":blocker_id,"task_state":task["state"] if outcome=="BLOCKED" else ("ASSIGNED" if self.con.execute("SELECT state FROM assignments WHERE id=?",(sub["assignment_id"],)).fetchone()["state"]=="ACTIVE" else "PLANNED"),"escalation_required":routine_escalation,"required_worker_profile":"complex" if routine_escalation else None}

    def current_integration(self,project,task_id):
        attempt=self.con.execute("SELECT * FROM integration_attempts WHERE project_id=? AND task_id=? AND state='SUCCEEDED' ORDER BY finished_at DESC,id DESC LIMIT 1",(project["id"],task_id)).fetchone()
        if attempt and git.ancestor(project["repository_root"],attempt["canonical_after_oid"],f"refs/heads/{project['canonical_branch']}"):return attempt
        return None

    def integrate_task(self,project,principal,data,from_acceptance=False):
        require_object(data,{"task_id"},{"task_id"});task=self.con.execute("SELECT * FROM tasks WHERE id=? AND project_id=?",(data["task_id"],project["id"])).fetchone()
        if not task:raise LedgerError("TASK_NOT_FOUND","Task was not found.")
        if task["state"]!="ACCEPTED":raise LedgerError("TASK_STATE_INVALID","Task is not accepted and awaiting integration.")
        self.preflight(project)
        if not self.plan_current(project["id"])[0]:raise LedgerError("PLAN_INVALID","Plan must be valid before integration.")
        sub=self.con.execute("SELECT * FROM submissions WHERE project_id=? AND assignment_id IN (SELECT id FROM assignments WHERE task_id=?) AND state='ACCEPTED' ORDER BY submitted_at DESC LIMIT 1",(project["id"],task["id"])).fetchone()
        if not sub:raise LedgerError("SUBMISSION_STATE_INVALID","Accepted submission is missing.")
        reasons=self.blocking_reasons(project["id"],"integrate",task["id"],sub["assignment_id"],sub["id"])
        if reasons:raise LedgerError("BLOCKER_OPEN","Open blockers prevent integration.",details={"blockers":reasons})
        root=project["repository_root"];branch=project["canonical_branch"]
        if git.current_branch(root)!=branch:raise LedgerError("CANONICAL_BRANCH_NOT_CHECKED_OUT","Repository root must be checked out on the canonical branch.")
        if not git.clean(root):raise LedgerError("CANONICAL_WORKTREE_DIRTY","Canonical worktree must be clean.")
        if git.operation_in_progress(root):raise LedgerError("RECOVERY_REQUIRED","Canonical worktree has an operation in progress.")
        before=git.oid(root,f"refs/heads/{branch}");iid,op=new_id(),new_id()
        with transaction(self.con):
            self.ensure_no_operation(project["id"])
            self.con.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)",(op,project["id"],"INTEGRATION","TASK",task["id"],"STARTED",canonical({"before":before,"accepted":sub["head_commit_oid"]}),None,now(),None));self.con.execute("INSERT INTO integration_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(iid,project["id"],task["id"],sub["id"],"STARTED",branch,before,sub["head_commit_oid"],None,None,now(),None,None))
        success=False;error=None
        try:
            if git.ancestor(root,sub["head_commit_oid"],f"refs/heads/{branch}"):success=True
            else:
                r=git.run(root,["-c","commit.gpgSign=false","-c","merge.autoStash=false","merge","--no-ff","--no-edit","--no-gpg-sign",sub["head_commit_oid"]],check=False)
                if r.returncode: error=r.stderr[-2000:];
                else:success=True
            after=git.oid(root)
            success=success and git.ancestor(root,sub["head_commit_oid"],after)
        except LedgerError as exc:error=exc.message;after=git.oid(root)
        if success:
            with transaction(self.con):
                self.con.execute("UPDATE integration_attempts SET state='SUCCEEDED',canonical_after_oid=?,finished_at=? WHERE id=?",(after,now(),iid));self.con.execute("UPDATE operations SET state='SUCCEEDED',result_json=?,finished_at=? WHERE id=?",(canonical({"after":after}),now(),op));self.con.execute("UPDATE tasks SET state='COMPLETED',completed_at=?,updated_at=? WHERE id=?",(now(),now(),task["id"]));self.invalidate_completion(project["id"],"task integration changed");self.audit(project["id"],principal["id"],"TASK_INTEGRATED","TASK",task["id"],{"after":after})
            return {"integrated":True,"task_state":"COMPLETED","integration_id":iid,"canonical_after_oid":after}
        # Only abort if Git says one is active; no rollback assumptions beyond clean/before proof.
        if git.operation_in_progress(root):git.run(root,["merge","--abort"],check=False)
        safe=git.oid(root)==before and git.clean(root) and not git.operation_in_progress(root)
        with transaction(self.con):
            if safe:
                self.con.execute("UPDATE integration_attempts SET state='FAILED',error_json=?,finished_at=? WHERE id=?",(canonical({"error":error}),now(),iid));self.con.execute("UPDATE operations SET state='FAILED',result_json=?,finished_at=? WHERE id=?",(canonical({"error":error}),now(),op));bid=self.system_blocker(project["id"],"REPOSITORY_STATE","INTEGRATION",iid,"Integration failed and was safely aborted")
                self.audit(project["id"],principal["id"],"INTEGRATION_FAILED","INTEGRATION",iid)
                return {"integrated":False,"task_state":"ACCEPTED","blocker_id":bid}
            self.con.execute("UPDATE integration_attempts SET state='UNCERTAIN',error_json=?,finished_at=? WHERE id=?",(canonical({"error":error}),now(),iid));self.con.execute("UPDATE operations SET state='UNCERTAIN',result_json=?,finished_at=? WHERE id=?",(canonical({"error":error}),now(),op));bid=self.system_blocker(project["id"],"INTERRUPTED_OPERATION","OPERATION",op,"Integration outcome cannot be proven")
        raise LedgerError("INTEGRATION_STATE_UNCERTAIN","Integration outcome is uncertain.",details={"blocker_id":bid})

    def reconcile_integrations(self,project):
        # This reconciliation is safe because it only removes credit after a factual reachability check.
        rows=self.con.execute("SELECT * FROM integration_attempts WHERE project_id=? AND state='SUCCEEDED'",(project["id"],)).fetchall()
        for attempt in rows:
            if not git.ancestor(project["repository_root"],attempt["canonical_after_oid"],f"refs/heads/{project['canonical_branch']}"):
                with transaction(self.con):
                    self.con.execute("UPDATE integration_attempts SET state='INVALIDATED',invalidated_at=? WHERE id=?",(now(),attempt["id"]));self.con.execute("UPDATE tasks SET state='ACCEPTED',completed_at=NULL,updated_at=? WHERE id=? AND state='COMPLETED'",(now(),attempt["task_id"]));self.invalid_requirements(self.current_links(attempt["task_id"]),"integration no longer reachable");self.invalidate_completion(project["id"],"integration no longer reachable");self.system_blocker(project["id"],"REPOSITORY_STATE","INTEGRATION",attempt["id"],"Recorded integration is no longer reachable from canonical branch")

    # ---- requirements, progress, recovery, completion ----
    def requirement_current(self, project, requirement):
        verification=self.con.execute("SELECT * FROM requirement_verifications WHERE requirement_id=? AND state='CURRENT'",(requirement["id"],)).fetchone()
        if not verification:return None
        if verification["requirement_revision"]!=requirement["current_revision"]:return None
        snapshot={x["task_id"]:x for x in json.loads(verification["task_snapshot_json"])}; current=self.current_tasks_for_requirement(requirement["id"])
        if set(snapshot)!={t["id"] for t in current}:return None
        for task in current:
            integration=self.current_integration(project,task["id"])
            if task["state"]!="COMPLETED" or not integration or snapshot[task["id"]]["revision"]!=task["current_revision"] or snapshot[task["id"]]["integration_oid"]!=integration["canonical_after_oid"]:return None
        reviews=self.con.execute("SELECT * FROM specification_reviews WHERE project_id=? AND state='COMPLETED' AND completed_at>?",(project["id"],verification["verified_at"])).fetchall()
        for review in reviews:
            affected=set(json.loads(review["affected_requirement_ids_json"] or "[]"))
            if requirement["id"] in affected:return None
            for tid in json.loads(review["affected_task_ids_json"] or "[]"):
                if tid in snapshot:return None
        return verification

    def requirement_verify(self,project,principal,data):
        require_object(data,{"requirement_id","evidence","notes"},{"requirement_id","evidence","notes"});req=self.con.execute("SELECT * FROM requirements WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(data["requirement_id"],project["id"])).fetchone()
        if not req:raise LedgerError("REQUIREMENT_NOT_ACTIVE","Requirement is not active.")
        self.preflight(project)
        if not self.plan_current(project["id"])[0]:raise LedgerError("PLAN_INVALID","Plan must be valid before requirement verification.")
        evidence=array(data["evidence"],"evidence")
        if not evidence:raise LedgerError("SUBMISSION_EVIDENCE_REQUIRED","Requirement verification requires evidence.")
        for item in evidence:require_object(item,{"label","details"},{"label","details"});text(item["label"],"evidence label");text(item["details"],"evidence details")
        definition=self.con.execute("SELECT * FROM requirement_revisions WHERE requirement_id=? AND revision=?",(req["id"],req["current_revision"])).fetchone(); tasks=self.current_tasks_for_requirement(req["id"])
        if self.blocking_reasons(project["id"],"verify_requirement",requirement_id=req["id"]):raise LedgerError("BLOCKER_OPEN","Open blocker prevents requirement verification.")
        if definition["implementation_required"]:
            if not tasks:raise LedgerError("REQUIREMENT_NOT_READY_FOR_VERIFICATION","Task-backed requirement has no covering tasks.")
            snapshots=[]
            for task in tasks:
                integration=self.current_integration(project,task["id"])
                if task["state"]!="COMPLETED" or not integration:raise LedgerError("REQUIREMENT_NOT_READY_FOR_VERIFICATION","All covering tasks must be integrated.",details={"task_id":task["id"]})
                accepted=self.con.execute("SELECT head_commit_oid FROM submissions WHERE assignment_id IN (SELECT id FROM assignments WHERE task_id=?) AND state='ACCEPTED' ORDER BY submitted_at DESC LIMIT 1",(task["id"],)).fetchone()
                snapshots.append({"task_id":task["id"],"revision":task["current_revision"],"accepted_oid":accepted[0] if accepted else None,"integration_oid":integration["canonical_after_oid"]})
        else:
            if tasks:raise LedgerError("REQUIREMENT_NOT_READY_FOR_VERIFICATION","Directly verifiable requirement must not have task links.")
            snapshots=[]
        with transaction(self.con):
            self.con.execute("UPDATE requirement_verifications SET state='INVALIDATED',invalidated_at=?,invalidation_reason=? WHERE requirement_id=? AND state='CURRENT'",(now(),"reverified",req["id"]))
            vid=new_id();fingerprint=self.plan_current(project["id"])[1];specs=[dict(x) for x in self.con.execute("SELECT s.id,r.content_hash FROM specifications s LEFT JOIN specification_revisions r ON r.id=s.active_revision_id WHERE s.project_id=? AND s.lifecycle='ACTIVE' ORDER BY s.id",(project["id"],))]
            self.con.execute("INSERT INTO requirement_verifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(vid,project["id"],req["id"],req["current_revision"],fingerprint,project["canonical_branch"],canonical(specs),canonical(snapshots),canonical(evidence),text(data["notes"],"notes"),"CURRENT",principal["id"],now(),None,None));self.audit(project["id"],principal["id"],"REQUIREMENT_VERIFIED","REQUIREMENT",req["id"])
        return {"requirement_id":req["id"],"verification_id":vid,"state":"COMPLETE"}

    def invalidate_requirement(self,project,principal,data):
        require_object(data,{"requirement_id","reason"},{"requirement_id","reason"});req=self.con.execute("SELECT * FROM requirements WHERE id=? AND project_id=? AND lifecycle='ACTIVE'",(data["requirement_id"],project["id"])).fetchone()
        if not req:raise LedgerError("REQUIREMENT_NOT_ACTIVE","Requirement is not active.")
        with transaction(self.con):
            result=self.con.execute("UPDATE requirement_verifications SET state='INVALIDATED',invalidated_at=?,invalidation_reason=? WHERE requirement_id=? AND state='CURRENT'",(now(),text(data["reason"],"reason"),req["id"]));
            if not result.rowcount:raise LedgerError("STALE_STATE","Requirement has no current verification.")
            self.invalidate_completion(project["id"],"requirement explicitly invalidated");self.audit(project["id"],principal["id"],"REQUIREMENT_INVALIDATED","REQUIREMENT",req["id"])
        return {"requirement_id":req["id"],"state":"REMAINING"}

    def requirement_rows(self,project):
        result=[]
        for r in self.con.execute("SELECT r.*,v.statement,v.details,v.implementation_required FROM requirements r JOIN requirement_revisions v ON v.requirement_id=r.id AND v.revision=r.current_revision WHERE r.project_id=? AND r.lifecycle='ACTIVE' ORDER BY r.id",(project["id"],)):
            verification=self.requirement_current(project,r);tasks=self.current_tasks_for_requirement(r["id"])
            reasons=self.blocking_reasons(project["id"],"verify_requirement",requirement_id=r["id"])
            status="COMPLETE" if verification else ("BLOCKED" if reasons or self.phase(project)=="SPECIFICATION_REVIEW_REQUIRED" else "REMAINING")
            ready,ready_reasons=self.requirement_ready(project,r,tasks,verification)
            result.append({"id":r["id"],"revision":r["current_revision"],"statement":r["statement"],"details":r["details"],"implementation_required":bool(r["implementation_required"]),"status":status,"ready_for_verification":ready,"readiness_reasons":ready_reasons,"task_ids":[t["id"] for t in tasks],"verification_id":verification["id"] if verification else None})
        return result

    def requirement_ready(self,project,requirement,tasks=None,verification=None):
        reasons=[];tasks=self.current_tasks_for_requirement(requirement["id"]) if tasks is None else tasks
        if verification is None:verification=self.requirement_current(project,requirement)
        if verification:reasons.append("ALREADY_COMPLETE")
        if self.phase(project)=="SPECIFICATION_REVIEW_REQUIRED":reasons.append("SPECIFICATION_REVIEW_REQUIRED")
        if not self.plan_current(project["id"])[0]:reasons.append("PLAN_INVALID")
        if self.blocking_reasons(project["id"],"verify_requirement",requirement_id=requirement["id"]):reasons.append("BLOCKER_OPEN")
        definition=self.con.execute("SELECT implementation_required FROM requirement_revisions WHERE requirement_id=? AND revision=?",(requirement["id"],requirement["current_revision"])).fetchone()
        if definition[0]:
            if not tasks:reasons.append("NO_COVERING_TASKS")
            for task in tasks:
                if task["state"]!="COMPLETED" or not self.current_integration(project,task["id"]):reasons.append("TASKS_NOT_INTEGRATED");break
        elif tasks:reasons.append("DIRECT_REQUIREMENT_HAS_TASKS")
        return not reasons,reasons

    def progress(self,project):
        reqs=self.requirement_rows(project);tasks=self.con.execute("SELECT * FROM tasks WHERE project_id=?",(project["id"],)).fetchall()
        return {"requirements":{"total":len(reqs),"complete":sum(r["status"]=="COMPLETE" for r in reqs),"remaining":sum(r["status"]=="REMAINING" for r in reqs),"blocked":sum(r["status"]=="BLOCKED" for r in reqs),"awaiting_final_verification":sum(r["status"]!="COMPLETE" and all(t["state"]=="COMPLETED" for t in self.current_tasks_for_requirement(r["id"])) for r in reqs)},"tasks":{"eligible":sum(self.task_eligible(project,t)[0] for t in tasks),"active_implementation":sum(t["state"]=="ASSIGNED" for t in tasks),"awaiting_submission_verification":sum(t["state"]=="SUBMITTED" for t in tasks),"accepted_awaiting_integration":sum(t["state"]=="ACCEPTED" for t in tasks),"completed":sum(t["state"]=="COMPLETED" for t in tasks),"cancelled":sum(t["state"]=="CANCELLED" for t in tasks)},"open_blockers":self.con.execute("SELECT COUNT(*) FROM blockers WHERE project_id=? AND state='OPEN'",(project["id"],)).fetchone()[0]}

    def completion_reasons(self,project):
        self.preflight(project);reasons=[];reqs=self.requirement_rows(project);valid,_,_=self.plan_current(project["id"])
        if git.oid_or_none(project["repository_root"],f"refs/heads/{project['canonical_branch']}") is None:reasons.append({"code":"INITIAL_COMMIT_REQUIRED","canonical_branch":project["canonical_branch"]})
        if not reqs:reasons.append({"code":"NO_ACTIVE_REQUIREMENTS"})
        if self.con.execute("SELECT 1 FROM specification_reviews WHERE project_id=? AND state='PENDING'",(project["id"],)).fetchone():reasons.append({"code":"SPECIFICATION_REVIEW_REQUIRED"})
        if not valid:reasons.append({"code":"PLAN_INVALID"})
        for r in reqs:
            if r["status"]!="COMPLETE":reasons.append({"code":"REQUIREMENT_INCOMPLETE","requirement_id":r["id"]})
        for task in self.con.execute("SELECT * FROM tasks WHERE project_id=?",(project["id"],)):
            if task["state"] in {"ASSIGNED","SUBMITTED","ACCEPTED"}:reasons.append({"code":"TASK_NOT_FINAL","task_id":task["id"],"state":task["state"]})
        for assignment in self.con.execute("SELECT id,state FROM assignments WHERE project_id=? AND state IN ('PREPARING','ACTIVE','UNCERTAIN')",(project["id"],)):
            reasons.append({"code":"ASSIGNMENT_NOT_FINAL","assignment_id":assignment["id"],"state":assignment["state"]})
        for b in self.con.execute("SELECT id FROM blockers WHERE project_id=? AND state='OPEN'",(project["id"],)):reasons.append({"code":"BLOCKER_OPEN","blocker_id":b[0]})
        if self.ensure_operation_exists(project["id"]):reasons.append({"code":"RECOVERY_REQUIRED"})
        return reasons

    def complete_project(self,project,principal):
        head=git.oid_or_none(project["repository_root"],f"refs/heads/{project['canonical_branch']}")
        if head is None:
            raise LedgerError(
                "INITIAL_COMMIT_REQUIRED",
                "This repository has no commits yet. Create the first commit on the canonical branch before completing the project.",
                details={"canonical_branch": project["canonical_branch"]},
            )
        reasons=self.completion_reasons(project)
        if reasons:raise LedgerError("PROJECT_NOT_READY_FOR_COMPLETION","Project cannot be completed.",details={"reasons":reasons})
        with transaction(self.con):
            existing=self.con.execute("SELECT * FROM projects WHERE id=?",(project["id"],)).fetchone()
            if existing["lifecycle"]!="COMPLETED":self.con.execute("UPDATE projects SET lifecycle='COMPLETED',completed_at=?,completion_head_oid=?,updated_at=? WHERE id=?",(now(),head,now(),project["id"]));self.audit(project["id"],principal["id"],"PROJECT_COMPLETED","PROJECT",project["id"],{"head":head})
        cleanup=self.cleanup_worktrees(project,principal)
        return {"project_id":project["id"],"completed":True,"completion_head_oid":head,"worktree_cleanup":cleanup}

    def cleanup_worktrees(self,project,principal):
        current=self.con.execute("SELECT lifecycle FROM projects WHERE id=?",(project["id"],)).fetchone()
        if not current or current["lifecycle"]!="COMPLETED":
            raise LedgerError("PROJECT_NOT_COMPLETED","Assignment worktrees may be cleaned only after project completion.")
        self.ensure_no_operation(project["id"])
        managed_root=(self.home/"projects"/project["id"]/"worktrees").resolve()
        removed=[];already_absent=[];skipped=[]
        rows=self.con.execute("SELECT id,state,branch_name,worktree_path FROM assignments WHERE project_id=? ORDER BY created_at,id",(project["id"],)).fetchall()
        try:registered=git.registered_worktrees(project["repository_root"])
        except LedgerError:
            registered=None
        for assignment in rows:
            path=Path(assignment["worktree_path"])
            resolved=path.resolve()
            item={"assignment_id":assignment["id"],"worktree_path":str(path),"branch_name":assignment["branch_name"]}
            if assignment["state"] in {"PREPARING","ACTIVE","UNCERTAIN"}:
                skipped.append({**item,"reason":"ASSIGNMENT_NOT_FINAL"});continue
            if not resolved.is_relative_to(managed_root):
                skipped.append({**item,"reason":"PATH_OUTSIDE_MANAGED_ROOT"});continue
            if not path.exists():
                already_absent.append(item);continue
            if registered is None:
                skipped.append({**item,"reason":"GIT_WORKTREE_LIST_FAILED"});continue
            if str(resolved) not in registered:
                skipped.append({**item,"reason":"WORKTREE_NOT_REGISTERED"});continue
            try:
                if git.current_branch(path)!=assignment["branch_name"]:
                    skipped.append({**item,"reason":"BRANCH_MISMATCH"});continue
                if git.operation_in_progress(path):
                    skipped.append({**item,"reason":"GIT_OPERATION_IN_PROGRESS"});continue
                if not git.clean(path):
                    skipped.append({**item,"reason":"WORKTREE_DIRTY"});continue
                git.run(project["repository_root"],["worktree","remove",str(path)])
                removed.append(item)
            except LedgerError:
                skipped.append({**item,"reason":"GIT_REFUSED_REMOVAL"})
        result={"removed_count":len(removed),"already_absent_count":len(already_absent),"skipped_count":len(skipped),"removed":removed,"already_absent":already_absent,"skipped":skipped,"branches_retained":True}
        if removed or skipped:
            with transaction(self.con):self.audit(project["id"],principal["id"],"ASSIGNMENT_WORKTREES_CLEANED","PROJECT",project["id"],{"removed_assignment_ids":[x["assignment_id"] for x in removed],"skipped":[{"assignment_id":x["assignment_id"],"reason":x["reason"]} for x in skipped]})
        return result

    def preparation_diagnostics(self, project, data):
        require_object(data,{"profiles","local_inputs","services","host_agent_availability"})
        profiles=data.get("profiles",["routine","complex"]);profiles=array(profiles,"profiles")
        if any(profile not in WORKER_PROFILES for profile in profiles):raise LedgerError("INVALID_REQUEST","profiles may contain only routine or complex.")
        inputs=array(data.get("local_inputs",[]),"local_inputs");services=array(data.get("services",[]),"services")
        host=data.get("host_agent_availability",{})
        if not isinstance(host,dict):raise LedgerError("INVALID_REQUEST","host_agent_availability must be an object.")
        version_checks={}
        for name,command in (("python",[sys.executable,"--version"]),("git",["git","--version"])):
            completed=subprocess.run(command,text=True,capture_output=True,check=False)
            version_checks[name]={"available":completed.returncode==0,"version":(completed.stdout or completed.stderr).strip()[:200]}
        version_checks["taskledger"]={"available":True,"version":__import__("taskledger").__version__}
        profile_checks=[]
        for profile in profiles:
            name=f"taskledger-worker-{profile}.toml"
            candidates=[Path(project["repository_root"])/".codex"/"agents"/name,
                        Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex")))/"agents"/name]
            path=next((candidate for candidate in candidates if candidate.is_file()),None)
            configured={"profile":profile,"configuration_present":path is not None,
                        "configuration_path":str(path) if path else None,
                        "runtime_capability":"HOST_HANDSHAKE_REQUIRED"}
            if path:
                raw=path.read_text(encoding="utf-8",errors="replace")
                for field in ("model","model_reasoning_effort"):
                    import re
                    match=re.search(rf"^\s*{field}\s*=\s*\"([^\"]+)\"",raw,re.MULTILINE)
                    configured[field]=match.group(1) if match else None
            if profile in host:
                if not isinstance(host[profile],bool):raise LedgerError("INVALID_REQUEST","Host availability values must be boolean.")
                configured["host_reported_available"]=host[profile]
                configured["runtime_capability"]="HOST_REPORTED_UNVERIFIED"
            profile_checks.append(configured)
        input_checks=[]
        for item in inputs:
            require_object(item,{"name","path","required"},{"name","path","required"})
            if not isinstance(item["required"],bool):raise LedgerError("INVALID_REQUEST","local input required must be boolean.")
            path=Path(text(item["path"],"local input path")).expanduser();exists=path.exists()
            input_checks.append({"name":text(item["name"],"local input name"),"path":str(path),"required":item["required"],
                                 "exists":exists,"kind":"directory" if path.is_dir() else ("file" if path.is_file() else "missing"),
                                 "readable":exists and os.access(path,os.R_OK),"contents_inspected":False})
        service_checks=[]
        for item in services:
            require_object(item,{"name","host","port","required"},{"name","host","port","required"})
            port=item["port"]
            if not isinstance(port,int) or isinstance(port,bool) or not 1<=port<=65535 or not isinstance(item["required"],bool):raise LedgerError("INVALID_REQUEST","Service port/required value is invalid.")
            reachable=False;error=None
            try:
                with socket.create_connection((text(item["host"],"service host"),port),timeout=1):reachable=True
            except OSError as exc:error=exc.__class__.__name__
            service_checks.append({"name":text(item["name"],"service name"),"host":item["host"],"port":port,"required":item["required"],"reachable":reachable,"error_class":error})
        info=git.inspect(project["repository_root"])
        repository={"root":info["root"],"symbolic_branch":info["branch"],"canonical_branch":project["canonical_branch"],
                    "branch_matches":info["branch"]==project["canonical_branch"],"has_commits":info["has_commits"],
                    "clean":git.clean(project["repository_root"]),"ledger_directory_ignored":git.ignored(project["repository_root"],".taskledger/")}
        ready=all(x["available"] for x in version_checks.values()) and all(x["configuration_present"] for x in profile_checks) and repository["branch_matches"] and repository["has_commits"] and repository["ledger_directory_ignored"] and all((not x["required"]) or (x["exists"] and x["readable"]) for x in input_checks) and all((not x["required"]) or x["reachable"] for x in service_checks)
        host_reported_ready=ready and all(x.get("host_reported_available") is True for x in profile_checks)
        return {"protocol_version":4,"ready_for_local_preparation":ready,"ready_for_assignment_from_host_report":host_reported_ready,"versions":version_checks,"repository":repository,
                "profiles":profile_checks,"local_inputs":input_checks,"services":service_checks,
                "host_contract":{"configuration_is_not_runtime_proof":True,"required_action":"The host must confirm it can launch the selected exact named profile before assignment creation.","silent_model_substitution_forbidden":True},
                "secrets_read":False}

    def wait_for_events(self, project, data):
        require_object(data,{"cursor","event_types","timeout_ms","limit"})
        cursor=data.get("cursor",0);timeout_ms=data.get("timeout_ms",30000);limit=data.get("limit",50)
        if not isinstance(cursor,int) or isinstance(cursor,bool) or cursor<0:raise LedgerError("INVALID_REQUEST","cursor must be a non-negative audit sequence.")
        if not isinstance(timeout_ms,int) or isinstance(timeout_ms,bool) or not 0<=timeout_ms<=60000:raise LedgerError("INVALID_REQUEST","timeout_ms must be between 0 and 60000.")
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=200:raise LedgerError("INVALID_REQUEST","limit must be between 1 and 200.")
        types=array(data.get("event_types",sorted(ACTIONABLE_EVENTS)),"event_types")
        if not types or any(not isinstance(value,str) or value not in ACTIONABLE_EVENTS for value in types):raise LedgerError("INVALID_REQUEST","event_types must contain supported actionable event names.")
        latest_at_start=self.con.execute("SELECT COALESCE(MAX(sequence),0) FROM audit_events WHERE project_id=?",(project["id"],)).fetchone()[0]
        if cursor>latest_at_start:
            raise LedgerError("EVENT_CURSOR_INVALID","Event cursor is ahead of the latest durable project event.",details={"cursor":cursor,"latest_event_sequence":latest_at_start})
        placeholders=",".join("?" for _ in types);deadline=time.monotonic()+timeout_ms/1000
        cancelled=False;rows=[]
        try:
            while True:
                rows=self.con.execute(f"SELECT sequence,event_type,entity_type,entity_id,payload_json,created_at FROM audit_events WHERE project_id=? AND sequence>? AND event_type IN ({placeholders}) ORDER BY sequence LIMIT ?",(project["id"],cursor,*types,limit)).fetchall()
                if rows or time.monotonic()>=deadline:break
                time.sleep(min(0.1,max(0,deadline-time.monotonic())))
        except KeyboardInterrupt:cancelled=True
        minimum=self.con.execute("SELECT MIN(sequence) FROM audit_events WHERE project_id=?",(project["id"],)).fetchone()[0]
        latest=self.con.execute("SELECT COALESCE(MAX(sequence),0) FROM audit_events WHERE project_id=?",(project["id"],)).fetchone()[0]
        events=[]
        for row in rows:
            event=dict(row);event["payload"]=json.loads(event.pop("payload_json"));events.append(event)
        next_cursor=events[-1]["sequence"] if events else cursor
        return {"events":events,"event_cursor":next_cursor,"latest_event_sequence":latest,"timed_out":not events and not cancelled,
                "cancelled":cancelled,"missed_events":False,"retained_event_floor":minimum,
                "cursor_gap_status":"NO_PRUNING_CONFIGURED",
                "host_wakeup_required":True,"polling_occurred_outside_model_reasoning":True}

    def evidence_export(self, project, principal, data):
        require_object(data,{"task_id"})
        task_id=data.get("task_id")
        if task_id is not None and not self.con.execute("SELECT 1 FROM tasks WHERE id=? AND project_id=?",(task_id,project["id"])).fetchone():raise LedgerError("TASK_NOT_FOUND","Task was not found.")
        task_clause=" AND a.task_id=?" if task_id else "";params=(project["id"],task_id) if task_id else (project["id"],)
        claims=[]
        for row in self.con.execute("SELECT s.*,a.task_id,a.task_revision FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.project_id=?"+task_clause+" ORDER BY a.task_id,s.assignment_id,s.sequence",params):
            claims.append({"provenance":"WORKER_CLAIM","submission_id":row["id"],"task_id":row["task_id"],"task_revision":row["task_revision"],"head_commit_oid":row["head_commit_oid"],"summary":row["summary"],"evidence":json.loads(row["evidence_json"]),"risks":json.loads(row["risks_json"]),"unresolved_questions":json.loads(row["unresolved_questions_json"]),"follow_up_work":json.loads(row["follow_up_work_json"])})
        receipts=[]
        for row in self.con.execute("SELECT r.* FROM execution_receipts r JOIN assignments a ON a.id=r.assignment_id WHERE r.project_id=?"+task_clause+" ORDER BY r.started_at,r.id",params):
            item=dict(row);item["provenance"]="OBSERVED_EXECUTION";item["telemetry"]=json.loads(item.pop("telemetry_json"));receipts.append(item)
        conclusions=[]
        for row in self.con.execute("SELECT sv.*,s.head_commit_oid,a.task_id FROM submission_verifications sv JOIN submissions s ON s.id=sv.submission_id JOIN assignments a ON a.id=s.assignment_id WHERE s.project_id=?"+task_clause+" ORDER BY sv.created_at,sv.id",params):
            item=dict(row);item["provenance"]="REVIEWER_CONCLUSION";item["criterion_results"]=json.loads(item.pop("criterion_results_json"));conclusions.append(item)
        checkpoint_params=(project["id"],task_id) if task_id else (project["id"],)
        for row in self.con.execute("SELECT c.*,a.task_id FROM assignment_checkpoints c JOIN assignments a ON a.id=c.assignment_id WHERE a.project_id=?"+task_clause+" AND c.reviewer_principal_id IS NOT NULL ORDER BY c.resolved_at,c.id",checkpoint_params):
            item=dict(row);item["provenance"]="CHECKPOINT_REVIEWER_CONCLUSION";item["criteria"]=json.loads(item.pop("criteria_json"));item["criterion_results"]=json.loads(item.pop("criterion_results_json"));item["evidence"]=json.loads(item.pop("evidence_json"));conclusions.append(item)
        sources=[]
        source_query="SELECT rr.requirement_id,rr.requirement_revision,rr.specification_id,rr.locator,rr.excerpt,s.relative_path,sr.content_hash FROM requirement_source_refs rr JOIN specifications s ON s.id=rr.specification_id LEFT JOIN specification_revisions sr ON sr.id=s.active_revision_id JOIN task_requirement_links l ON l.requirement_id=rr.requirement_id JOIN tasks t ON t.id=l.task_id AND t.current_revision=l.task_revision WHERE t.project_id=?"
        source_params=[project["id"]]
        if task_id:source_query+=" AND t.id=?";source_params.append(task_id)
        source_query+=" ORDER BY rr.requirement_id,rr.specification_id,rr.locator"
        for row in self.con.execute(source_query,source_params):sources.append({**dict(row),"provenance":"REGISTERED_SOURCE_CLAIM","claim_preserved_verbatim":True})
        artifacts=[dict(x) for x in self.con.execute("SELECT id,assignment_id,provenance_kind,original_path,stored_path,sha256,size_bytes,source_revision,created_at FROM evidence_artifacts WHERE project_id=? AND provenance_kind<>'MECHANICAL_EVIDENCE_EXPORT' ORDER BY created_at,id",(project["id"],))]
        document={"protocol_version":4,"project_id":project["id"],"task_filter":task_id,"source_use_claims":sources,"worker_claims":claims,"observed_execution_receipts":receipts,"reviewer_conclusions":conclusions,"registered_artifacts":artifacts,"semantic_conclusions_generated":False,"read_or_delivery_telemetry_proves_understanding":False}
        raw=canonical(document).encode();digest=sha256(raw);export_id=new_id();root=self.home/"projects"/project["id"]/"exports";root.mkdir(mode=0o700,parents=True,exist_ok=True);path=root/f"evidence-{digest}.json"
        if not path.exists():path.write_bytes(raw);os.chmod(path,0o600)
        with transaction(self.con):
            existing=self.con.execute("SELECT id FROM evidence_artifacts WHERE project_id=? AND provenance_kind='MECHANICAL_EVIDENCE_EXPORT' AND sha256=?",(project["id"],digest)).fetchone()
            if existing:export_id=existing["id"]
            else:self.con.execute("INSERT INTO evidence_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)",(export_id,project["id"],None,principal["id"],"MECHANICAL_EVIDENCE_EXPORT",None,str(path),digest,len(raw),git.oid_or_none(project["repository_root"],"HEAD"),now()))
            self.audit(project["id"],principal["id"],"EVIDENCE_EXPORTED","PROJECT",project["id"],{"sha256":digest,"task_id":task_id})
        return {"export_artifact_id":export_id,"path":str(path),"sha256":digest,"bytes":len(raw),"deterministic_content":True}

    def resume(self,project,data):
        require_object(data,{"cursor"});valid,fingerprint,diagnostics=self.plan_current(project["id"]);reqs=self.requirement_rows(project)
        latest=self.con.execute("SELECT COALESCE(MAX(sequence),0) FROM audit_events WHERE project_id=?",(project["id"],)).fetchone()[0]
        operations=[dict(x) for x in self.con.execute("SELECT id,kind,entity_type,entity_id,state,started_at,finished_at FROM operations WHERE project_id=? AND state IN ('STARTED','UNCERTAIN') ORDER BY started_at,id",(project["id"],))]
        snapshot={"schema_version":4,"project_id":project["id"],"phase":self.phase(project),"canonical_branch":project["canonical_branch"],"canonical_head_oid":git.oid_or_none(project["repository_root"],f"refs/heads/{project['canonical_branch']}"),"plan":{"valid":valid,"fingerprint":fingerprint,"diagnostics":diagnostics},"progress":self.progress(project),"pending_review_ids":[x[0] for x in self.con.execute("SELECT id FROM specification_reviews WHERE project_id=? AND state='PENDING' ORDER BY id",(project["id"],))],"eligible_task_ids":[t["id"] for t in self.con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id",(project["id"],)) if self.task_eligible(project,t)[0]],"active_assignments":[dict(x) for x in self.con.execute("SELECT id,task_id,worker_profile,state,worktree_path,base_commit_oid FROM assignments WHERE project_id=? AND state IN ('PREPARING','ACTIVE') ORDER BY id",(project["id"],))],"pending_submissions":[dict(x) for x in self.con.execute("SELECT s.id,s.assignment_id,s.state,s.head_commit_oid FROM submissions s WHERE s.project_id=? AND s.state IN ('PENDING','BLOCKED') ORDER BY s.id",(project["id"],))],"ready_requirement_ids":[r["id"] for r in reqs if r["ready_for_verification"]],"open_blockers":[dict(x) for x in self.con.execute("SELECT id,category,scope_type,scope_id,description FROM blockers WHERE project_id=? AND state='OPEN' ORDER BY id",(project["id"],))],"open_questions":[dict(x) for x in self.con.execute("SELECT q.id,q.assignment_id,q.body,q.is_blocking FROM worker_questions q JOIN assignments a ON a.id=q.assignment_id WHERE a.project_id=? AND q.state='OPEN' ORDER BY q.id",(project["id"],))],"operations":operations,"full_recovery_required":bool(operations),"latest_event_sequence":latest}
        cursor=sha256(canonical(snapshot)); supplied=data.get("cursor")
        if supplied==cursor:return {"project_id":project["id"],"cursor":cursor,"not_modified":True,"full_recovery_required":bool(operations),"operations":operations}
        snapshot.update({"cursor":cursor,"not_modified":False,"baseline_status":"NEW" if supplied is None else "REFRESHED"})
        return snapshot

    def recover(self,project):
        self.preflight(project);valid,fingerprint,diagnostics=self.plan_current(project["id"]);reqs=self.requirement_rows(project); tasks=[dict(x) for x in self.con.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at,id",(project["id"],))]
        assignments=[dict(x) for x in self.con.execute("SELECT * FROM assignments WHERE project_id=? ORDER BY created_at,id",(project["id"],))]
        canonical_head=git.oid_or_none(project["repository_root"],f"refs/heads/{project['canonical_branch']}")
        return {"project":{"id":project["id"],"repository_root":project["repository_root"],"canonical_branch":project["canonical_branch"],"canonical_head_oid":canonical_head,"initial_commit_required":canonical_head is None,"effective_phase":self.phase(project),"completed":project["lifecycle"]=="COMPLETED"},"specifications":{"active":[dict(x) for x in self.con.execute("SELECT * FROM specifications WHERE project_id=? AND lifecycle='ACTIVE' ORDER BY id",(project["id"],))],"pending_reviews":[dict(x) for x in self.con.execute("SELECT * FROM specification_reviews WHERE project_id=? AND state='PENDING' ORDER BY id",(project["id"],))]},"plan":{"valid":valid,"fingerprint":fingerprint,"diagnostics":diagnostics},"progress":self.progress(project),"requirements":{"complete":[r for r in reqs if r["status"]=="COMPLETE"],"remaining":[r for r in reqs if r["status"]=="REMAINING"],"blocked":[r for r in reqs if r["status"]=="BLOCKED"],"awaiting_verification":[r for r in reqs if r["status"]!="COMPLETE"]},"tasks":{"all":tasks,"assignments":assignments,"eligible":[t["id"] for t in tasks if self.task_eligible(project,t)[0]]},"questions":{"open":[dict(x) for x in self.con.execute("SELECT q.* FROM worker_questions q JOIN assignments a ON a.id=q.assignment_id WHERE a.project_id=? AND q.state='OPEN' ORDER BY q.asked_at,q.id",(project["id"],))]},"follow_up_proposals":{"unreviewed":[dict(x) for x in self.con.execute("SELECT f.* FROM follow_up_proposals f JOIN assignments a ON a.id=f.assignment_id WHERE a.project_id=? AND f.state='PROPOSED' ORDER BY f.created_at,f.id",(project["id"],))]},"blockers":{"open":[dict(x) for x in self.con.execute("SELECT * FROM blockers WHERE project_id=? AND state='OPEN' ORDER BY id",(project["id"],))]},"operations":{"started_or_uncertain":[dict(x) for x in self.con.execute("SELECT * FROM operations WHERE project_id=? AND state IN ('STARTED','UNCERTAIN') ORDER BY started_at,id",(project["id"],))]},"completion":{"eligible":not self.completion_reasons(project),"blocking_reasons":self.completion_reasons(project)},"allowed_next_actions":["spec.review" if self.phase(project)=="SPECIFICATION_REVIEW_REQUIRED" else "plan.validate"]}

    def recover_operation(self, project, principal, data, adopt: bool):
        require_object(data,{"operation_id","current_oid"},{"operation_id","current_oid"})
        op=self.con.execute("SELECT * FROM operations WHERE id=? AND project_id=? AND state IN ('STARTED','UNCERTAIN')",(data["operation_id"],project["id"])).fetchone()
        if not op:raise LedgerError("RECOVERY_REQUIRED","No unresolved operation with that ID exists.")
        expected=json.loads(op["expected_state_json"]); actual=git.oid(project["repository_root"])
        if data["current_oid"]!=actual:raise LedgerError("STALE_STATE","Supplied recovery OID does not match repository.")
        if adopt:
            if op["kind"]!="INTEGRATION" or not git.ancestor(project["repository_root"],expected["accepted"],actual):raise LedgerError("RECOVERY_REQUIRED","Repository facts do not prove success.")
            attempt=self.con.execute("SELECT * FROM integration_attempts WHERE project_id=? AND task_id=? AND state IN ('STARTED','UNCERTAIN') ORDER BY started_at DESC LIMIT 1",(project["id"],op["entity_id"])).fetchone()
            with transaction(self.con):
                self.con.execute("UPDATE operations SET state='SUCCEEDED',result_json=?,finished_at=? WHERE id=?",(canonical({"adopted_oid":actual}),now(),op["id"]))
                if attempt:
                    self.con.execute("UPDATE integration_attempts SET state='SUCCEEDED',canonical_after_oid=?,finished_at=? WHERE id=?",(actual,now(),attempt["id"]))
                    self.con.execute("UPDATE tasks SET state='COMPLETED',completed_at=?,updated_at=? WHERE id=?",(now(),now(),attempt["task_id"]))
                self.con.execute("UPDATE blockers SET state='RESOLVED',resolution=?,resolved_by_principal_id=?,resolved_at=? WHERE project_id=? AND scope_type='OPERATION' AND scope_id=? AND state='OPEN'",("Operation success adopted from repository proof",principal["id"],now(),project["id"],op["id"]))
                self.audit(project["id"],principal["id"],"OPERATION_SUCCESS_ADOPTED","OPERATION",op["id"])
        else:
            pre_operation_oid={"INTEGRATION":expected.get("before"),"SUBMISSION_CHECKPOINT":expected.get("head"),"ASSIGNMENT_PREPARATION":expected.get("base")}.get(op["kind"])
            if pre_operation_oid is None or actual!=pre_operation_oid:raise LedgerError("RECOVERY_REQUIRED","Repository is not restored to the expected pre-operation OID.")
            with transaction(self.con):
                self.con.execute("UPDATE operations SET state='FAILED',finished_at=? WHERE id=?",(now(),op["id"]))
                if op["kind"] == "INTEGRATION": self.con.execute("UPDATE integration_attempts SET state='FAILED',finished_at=? WHERE project_id=? AND task_id=? AND state IN ('STARTED','UNCERTAIN')",(now(),project["id"],op["entity_id"]))
                if op["kind"] == "ASSIGNMENT_PREPARATION":
                    assignment=self.con.execute("SELECT * FROM assignments WHERE id=?",(op["entity_id"],)).fetchone()
                    if assignment:
                        self.con.execute("UPDATE assignments SET state='REVOKED',revoked_at=?,revocation_reason=? WHERE id=?",(now(),"operation marked failed",assignment["id"]))
                        self.con.execute("UPDATE tasks SET state='PLANNED',updated_at=? WHERE id=? AND state='ASSIGNED'",(now(),assignment["task_id"]))
                self.con.execute("UPDATE blockers SET state='RESOLVED',resolution=?,resolved_by_principal_id=?,resolved_at=? WHERE project_id=? AND scope_type='OPERATION' AND scope_id=? AND state='OPEN'",("Operation marked failed from restored repository proof",principal["id"],now(),project["id"],op["id"]))
                self.audit(project["id"],principal["id"],"OPERATION_MARKED_FAILED","OPERATION",op["id"])
        return {"operation_id":op["id"],"state":"SUCCEEDED" if adopt else "FAILED"}
