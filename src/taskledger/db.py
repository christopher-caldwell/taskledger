from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

from .core import LedgerError, now


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, repository_root TEXT NOT NULL UNIQUE, git_common_dir TEXT NOT NULL,
 canonical_branch TEXT NOT NULL, canonical_branch_confirmed_at TEXT NOT NULL,
 lifecycle TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE','COMPLETED')), planning_started_at TEXT,
 completed_at TEXT, completion_head_oid TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS principals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), role TEXT NOT NULL CHECK(role IN ('ORCHESTRATOR','WORKER')),
 assignment_id TEXT, active INTEGER NOT NULL CHECK(active IN (0,1)), created_at TEXT NOT NULL, deactivated_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_orchestrator ON principals(project_id) WHERE role='ORCHESTRATOR' AND active=1;
CREATE TABLE IF NOT EXISTS principal_credentials(principal_id TEXT PRIMARY KEY REFERENCES principals(id),token_hash TEXT NOT NULL,created_at TEXT NOT NULL,rotated_at TEXT);
CREATE TABLE IF NOT EXISTS specifications (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),relative_path TEXT NOT NULL,lifecycle TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE','RETIRED')),
 active_revision_id TEXT,created_at TEXT NOT NULL,retired_at TEXT,UNIQUE(project_id,relative_path));
CREATE TABLE IF NOT EXISTS specification_revisions (
 id TEXT PRIMARY KEY,specification_id TEXT NOT NULL REFERENCES specifications(id),sequence INTEGER NOT NULL,file_state TEXT NOT NULL CHECK(file_state IN ('PRESENT','MISSING','UNREADABLE')),
 content_hash TEXT,content_bytes BLOB,error_message TEXT,observed_at TEXT NOT NULL,approved_at TEXT,UNIQUE(specification_id,sequence));
CREATE TABLE IF NOT EXISTS specification_reviews (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),specification_id TEXT NOT NULL REFERENCES specifications(id),from_revision_id TEXT,to_revision_id TEXT NOT NULL REFERENCES specification_revisions(id),
 change_kind TEXT NOT NULL CHECK(change_kind IN ('ADDED','MODIFIED','MISSING','UNREADABLE','RESTORED')),state TEXT NOT NULL CHECK(state IN ('PENDING','COMPLETED')),
 resolution TEXT,affected_requirement_ids_json TEXT,affected_task_ids_json TEXT,no_existing_items_affected INTEGER,review_summary TEXT,reviewer_principal_id TEXT,created_at TEXT NOT NULL,completed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS pending_spec_review ON specification_reviews(specification_id) WHERE state='PENDING';
CREATE TABLE IF NOT EXISTS requirements (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),current_revision INTEGER NOT NULL,lifecycle TEXT NOT NULL CHECK(lifecycle IN ('ACTIVE','RETIRED')),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,retired_at TEXT);
CREATE TABLE IF NOT EXISTS requirement_revisions(requirement_id TEXT NOT NULL REFERENCES requirements(id),revision INTEGER NOT NULL,statement TEXT NOT NULL,details TEXT NOT NULL,implementation_required INTEGER NOT NULL CHECK(implementation_required IN (0,1)),created_by_principal_id TEXT NOT NULL REFERENCES principals(id),created_at TEXT NOT NULL,PRIMARY KEY(requirement_id,revision));
CREATE TABLE IF NOT EXISTS requirement_source_refs(requirement_id TEXT NOT NULL,requirement_revision INTEGER NOT NULL,specification_id TEXT NOT NULL REFERENCES specifications(id),locator TEXT NOT NULL,excerpt TEXT,PRIMARY KEY(requirement_id,requirement_revision,specification_id,locator),FOREIGN KEY(requirement_id,requirement_revision) REFERENCES requirement_revisions(requirement_id,revision));
CREATE TABLE IF NOT EXISTS requirement_supersessions(
 superseded_requirement_id TEXT PRIMARY KEY REFERENCES requirements(id),
 replacement_requirement_id TEXT NOT NULL REFERENCES requirements(id),
 reason TEXT NOT NULL,created_by_principal_id TEXT NOT NULL REFERENCES principals(id),created_at TEXT NOT NULL,
 CHECK(superseded_requirement_id<>replacement_requirement_id));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),current_revision INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('PLANNED','ASSIGNED','SUBMITTED','ACCEPTED','COMPLETED','CANCELLED')),cancellation_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,completed_at TEXT,cancelled_at TEXT);
