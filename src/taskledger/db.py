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
CREATE TABLE IF NOT EXISTS controller_runs(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),mode TEXT NOT NULL CHECK(mode IN ('ASSIGNMENT','PROJECT','PREPARATION')),
 state TEXT NOT NULL CHECK(state IN ('RUNNING','PAUSED','COMPLETED','FAILED')),config_json TEXT NOT NULL,
 pause_reason TEXT,pause_detail TEXT,created_at TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_controller_run ON controller_runs(project_id) WHERE state='RUNNING';
CREATE TABLE IF NOT EXISTS controller_run_targets(
 run_id TEXT NOT NULL REFERENCES controller_runs(id),task_id TEXT NOT NULL REFERENCES tasks(id),wave INTEGER NOT NULL,
 worker_profile TEXT NOT NULL CHECK(worker_profile IN ('routine','complex')),parallel_safe INTEGER NOT NULL CHECK(parallel_safe IN (0,1)),
 write_surfaces_json TEXT NOT NULL,assignment_id TEXT REFERENCES assignments(id),state TEXT NOT NULL CHECK(state IN ('QUEUED','ACTIVE','INTEGRATED','BLOCKED','CANCELLED')),
 PRIMARY KEY(run_id,task_id));
CREATE TABLE IF NOT EXISTS controller_sessions(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES controller_runs(id),project_id TEXT NOT NULL REFERENCES projects(id),
 role TEXT NOT NULL CHECK(role IN ('TASK_CREATOR','WORKER','REVIEWER','REQUIREMENT_REVIEWER')),profile TEXT NOT NULL,subject_id TEXT NOT NULL,
 external_thread_id TEXT NOT NULL,config_hash TEXT NOT NULL,runtime_identity_json TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('ACTIVE','CLOSED','UNCERTAIN')),
 created_at TEXT NOT NULL,closed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_controller_session ON controller_sessions(project_id,role,subject_id) WHERE state='ACTIVE';
CREATE TABLE IF NOT EXISTS controller_turns(
 id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES controller_sessions(id),sequence INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('DISPATCHING','RUNNING','COMPLETED','FAILED','UNCERTAIN')),prompt_kind TEXT NOT NULL,
 external_turn_id TEXT,result_json TEXT,final_response TEXT,error TEXT,progress_before TEXT,progress_after TEXT,
 progressed INTEGER CHECK(progressed IN (0,1)),dispatch_reason TEXT,prompt_builder_version TEXT,controller_payload_bytes INTEGER NOT NULL DEFAULT 0,
 static_assignment_bytes INTEGER NOT NULL DEFAULT 0,dynamic_state_bytes INTEGER NOT NULL DEFAULT 0,correction_bytes INTEGER NOT NULL DEFAULT 0,
 output_schema_bytes INTEGER NOT NULL DEFAULT 0,dynamic_state_hash TEXT,context_hashes_json TEXT NOT NULL DEFAULT '{}',response_hash TEXT,
 input_tokens INTEGER NOT NULL DEFAULT 0,cached_input_tokens INTEGER NOT NULL DEFAULT 0,cache_write_input_tokens INTEGER NOT NULL DEFAULT 0,
 output_tokens INTEGER NOT NULL DEFAULT 0,reasoning_tokens INTEGER NOT NULL DEFAULT 0,usage_precision TEXT NOT NULL DEFAULT 'MISSING',
 usage_before_json TEXT,usage_after_json TEXT,exact_response_count INTEGER NOT NULL DEFAULT 0,usage_missing INTEGER NOT NULL DEFAULT 0 CHECK(usage_missing IN (0,1)),
 started_at TEXT NOT NULL,finished_at TEXT,consumed_at TEXT,
 UNIQUE(session_id,sequence));
CREATE INDEX IF NOT EXISTS controller_turns_open ON controller_turns(state) WHERE state IN ('DISPATCHING','RUNNING','UNCERTAIN');
CREATE INDEX IF NOT EXISTS controller_turns_unconsumed ON controller_turns(session_id,consumed_at) WHERE consumed_at IS NULL;
CREATE TABLE IF NOT EXISTS controller_usage_events(
 id TEXT PRIMARY KEY,turn_id TEXT NOT NULL REFERENCES controller_turns(id),external_event_id TEXT NOT NULL,raw_json TEXT NOT NULL,
 input_tokens INTEGER,cached_input_tokens INTEGER,output_tokens INTEGER,reasoning_tokens INTEGER,created_at TEXT NOT NULL,
 UNIQUE(turn_id,external_event_id));
