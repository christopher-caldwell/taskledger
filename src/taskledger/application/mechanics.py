from __future__ import annotations

from taskledger.core import LedgerError, require_object


def active_specification_revision(service, project, *, relative_path=None, specification_id=None):
    """Synchronize and bind one current immutable specification revision."""
    service.preflight(project)
    if relative_path is not None:
        row = service.con.execute(
            "SELECT s.id specification_id,s.relative_path,s.lifecycle,r.id revision_id,r.file_state,r.content_hash,r.content_bytes "
            "FROM specifications s LEFT JOIN specification_revisions r ON r.id=s.active_revision_id "
            "WHERE s.project_id=? AND s.relative_path=?", (project["id"], relative_path),
        ).fetchone()
    else:
        row = service.con.execute(
            "SELECT s.id specification_id,s.relative_path,s.lifecycle,r.id revision_id,r.file_state,r.content_hash,r.content_bytes "
            "FROM specifications s LEFT JOIN specification_revisions r ON r.id=s.active_revision_id "
            "WHERE s.project_id=? AND s.id=?", (project["id"], specification_id),
        ).fetchone()
    if not row or row["lifecycle"] != "ACTIVE" or row["file_state"] != "PRESENT" or not row["content_hash"] or row["content_bytes"] is None:
        raise LedgerError("SPECIFICATION_STATE", "Preparation requires a present active specification revision.")
    return row


def project_controller_config(limits):
    from taskledger.controller.project import ProjectControllerConfig
    from taskledger.controller.supervisor import SupervisorConfig
    require_object(limits, {"max_workers", "max_reviewers", "max_total_worker_turns", "max_total_reviewer_turns",
        "reviewer_token_reserve", "max_final_reviewer_turns", "max_task_creator_turns", "max_total_task_creator_turns",
        "max_inflight_targets", "task_creator_profile", "supervisor"})
    supervisor = limits.get("supervisor", {})
    require_object(supervisor, {"max_worker_turns", "max_consecutive_stalled_turns", "max_consecutive_runtime_failures",
        "max_reviewer_turns_per_submission", "max_total_tokens", "max_elapsed_seconds", "turn_timeout_seconds",
        "reviewer_profile"})
    try:
        config = ProjectControllerConfig(
            max_workers=limits.get("max_workers", 2), max_reviewers=limits.get("max_reviewers", 1),
            max_total_worker_turns=limits.get("max_total_worker_turns", 100),
            max_total_reviewer_turns=limits.get("max_total_reviewer_turns", 50),
            reviewer_token_reserve=limits.get("reviewer_token_reserve", 0),
            max_final_reviewer_turns=limits.get("max_final_reviewer_turns", 2),
            max_task_creator_turns=limits.get("max_task_creator_turns", 2),
            max_total_task_creator_turns=limits.get("max_total_task_creator_turns", 6),
            max_inflight_targets=limits.get("max_inflight_targets"),
            task_creator_profile=limits.get("task_creator_profile", "taskledger_task_creator"),
            supervisor=SupervisorConfig(**supervisor),
        )
        config.validate()
        return config
    except (TypeError, ValueError) as exc:
        raise LedgerError("INVALID_REQUEST", str(exc)) from exc