CREATE TABLE IF NOT EXISTS task_revisions(task_id TEXT NOT NULL REFERENCES tasks(id),revision INTEGER NOT NULL,objective TEXT NOT NULL,implementation_scope TEXT NOT NULL,created_by_principal_id TEXT NOT NULL REFERENCES principals(id),created_at TEXT NOT NULL,PRIMARY KEY(task_id,revision));
CREATE TABLE IF NOT EXISTS task_acceptance_criteria(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,position INTEGER NOT NULL,criterion_text TEXT NOT NULL,FOREIGN KEY(task_id,task_revision) REFERENCES task_revisions(task_id,revision),UNIQUE(task_id,task_revision,position));
CREATE TABLE IF NOT EXISTS task_required_checks(task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,position INTEGER NOT NULL,command TEXT NOT NULL,FOREIGN KEY(task_id,task_revision) REFERENCES task_revisions(task_id,revision),PRIMARY KEY(task_id,task_revision,position),UNIQUE(task_id,task_revision,command));
CREATE TABLE IF NOT EXISTS task_requirement_links(task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,requirement_id TEXT NOT NULL REFERENCES requirements(id),PRIMARY KEY(task_id,task_revision,requirement_id),FOREIGN KEY(task_id,task_revision) REFERENCES task_revisions(task_id,revision));
CREATE TABLE IF NOT EXISTS task_dependencies(task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),PRIMARY KEY(task_id,task_revision,depends_on_task_id),FOREIGN KEY(task_id,task_revision) REFERENCES task_revisions(task_id,revision),CHECK(task_id<>depends_on_task_id));
CREATE TABLE IF NOT EXISTS plan_validations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),fingerprint TEXT NOT NULL,succeeded INTEGER NOT NULL,diagnostics_json TEXT NOT NULL,specification_snapshot_json TEXT NOT NULL,created_by_principal_id TEXT NOT NULL REFERENCES principals(id),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignments(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),task_id TEXT NOT NULL REFERENCES tasks(id),task_revision INTEGER NOT NULL,attempt_number INTEGER NOT NULL,worker_profile TEXT NOT NULL CHECK(worker_profile IN ('routine','complex')),execution_mode TEXT NOT NULL DEFAULT 'isolated' CHECK(execution_mode IN ('isolated','lightweight')),checkpoint_plan_json TEXT NOT NULL DEFAULT '[]',state TEXT NOT NULL CHECK(state IN ('PREPARING','ACTIVE','CLOSED','REVOKED','UNCERTAIN')),base_commit_oid TEXT NOT NULL,branch_name TEXT NOT NULL,worktree_path TEXT NOT NULL,context_json TEXT NOT NULL,created_at TEXT NOT NULL,activated_at TEXT,closed_at TEXT,revoked_at TEXT,revocation_reason TEXT,UNIQUE(task_id,attempt_number));
CREATE UNIQUE INDEX IF NOT EXISTS active_assignment ON assignments(task_id) WHERE state IN ('PREPARING','ACTIVE');
CREATE TABLE IF NOT EXISTS worker_questions(id TEXT PRIMARY KEY,assignment_id TEXT NOT NULL REFERENCES assignments(id),body TEXT NOT NULL,is_blocking INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('OPEN','ANSWERED')),blocker_id TEXT,answer TEXT,asked_at TEXT NOT NULL,answered_by_principal_id TEXT,answered_at TEXT);
CREATE TABLE IF NOT EXISTS follow_up_proposals(id TEXT PRIMARY KEY,assignment_id TEXT NOT NULL REFERENCES assignments(id),submission_id TEXT,body TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('PROPOSED','ACKNOWLEDGED','DISMISSED')),created_at TEXT NOT NULL,reviewed_at TEXT,reviewed_by_principal_id TEXT);
CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),assignment_id TEXT NOT NULL REFERENCES assignments(id),sequence INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('PENDING','BLOCKED','REJECTED','ACCEPTED','SUPERSEDED')),summary TEXT NOT NULL,head_commit_oid TEXT NOT NULL,changed_files_json TEXT NOT NULL,evidence_json TEXT NOT NULL,risks_json TEXT NOT NULL,unresolved_questions_json TEXT NOT NULL,follow_up_work_json TEXT NOT NULL,payload_hash TEXT NOT NULL,submitted_by_principal_id TEXT NOT NULL REFERENCES principals(id),submitted_at TEXT NOT NULL,resolved_at TEXT,UNIQUE(assignment_id,sequence));
CREATE TABLE IF NOT EXISTS submission_verifications(id TEXT PRIMARY KEY,submission_id TEXT NOT NULL REFERENCES submissions(id),verifier_principal_id TEXT NOT NULL REFERENCES principals(id),outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED','REJECTED','BLOCKED')),criterion_results_json TEXT NOT NULL,behavior_matches_intent INTEGER NOT NULL,required_evidence_present INTEGER NOT NULL,blocking_issues_remaining INTEGER NOT NULL,corrections TEXT,notes TEXT NOT NULL,blocker_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignment_checkpoints(
 id TEXT PRIMARY KEY,assignment_id TEXT NOT NULL REFERENCES assignments(id),sequence INTEGER NOT NULL,
 task_revision INTEGER NOT NULL,label TEXT NOT NULL,criteria_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PENDING','APPROVED','REJECTED','SUPERSEDED')),
 summary TEXT NOT NULL,head_commit_oid TEXT NOT NULL,evidence_json TEXT NOT NULL,payload_hash TEXT NOT NULL,
 submitted_by_principal_id TEXT NOT NULL REFERENCES principals(id),submitted_at TEXT NOT NULL,resolved_at TEXT,
 reviewer_principal_id TEXT REFERENCES principals(id),criterion_results_json TEXT,corrections TEXT,notes TEXT,
 UNIQUE(assignment_id,sequence,head_commit_oid));
