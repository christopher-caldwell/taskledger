from __future__ import annotations

import json
from typing import Any

from taskledger import git
from taskledger.core import canonical, sha256

from .model import ExecutionStatus, ExecutionView, ReviewOutcome, ReviewPreparation, ReviewVerdict


class TaskledgerLedgerAdapter:
    """Translate controller operations into existing Taskledger service behavior."""

    def __init__(self, service, project, orchestrator, *, worker_socket: str | None = None, worker_tool_enabled: bool = False):
        self.service = service
        self.project = project
        self.orchestrator = orchestrator
        self.worker_socket = worker_socket
        self.worker_tool_enabled = worker_tool_enabled

    def execution_view(self, assignment_id: str) -> ExecutionView:
        assignment = self.service.con.execute(
            "SELECT * FROM assignments WHERE id=? AND project_id=?", (assignment_id, self.project["id"])
        ).fetchone()
        if not assignment:
            raise RuntimeError("assignment was not found")
        task = self.service.con.execute("SELECT * FROM tasks WHERE id=?", (assignment["task_id"],)).fetchone()
        if not task:
            raise RuntimeError("assignment task was not found")

        if assignment["state"] == "REVOKED":
            return ExecutionView(assignment_id, ExecutionStatus.REVOKED, assignment["worker_profile"], detail=assignment["revocation_reason"])
        if assignment["state"] == "UNCERTAIN":
            return ExecutionView(assignment_id, ExecutionStatus.BLOCKED, assignment["worker_profile"], detail="assignment preparation is uncertain")
        if task["state"] == "CANCELLED":
            return ExecutionView(assignment_id, ExecutionStatus.REVOKED, assignment["worker_profile"], detail=task["cancellation_reason"] or "task cancelled")

        blockers = self.service.blocking_reasons(
            self.project["id"], "assign", task_id=task["id"], assignment_id=assignment_id
        )
        open_question = self.service.con.execute(
            "SELECT body FROM worker_questions WHERE assignment_id=? AND is_blocking=1 AND state='OPEN' ORDER BY asked_at,id LIMIT 1",
            (assignment_id,),
        ).fetchone()
        if open_question or blockers:
            detail = open_question["body"] if open_question else blockers[0]["description"]
            return ExecutionView(assignment_id, ExecutionStatus.BLOCKED, assignment["worker_profile"], detail=detail)

        if task["state"] == "COMPLETED":
            integration = self.service.current_integration(self.project, task["id"])
            if integration:
                return ExecutionView(assignment_id, ExecutionStatus.COMPLETED, assignment["worker_profile"])
            return ExecutionView(assignment_id, ExecutionStatus.ACCEPTED, assignment["worker_profile"], detail="recorded integration is not reachable")
        if task["state"] == "ACCEPTED":
            uncertain = self.service.con.execute(
                "SELECT id FROM integration_attempts WHERE task_id=? AND state='UNCERTAIN' ORDER BY started_at DESC LIMIT 1",
                (task["id"],),
            ).fetchone()
            operation = self.service.con.execute(
                "SELECT id FROM operations WHERE project_id=? AND kind='INTEGRATION' AND entity_id=? AND state='UNCERTAIN'",
                (self.project["id"], task["id"]),
            ).fetchone()
            if uncertain or operation:
                return ExecutionView(assignment_id, ExecutionStatus.INTEGRATION_UNCERTAIN, assignment["worker_profile"], detail="integration outcome requires recovery")
            return ExecutionView(assignment_id, ExecutionStatus.ACCEPTED, assignment["worker_profile"], detail="accepted work was not integrated")
        if task["state"] == "SUBMITTED":
            submission = self.service.con.execute(
                "SELECT * FROM submissions WHERE assignment_id=? AND state IN ('PENDING','BLOCKED') ORDER BY sequence DESC LIMIT 1",
                (assignment_id,),
            ).fetchone()
            if not submission:
                raise RuntimeError("submitted task has no pending submission")
            if submission["state"] == "BLOCKED":
                return ExecutionView(assignment_id, ExecutionStatus.BLOCKED, assignment["worker_profile"], submission["id"], detail="submission is blocked")
            return ExecutionView(assignment_id, ExecutionStatus.SUBMITTED, assignment["worker_profile"], submission["id"])
        if task["state"] != "ASSIGNED" or assignment["state"] != "ACTIVE":
            raise RuntimeError(f"unsupported task and assignment state: {task['state']}/{assignment['state']}")
        checkpoint = self.service.checkpoint_progress(assignment).get("pending")
        if checkpoint:
            return ExecutionView(
                assignment_id, ExecutionStatus.CHECKPOINT, assignment["worker_profile"],
                pending_checkpoint_id=checkpoint["id"],
            )
        return ExecutionView(
            assignment_id,
            ExecutionStatus.ACTIVE,
            assignment["worker_profile"],
            correction_packet=self.service.correction_packet(self.project, assignment),
        )

    def progress_fingerprint(self, assignment_id: str) -> str:
        assignment = self._assignment(assignment_id)
        snapshot = {
            "tree": self.service.tree_fingerprint(assignment["worktree_path"]),
            "assignment": dict(self.service.con.execute(
                "SELECT state,task_revision,worker_profile FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()),
            "task": dict(self.service.con.execute(
                "SELECT state,current_revision,completed_at FROM tasks WHERE id=?", (assignment["task_id"],)
            ).fetchone()),
            "questions": [dict(row) for row in self.service.con.execute(
                "SELECT id,state,is_blocking,answer FROM worker_questions WHERE assignment_id=? ORDER BY asked_at,id", (assignment_id,)
            )],
            "submissions": [dict(row) for row in self.service.con.execute(
                "SELECT id,state,head_commit_oid,resolved_at FROM submissions WHERE assignment_id=? ORDER BY sequence", (assignment_id,)
            )],
            "correction": self.service.correction_packet(self.project, assignment),
            "checkpoints": self.service.checkpoint_progress(assignment),
        }
        return sha256(canonical(snapshot))

    def worker_prompt(self, assignment_id: str, *, first_turn: bool) -> str:
        assignment = self._assignment(assignment_id)
        context = json.loads(assignment["context_json"] or self.service.context(assignment))
        dynamic = {
            "correction_packet": self.service.correction_packet(self.project, assignment),
            "checkpoint_progress": self.service.checkpoint_progress(assignment),
            "questions": [dict(row) for row in self.service.con.execute(
                "SELECT id,body,is_blocking,state,answer FROM worker_questions WHERE assignment_id=? ORDER BY asked_at,id", (assignment_id,)
            )],
        }
        instruction = (
            "Load this persisted Taskledger assignment context. Continue all authorized work until you submit, create a blocking question or blocker, or the task becomes invalid. "
            "A normal turn ending is not completion. Use only assignment scoped worker operations. Do not load Codex skill files or invoke the Taskledger CLI; the current context is already included below."
            if first_turn
            else "Continue the same Taskledger assignment. Recheck the current correction and checkpoint state below, then keep working until submission or a durable blocker. Do not reload Codex skill files."
        )
        broker = ""
        if self.worker_socket:
            broker = (
                "\nUse `python3 -m taskledger.worker_proxy <action> --socket " + self.worker_socket
                + " --input -` for Taskledger worker operations. The proxy is already limited to this assignment; no credential is available in the model environment."
            )
        elif self.worker_tool_enabled:
            broker = "\nUse the assignment-scoped `taskledger_*` tools for context, checks, checkpoints, evidence, questions, blockers, follow-up work, and submission. No credential is available in the model environment."
        return instruction + broker + "\n\n" + canonical({"context": context, "dynamic": dynamic})

    def worker_cwd(self, assignment_id: str) -> str:
        return self._assignment(assignment_id)["worktree_path"]

    def submission_pending(self, submission_id: str) -> bool:
        row = self.service.con.execute(
            "SELECT state FROM submissions WHERE id=? AND project_id=?", (submission_id, self.project["id"])
        ).fetchone()
        return bool(row and row["state"] in {"PENDING", "BLOCKED"})

    def checkpoint_pending(self, checkpoint_id: str) -> bool:
        row = self.service.con.execute(
            "SELECT c.state FROM assignment_checkpoints c JOIN assignments a ON a.id=c.assignment_id WHERE c.id=? AND a.project_id=?",
            (checkpoint_id, self.project["id"]),
        ).fetchone()
        return bool(row and row["state"] == "PENDING")

    def prepare_checkpoint_review(self, checkpoint_id: str) -> ReviewPreparation:
        context = self.service.checkpoint_review_context(self.project, checkpoint_id)
        checkpoint = context["checkpoint"]
        assignment = self._assignment(checkpoint["assignment_id"])
        current = (
            checkpoint["state"] == "PENDING"
            and checkpoint["head_object_exists"]
            and not checkpoint["definition_stale"]
            and git.oid(assignment["worktree_path"]) == checkpoint["head_commit_oid"]
            and git.clean(assignment["worktree_path"])
            and self.service.plan_current(self.project["id"])[0]
            and not self.service.blocking_reasons(
                self.project["id"], "assign", task_id=assignment["task_id"], assignment_id=assignment["id"]
            )
        )
        if not current:
            raise RuntimeError("checkpoint checkout, definition, plan, or blocker state is stale")
        context["review_kind"] = "CHECKPOINT"
        context["acceptance_allowed"] = True
        criterion_ids = tuple(f"checkpoint:{position}" for position in range(1, len(checkpoint["criteria"]) + 1))
        return ReviewPreparation(checkpoint_id, criterion_ids, True, context)

    def prepare_review(self, submission_id: str) -> ReviewPreparation:
        context = self.service.submission_review_context(self.project, submission_id)
        submission = context["submission"]
        assignment = self._assignment(submission["assignment_id"])
        structurally_current = (
            submission["state"] == "PENDING"
            and submission["head_object_exists"]
            and not submission["definition_stale"]
            and git.oid(assignment["worktree_path"]) == submission["head_commit_oid"]
            and git.clean(assignment["worktree_path"])
            and self.service.plan_current(self.project["id"])[0]
            and not context["open_blockers"]
        )
        if not structurally_current:
            raise RuntimeError("submission checkout, definition, plan, or blocker state is stale")
        receipts = []
        for command in context["required_checks"]:
            try:
                receipts.append(self.service.reviewer_check(
                    self.project, self.orchestrator,
                    {"submission_id": submission_id, "command": command, "timeout_seconds": 900},
                ))
            except Exception as exc:
                receipts.append({"command": command, "status": "ERROR", "error": str(exc), "source_changed_during_execution": True})
        current = self.service.submission_review_context(self.project, submission_id)
        receipt_by_command = {receipt["command"]: receipt for receipt in receipts}
        checks_pass = all(
            command in receipt_by_command
            and receipt_by_command[command].get("status") == "SUCCEEDED"
            and receipt_by_command[command].get("exit_code") == 0
            and not receipt_by_command[command].get("source_changed_during_execution")
            for command in context["required_checks"]
        )
        current["controller_reviewer_checks"] = receipts
        current["acceptance_allowed"] = structurally_current and checks_pass
        return ReviewPreparation(
            submission_id,
            tuple(item["id"] for item in current["criteria"]),
            structurally_current and checks_pass,
            current,
        )

    def reviewer_prompt(self, preparation: ReviewPreparation) -> str:
        return (
            "Independently review the exact immutable Taskledger submission described below. Inspect the exact diff in the read only checkout. "
            "Return one structured verdict covering every criterion exactly once. Failed checks make acceptance impossible but still require a truthful semantic rejection or blocker. "
            "Do not run ledger mutations and do not infer success from worker claims.\n\n"
            + canonical(preparation.context)
        )

    def checkpoint_reviewer_prompt(self, preparation: ReviewPreparation) -> str:
        return (
            "Independently review this exact immutable Taskledger checkpoint commit in the read only checkout. "
            "Return ACCEPTED only when every checkpoint criterion is satisfied. Return REJECTED with exact corrections otherwise. "
            "This verdict only advances an intermediate checkpoint and never accepts or integrates the task.\n\n"
            + canonical(preparation.context)
        )

    def reviewer_cwd(self, submission_id: str) -> str:
        row = self.service.con.execute(
            "SELECT a.worktree_path FROM submissions s JOIN assignments a ON a.id=s.assignment_id WHERE s.id=? AND s.project_id=?",
            (submission_id, self.project["id"]),
        ).fetchone()
        if not row:
            raise RuntimeError("submission was not found")
        return row["worktree_path"]

    def checkpoint_reviewer_cwd(self, checkpoint_id: str) -> str:
        row = self.service.con.execute(
            "SELECT a.worktree_path FROM assignment_checkpoints c JOIN assignments a ON a.id=c.assignment_id WHERE c.id=? AND a.project_id=?",
            (checkpoint_id, self.project["id"]),
        ).fetchone()
        if not row:
            raise RuntimeError("checkpoint was not found")
        return row["worktree_path"]

    def reviewer_output_schema(self, preparation: ReviewPreparation) -> dict[str, Any]:
        criterion = {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "string", "enum": list(preparation.expected_criterion_ids)},
                "satisfied": {"type": "boolean"},
                "evidence": {"type": "string", "minLength": 1},
            },
            "required": ["criterion_id", "satisfied", "evidence"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["ACCEPTED", "REJECTED", "BLOCKED"]},
                "criterion_results": {"type": "array", "items": criterion, "minItems": len(preparation.expected_criterion_ids), "maxItems": len(preparation.expected_criterion_ids)},
                "behavior_matches_intent": {"type": "boolean"},
                "required_evidence_present": {"type": "boolean"},
                "blocking_issues_remaining": {"type": "boolean"},
                "corrections": {"type": ["string", "null"]},
                "notes": {"type": "string"},
                "blocker_id": {"type": ["string", "null"]},
                "blocker": {
                    "type": ["object", "null"],
                    "properties": {"category": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["category", "description"],
                    "additionalProperties": False,
                },
            },
            "required": ["outcome", "criterion_results", "behavior_matches_intent", "required_evidence_present", "blocking_issues_remaining", "corrections", "notes", "blocker_id", "blocker"],
            "additionalProperties": False,
        }

    def checkpoint_reviewer_output_schema(self, preparation: ReviewPreparation) -> dict[str, Any]:
        schema = self.reviewer_output_schema(preparation)
        schema["properties"]["outcome"]["enum"] = ["ACCEPTED", "REJECTED"]
        return schema

    def apply_review(self, submission_id: str, verdict: ReviewVerdict) -> ExecutionView:
        before = self.service.submission_review_context(self.project, submission_id)
        if before["submission"]["state"] != "PENDING" or before["submission"]["definition_stale"]:
            raise RuntimeError("review verdict is stale")
        data: dict[str, Any] = {
            "submission_id": submission_id,
            "outcome": verdict.outcome.value,
            "criterion_results": list(verdict.criterion_results),
            "behavior_matches_intent": verdict.behavior_matches_intent,
            "required_evidence_present": verdict.required_evidence_present,
            "blocking_issues_remaining": verdict.blocking_issues_remaining,
            "corrections": verdict.corrections,
            "notes": verdict.notes,
        }
        if verdict.blocker_id:
            data["blocker_id"] = verdict.blocker_id
        if verdict.blocker:
            data["blocker"] = verdict.blocker
        self.service.verify_submission(self.project, self.orchestrator, data)
        return self.execution_view(before["submission"]["assignment_id"])

    def apply_checkpoint_review(self, checkpoint_id: str, verdict: ReviewVerdict) -> ExecutionView:
        before = self.service.checkpoint_review_context(self.project, checkpoint_id)
        checkpoint = before["checkpoint"]
        if checkpoint["state"] != "PENDING" or checkpoint["definition_stale"]:
            raise RuntimeError("checkpoint reviewer verdict is stale")
        if verdict.outcome not in {ReviewOutcome.ACCEPTED, ReviewOutcome.REJECTED}:
            raise RuntimeError("checkpoint reviewer verdict must accept or reject")
        results = []
        for item in verdict.criterion_results:
            prefix, position = item["criterion_id"].split(":", 1)
            if prefix != "checkpoint":
                raise RuntimeError("checkpoint reviewer criterion identity is invalid")
            results.append({"position": int(position), "satisfied": item["satisfied"], "evidence": item["evidence"]})
        self.service.verify_checkpoint(self.project, self.orchestrator, {
            "checkpoint_id": checkpoint_id,
            "outcome": "APPROVED" if verdict.outcome == ReviewOutcome.ACCEPTED else "REJECTED",
            "criterion_results": results,
            "corrections": verdict.corrections,
            "notes": verdict.notes,
        })
        return self.execution_view(checkpoint["assignment_id"])

    def _assignment(self, assignment_id: str):
        row = self.service.con.execute(
            "SELECT * FROM assignments WHERE id=? AND project_id=?", (assignment_id, self.project["id"])
        ).fetchone()
        if not row:
            raise RuntimeError("assignment was not found")
        return row