CREATE TABLE IF NOT EXISTS controller_budget_grants(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES controller_runs(id),kind TEXT NOT NULL CHECK(kind IN ('WORKER_TURNS','REVIEWER_TURNS','TASK_CREATOR_TURNS','TOKENS','ELAPSED_SECONDS')),
 amount INTEGER NOT NULL CHECK(amount>0),reason TEXT NOT NULL,granted_by_principal_id TEXT NOT NULL REFERENCES principals(id),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controller_final_reviews(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES controller_runs(id),canonical_oid TEXT NOT NULL,
 plan_fingerprint TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('PENDING','SATISFIED','DEFECTS','AMBIGUOUS','INVALID')),
 verdict_json TEXT,created_at TEXT NOT NULL,resolved_at TEXT);
CREATE TABLE IF NOT EXISTS controller_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES controller_runs(id),scope_type TEXT NOT NULL,
 scope_id TEXT NOT NULL,event_type TEXT NOT NULL,reason_code TEXT,local_turn_id TEXT REFERENCES controller_turns(id),
 small_attributes_json TEXT NOT NULL DEFAULT '{}',occurred_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS controller_events_run ON controller_events(run_id,sequence);
CREATE TABLE IF NOT EXISTS project_preparations(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),planning_run_id TEXT NOT NULL REFERENCES controller_runs(id),
 task_creator_turn_id TEXT REFERENCES controller_turns(id),starting_oid TEXT NOT NULL,canonical_branch TEXT NOT NULL,
 specification_id TEXT NOT NULL REFERENCES specifications(id),specification_hash TEXT NOT NULL,
 profile_hashes_json TEXT NOT NULL,run_configuration_json TEXT NOT NULL,run_configuration_hash TEXT NOT NULL,
 proposal_json TEXT,proposal_hash TEXT,state TEXT NOT NULL CHECK(state IN ('PLANNING','AWAITING_APPROVAL','APPROVED','SUPERSEDED','FAILED')),
 failure_reason TEXT,execution_run_id TEXT REFERENCES controller_runs(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,approved_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_project_preparation ON project_preparations(project_id) WHERE state IN ('PLANNING','AWAITING_APPROVAL');
CREATE TABLE IF NOT EXISTS preparation_run_groups(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preparation_attempts(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),run_group_id TEXT NOT NULL REFERENCES preparation_run_groups(id),
 sequence INTEGER NOT NULL,planning_run_id TEXT REFERENCES controller_runs(id),preparation_id TEXT REFERENCES project_preparations(id),
 state TEXT NOT NULL CHECK(state IN ('STARTED','COMPLETED','FAILED')),failure_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(preparation_id),UNIQUE(run_group_id,sequence));
CREATE INDEX IF NOT EXISTS preparation_attempts_group ON preparation_attempts(run_group_id,sequence);
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
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(5,?)", (now(),))
        _migrate_controller_v6(con)
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(6,?)", (now(),))
        _migrate_controller_v7(con)
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(7,?)", (now(),))
        _migrate_controller_v8(con)
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(8,?)", (now(),))
        _migrate_preparation_v9(con)
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(9,?)", (now(),))
        _migrate_preparation_v10(con)
        con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(10,?)", (now(),))
        return con
    except (OSError, sqlite3.Error) as exc:
        if con is not None:
            con.close()
        raise LedgerError(
            "LEDGER_STORAGE_UNAVAILABLE",
            "Taskledger could not open this repository's local state.",
            details={"path": str(path), "reason": exc.__class__.__name__},
        )


def _migrate_controller_v6(con: sqlite3.Connection) -> None:
    """Add controller identity/usage fields and widen semantic session roles."""
    session_columns = {row[1] for row in con.execute("PRAGMA table_info(controller_sessions)")}
    turn_columns = {row[1] for row in con.execute("PRAGMA table_info(controller_turns)")}
    if "runtime_identity_json" not in session_columns:
        con.execute("ALTER TABLE controller_sessions ADD COLUMN runtime_identity_json TEXT NOT NULL DEFAULT '{}'")
    if "usage_missing" not in turn_columns:
        con.execute("ALTER TABLE controller_turns ADD COLUMN usage_missing INTEGER NOT NULL DEFAULT 0 CHECK(usage_missing IN (0,1))")
    session_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='controller_sessions'"
    ).fetchone()[0]
    if "TASK_CREATOR" not in session_sql:
        con.execute("PRAGMA foreign_keys=OFF")
        try:
            con.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE controller_sessions_v6(
                 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES controller_runs(id),project_id TEXT NOT NULL REFERENCES projects(id),
                 role TEXT NOT NULL CHECK(role IN ('TASK_CREATOR','WORKER','REVIEWER','REQUIREMENT_REVIEWER')),profile TEXT NOT NULL,subject_id TEXT NOT NULL,
                 external_thread_id TEXT NOT NULL,config_hash TEXT NOT NULL,runtime_identity_json TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('ACTIVE','CLOSED','UNCERTAIN')),
                 created_at TEXT NOT NULL,closed_at TEXT);
                INSERT INTO controller_sessions_v6 SELECT id,run_id,project_id,role,profile,subject_id,external_thread_id,config_hash,runtime_identity_json,state,created_at,closed_at FROM controller_sessions;
                CREATE TABLE controller_turns_v6(
                 id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES controller_sessions_v6(id),sequence INTEGER NOT NULL,
                 state TEXT NOT NULL CHECK(state IN ('DISPATCHING','RUNNING','COMPLETED','FAILED','UNCERTAIN')),prompt_kind TEXT NOT NULL,
                 external_turn_id TEXT,result_json TEXT,final_response TEXT,error TEXT,progress_before TEXT,progress_after TEXT,
                 progressed INTEGER CHECK(progressed IN (0,1)),input_tokens INTEGER NOT NULL DEFAULT 0,cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                 output_tokens INTEGER NOT NULL DEFAULT 0,reasoning_tokens INTEGER NOT NULL DEFAULT 0,usage_missing INTEGER NOT NULL DEFAULT 0 CHECK(usage_missing IN (0,1)),
                 started_at TEXT NOT NULL,finished_at TEXT,consumed_at TEXT,UNIQUE(session_id,sequence));
                INSERT INTO controller_turns_v6 SELECT id,session_id,sequence,state,prompt_kind,external_turn_id,result_json,final_response,error,progress_before,progress_after,progressed,input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,usage_missing,started_at,finished_at,consumed_at FROM controller_turns;
                CREATE TABLE controller_usage_events_v6(
                 id TEXT PRIMARY KEY,turn_id TEXT NOT NULL REFERENCES controller_turns_v6(id),external_event_id TEXT NOT NULL,raw_json TEXT NOT NULL,
                 input_tokens INTEGER,cached_input_tokens INTEGER,output_tokens INTEGER,reasoning_tokens INTEGER,created_at TEXT NOT NULL,UNIQUE(turn_id,external_event_id));
                INSERT INTO controller_usage_events_v6 SELECT * FROM controller_usage_events;
                DROP TABLE controller_usage_events;
                DROP TABLE controller_turns;
                DROP TABLE controller_sessions;
                ALTER TABLE controller_sessions_v6 RENAME TO controller_sessions;
                ALTER TABLE controller_turns_v6 RENAME TO controller_turns;
                ALTER TABLE controller_usage_events_v6 RENAME TO controller_usage_events;
                CREATE UNIQUE INDEX one_active_controller_session ON controller_sessions(project_id,role,subject_id) WHERE state='ACTIVE';
                CREATE INDEX controller_turns_open ON controller_turns(state) WHERE state IN ('DISPATCHING','RUNNING','UNCERTAIN');
                CREATE INDEX controller_turns_unconsumed ON controller_turns(session_id,consumed_at) WHERE consumed_at IS NULL;
                COMMIT;
            """)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.execute("PRAGMA foreign_keys=ON")


def _migrate_controller_v7(con: sqlite3.Connection) -> None:
    """Add controller-owned turn metrics/events and explicit usage provenance."""
    turn_columns = {row[1] for row in con.execute("PRAGMA table_info(controller_turns)")}
    additions = {
        "dispatch_reason": "TEXT",
        "prompt_builder_version": "TEXT",
        "controller_payload_bytes": "INTEGER NOT NULL DEFAULT 0",
        "static_assignment_bytes": "INTEGER NOT NULL DEFAULT 0",
        "dynamic_state_bytes": "INTEGER NOT NULL DEFAULT 0",
        "correction_bytes": "INTEGER NOT NULL DEFAULT 0",
        "output_schema_bytes": "INTEGER NOT NULL DEFAULT 0",
        "dynamic_state_hash": "TEXT",
        "context_hashes_json": "TEXT NOT NULL DEFAULT '{}'",
        "response_hash": "TEXT",
        "cache_write_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        "usage_precision": "TEXT NOT NULL DEFAULT 'LEGACY_LAST_USAGE'",
        "usage_before_json": "TEXT",
        "usage_after_json": "TEXT",
        "exact_response_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in turn_columns:
            con.execute(f"ALTER TABLE controller_turns ADD COLUMN {name} {definition}")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS controller_events(
         sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES controller_runs(id),scope_type TEXT NOT NULL,
         scope_id TEXT NOT NULL,event_type TEXT NOT NULL,reason_code TEXT,local_turn_id TEXT REFERENCES controller_turns(id),
         small_attributes_json TEXT NOT NULL DEFAULT '{}',occurred_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS controller_events_run ON controller_events(run_id,sequence);
    """)
    budget_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='controller_budget_grants'"
    ).fetchone()[0]
    if "TASK_CREATOR_TURNS" not in budget_sql:
        con.execute("PRAGMA foreign_keys=OFF")
        try:
            con.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE controller_budget_grants_v7(
                 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES controller_runs(id),kind TEXT NOT NULL CHECK(kind IN ('WORKER_TURNS','REVIEWER_TURNS','TASK_CREATOR_TURNS','TOKENS','ELAPSED_SECONDS')),
                 amount INTEGER NOT NULL CHECK(amount>0),reason TEXT NOT NULL,granted_by_principal_id TEXT NOT NULL REFERENCES principals(id),created_at TEXT NOT NULL);
                INSERT INTO controller_budget_grants_v7 SELECT * FROM controller_budget_grants;
                DROP TABLE controller_budget_grants;
                ALTER TABLE controller_budget_grants_v7 RENAME TO controller_budget_grants;
                COMMIT;
            """)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.execute("PRAGMA foreign_keys=ON")


def _migrate_controller_v8(con: sqlite3.Connection) -> None:
    """Add CLI preparation records and the PREPARATION controller-run mode."""
    run_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='controller_runs'"
    ).fetchone()[0]
    if "PREPARATION" not in run_sql:
        con.execute("PRAGMA foreign_keys=OFF")
        try:
            con.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE controller_runs_v8(
                 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
                 mode TEXT NOT NULL CHECK(mode IN ('ASSIGNMENT','PROJECT','PREPARATION')),
                 state TEXT NOT NULL CHECK(state IN ('RUNNING','PAUSED','COMPLETED','FAILED')),config_json TEXT NOT NULL,
                 pause_reason TEXT,pause_detail TEXT,created_at TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT);
                INSERT INTO controller_runs_v8 SELECT * FROM controller_runs;
                DROP TABLE controller_runs;
                ALTER TABLE controller_runs_v8 RENAME TO controller_runs;
                CREATE UNIQUE INDEX one_active_controller_run ON controller_runs(project_id) WHERE state='RUNNING';
                COMMIT;
            """)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS project_preparations(
         id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),planning_run_id TEXT NOT NULL REFERENCES controller_runs(id),
         task_creator_turn_id TEXT REFERENCES controller_turns(id),starting_oid TEXT NOT NULL,canonical_branch TEXT NOT NULL,
         specification_id TEXT NOT NULL REFERENCES specifications(id),specification_hash TEXT NOT NULL,
         profile_hashes_json TEXT NOT NULL,run_configuration_json TEXT NOT NULL,run_configuration_hash TEXT NOT NULL,
         proposal_json TEXT,proposal_hash TEXT,state TEXT NOT NULL CHECK(state IN ('PLANNING','AWAITING_APPROVAL','APPROVED','SUPERSEDED','FAILED')),
         failure_reason TEXT,execution_run_id TEXT REFERENCES controller_runs(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,approved_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_project_preparation ON project_preparations(project_id) WHERE state IN ('PLANNING','AWAITING_APPROVAL');
    """)


def _migrate_preparation_v9(con: sqlite3.Connection) -> None:
    """Add explicit preparation-attempt lineage without guessing legacy groups."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS preparation_run_groups(
         id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preparation_attempts(
         id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),run_group_id TEXT NOT NULL REFERENCES preparation_run_groups(id),
         sequence INTEGER NOT NULL,planning_run_id TEXT REFERENCES controller_runs(id),preparation_id TEXT REFERENCES project_preparations(id),
         state TEXT NOT NULL CHECK(state IN ('STARTED','COMPLETED','FAILED')),failure_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
         UNIQUE(preparation_id),UNIQUE(run_group_id,sequence));
        CREATE INDEX IF NOT EXISTS preparation_attempts_group ON preparation_attempts(run_group_id,sequence);
    """)


def _migrate_preparation_v10(con: sqlite3.Connection) -> None:
    """Give grouped retries a durable ordering even for early v9 ledgers."""
    columns = {row[1] for row in con.execute("PRAGMA table_info(preparation_attempts)")}
    if "sequence" not in columns:
        con.execute("ALTER TABLE preparation_attempts ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0")
        groups = [row[0] for row in con.execute("SELECT DISTINCT run_group_id FROM preparation_attempts")]
        for group_id in groups:
            rows = con.execute(
                "SELECT id FROM preparation_attempts WHERE run_group_id=? ORDER BY created_at,id", (group_id,)
            ).fetchall()
            for sequence, row in enumerate(rows, start=1):
                con.execute("UPDATE preparation_attempts SET sequence=? WHERE id=?", (sequence, row["id"]))
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS preparation_attempts_group_sequence ON preparation_attempts(run_group_id,sequence)"
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
        from .notifications import publish_committed
        publish_committed(con)