CREATE TABLE IF NOT EXISTS evidence_artifacts(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),assignment_id TEXT REFERENCES assignments(id),
 registered_by_principal_id TEXT NOT NULL REFERENCES principals(id),provenance_kind TEXT NOT NULL,
 original_path TEXT,stored_path TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,
 source_revision TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_receipts(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),assignment_id TEXT NOT NULL REFERENCES assignments(id),
 submission_id TEXT REFERENCES submissions(id),executed_by_principal_id TEXT NOT NULL REFERENCES principals(id),execution_role TEXT NOT NULL CHECK(execution_role IN ('WORKER','REVIEWER')),
 command TEXT NOT NULL,cwd TEXT NOT NULL,source_revision TEXT,tree_fingerprint_before TEXT NOT NULL,
 tree_fingerprint_after TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','FAILED','TIMED_OUT','INTERRUPTED')),
 exit_code INTEGER,output_artifact_id TEXT REFERENCES evidence_artifacts(id),stale INTEGER NOT NULL DEFAULT 0,
 telemetry_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integration_attempts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),task_id TEXT NOT NULL REFERENCES tasks(id),submission_id TEXT NOT NULL REFERENCES submissions(id),state TEXT NOT NULL CHECK(state IN ('STARTED','SUCCEEDED','FAILED','UNCERTAIN','INVALIDATED')),canonical_branch TEXT NOT NULL,canonical_before_oid TEXT NOT NULL,accepted_head_oid TEXT NOT NULL,canonical_after_oid TEXT,error_json TEXT,started_at TEXT NOT NULL,finished_at TEXT,invalidated_at TEXT);
CREATE TABLE IF NOT EXISTS requirement_verifications(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),requirement_id TEXT NOT NULL REFERENCES requirements(id),requirement_revision INTEGER NOT NULL,plan_fingerprint TEXT NOT NULL,canonical_branch TEXT NOT NULL,specification_snapshot_json TEXT NOT NULL,task_snapshot_json TEXT NOT NULL,evidence_json TEXT NOT NULL,notes TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('CURRENT','INVALIDATED')),verified_by_principal_id TEXT NOT NULL REFERENCES principals(id),verified_at TEXT NOT NULL,invalidated_at TEXT,invalidation_reason TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS current_requirement_verification ON requirement_verifications(requirement_id) WHERE state='CURRENT';
CREATE TABLE IF NOT EXISTS blockers(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),category TEXT NOT NULL,scope_type TEXT NOT NULL,scope_id TEXT,description TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('OPEN','RESOLVED')),created_by_principal_id TEXT,created_by_system INTEGER NOT NULL,created_at TEXT NOT NULL,resolution TEXT,resolved_by_principal_id TEXT,resolved_at TEXT);
CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('STARTED','SUCCEEDED','FAILED','UNCERTAIN')),expected_state_json TEXT NOT NULL,result_json TEXT,started_at TEXT NOT NULL,finished_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS current_operation ON operations(project_id) WHERE state='STARTED';
CREATE TABLE IF NOT EXISTS audit_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES projects(id),principal_id TEXT,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
"""


def connect(home: Path) -> sqlite3.Connection:
    path = home / "taskledger.sqlite3"
    con: sqlite3.Connection | None = None
    try:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.executescript("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;")
        con.executescript(SCHEMA)
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(1,?)", (now(),))
        assignment_columns = {row[1] for row in con.execute("PRAGMA table_info(assignments)")}
        if "worker_profile" not in assignment_columns:
            con.execute(
                "ALTER TABLE assignments ADD COLUMN worker_profile TEXT NOT NULL DEFAULT 'complex' "
                "CHECK(worker_profile IN ('routine','complex'))"
            )
        if "execution_mode" not in assignment_columns:
            con.execute(
                "ALTER TABLE assignments ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'isolated' "
                "CHECK(execution_mode IN ('isolated','lightweight'))"
            )
        if "checkpoint_plan_json" not in assignment_columns:
            con.execute("ALTER TABLE assignments ADD COLUMN checkpoint_plan_json TEXT NOT NULL DEFAULT '[]'")
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(2,?)", (now(),))
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(3,?)", (now(),))
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(4,?)", (now(),))
        return con
    except (OSError, sqlite3.Error) as exc:
        if con is not None:
            con.close()
        raise LedgerError(
            "LEDGER_STORAGE_UNAVAILABLE",
            "Taskledger could not open this repository's local state.",
            details={"path": str(path), "reason": exc.__class__.__name__},
        )


@contextlib.contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except Exception:
        con.rollback()
        raise
    else:
        con.commit()
