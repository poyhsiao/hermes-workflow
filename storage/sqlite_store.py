"""SQLite-backed storage for workflow definitions and executions."""

from __future__ import annotations

import sqlite3
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

from workflow.core import ExecutionRecord, ExecutionStatus, WorkflowDefinition, WorkflowEngine, Step, ConcurrencyMode, ErrorPolicy, RollbackPolicy


class ExecutionStore:
    """SQLite store for all workflow persistent data."""

    _init_sql = """
        CREATE TABLE IF NOT EXISTS workflow_definitions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL DEFAULT 1,
            definition_yaml TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT,
            tags TEXT,
            is_template INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS workflow_versions (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            definition_yaml TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            changed_by TEXT,
            change_summary TEXT,
            UNIQUE(workflow_id, version)
        );

        CREATE TABLE IF NOT EXISTS workflow_executions (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            context_json TEXT,
            concurrency_mode TEXT NOT NULL DEFAULT 'sequential',
            max_duration INTEGER DEFAULT 0,
            error_policy TEXT NOT NULL DEFAULT 'fail_fast',
            rollback_policy TEXT NOT NULL DEFAULT 'checkpoint',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            triggered_by TEXT DEFAULT 'manual',
            triggered_by_user TEXT,
            current_step_index INTEGER DEFAULT 0,
            FOREIGN KEY (workflow_id) REFERENCES workflow_definitions(id)
        );

        CREATE TABLE IF NOT EXISTS execution_steps (
            id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            step_name TEXT NOT NULL,
            step_type TEXT NOT NULL,
            input_json TEXT,
            output_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT,
            ended_at TEXT,
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            checkpoint_json TEXT,
            FOREIGN KEY (execution_id) REFERENCES workflow_executions(id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            execution_id TEXT,
            step_id TEXT,
            action TEXT NOT NULL,
            actor TEXT,
            details_json TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES workflow_executions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_executions_status ON workflow_executions(status);
        CREATE INDEX IF NOT EXISTS idx_execution_steps_exec ON execution_steps(execution_id);
        CREATE INDEX IF NOT EXISTS idx_audit_exec ON audit_log(execution_id);
        CREATE INDEX IF NOT EXISTS idx_versions_workflow ON workflow_versions(workflow_id);
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            home = Path.home()
            db_path = str(home / ".hermes" / "workflows.db")
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._ensure_schema()

    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _ensure_schema(self):
        with self._init_lock:
            self.db.executescript(self._init_sql)
            self.db.commit()

    # ── Workflow Definition CRUD ────────────────────────────────────────────────

    def save_definition(self, defn: WorkflowDefinition, created_by: Optional[str] = None) -> str:
        wf_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO workflow_definitions (id, name, version, definition_yaml, created_at, updated_at, created_by, tags, is_template) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (wf_id, defn.name, defn.version, defn.definition_yaml, now, now, created_by, json.dumps(defn.to_dict().get("tags", [])), 0),
        )
        # Also save first version
        self._save_version(wf_id, defn.version, defn.definition_yaml, created_by, "initial version")
        self.db.commit()
        return wf_id

    def update_definition(self, wf_id: str, defn: WorkflowDefinition, changed_by: Optional[str] = None, change_summary: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        new_version = defn.version + 1
        self.db.execute(
            "UPDATE workflow_definitions SET name=?, version=?, definition_yaml=?, updated_at=? WHERE id=?",
            (defn.name, new_version, defn.definition_yaml, now, wf_id),
        )
        self._save_version(wf_id, new_version, defn.definition_yaml, changed_by, change_summary)
        self.db.commit()

    def get_definition(self, name: str) -> Optional[WorkflowDefinition]:
        row = self.db.execute("SELECT * FROM workflow_definitions WHERE name=? ORDER BY version DESC LIMIT 1", (name,)).fetchone()
        if not row:
            return None
        from workflow.definitions import parse_workflow_yaml
        return parse_workflow_yaml(row["definition_yaml"])

    def get_definition_by_id(self, wf_id: str) -> Optional[WorkflowDefinition]:
        row = self.db.execute("SELECT * FROM workflow_definitions WHERE id=?", (wf_id,)).fetchone()
        if not row:
            return None
        from workflow.definitions import parse_workflow_yaml
        return parse_workflow_yaml(row["definition_yaml"])

    def list_definitions(self, include_templates: bool = False) -> list[dict]:
        query = "SELECT id, name, version, created_at, updated_at, is_template FROM workflow_definitions"
        if not include_templates:
            query += " WHERE is_template=0"
        rows = self.db.execute(query + " ORDER BY updated_at DESC").fetchall()
        results = []
        for r in rows:
            d = dict(r)
            # Extract description from YAML
            import yaml
            try:
                parsed = yaml.safe_load(d.get("definition_yaml") or "")
                d["description"] = (parsed or {}).get("description", "")
            except Exception:
                d["description"] = ""
            results.append(d)
        return results

    def delete_definition(self, wf_id: str) -> bool:
        self.db.execute("DELETE FROM workflow_definitions WHERE id=?", (wf_id,))
        self.db.execute("DELETE FROM workflow_versions WHERE workflow_id=?", (wf_id,))
        self.db.commit()
        return True

    def _save_version(self, workflow_id: str, version: int, yaml: str, changed_by: Optional[str], summary: str):
        self.db.execute(
            "INSERT OR REPLACE INTO workflow_versions (id, workflow_id, version, definition_yaml, changed_at, changed_by, change_summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), workflow_id, version, yaml, datetime.now(timezone.utc).isoformat(), changed_by, summary),
        )

    def get_version(self, workflow_id: str, version: int) -> Optional[WorkflowDefinition]:
        row = self.db.execute("SELECT * FROM workflow_versions WHERE workflow_id=? AND version=?", (workflow_id, version)).fetchone()
        if not row:
            return None
        from workflow.definitions import parse_workflow_yaml
        return parse_workflow_yaml(row["definition_yaml"])

    def list_versions(self, workflow_id: str) -> list[dict]:
        rows = self.db.execute("SELECT version, changed_at, changed_by, change_summary FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC", (workflow_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── Execution CRUD ────────────────────────────────────────────────────────

    def create_execution(self, record: ExecutionRecord, context_json: str = "{}") -> str:
        self.db.execute(
            "INSERT INTO workflow_executions (id, workflow_id, version, status, context_json, concurrency_mode, max_duration, error_policy, rollback_policy, started_at, triggered_by, triggered_by_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.id, record.workflow_id, record.version, record.status.value, context_json, record.concurrency_mode.value, record.max_duration, record.error_policy.value, record.rollback_policy.value, record.started_at, record.triggered_by, record.triggered_by_user),
        )
        self.db.commit()
        return record.id

    def save_execution(self, record: ExecutionRecord) -> None:
        self.db.execute(
            "UPDATE workflow_executions SET status=?, ended_at=?, current_step_index=? WHERE id=?",
            (record.status.value, record.ended_at, record.current_step_index, record.id),
        )
        self.db.commit()

    def get_execution(self, exec_id: str) -> Optional[ExecutionRecord]:
        row = self.db.execute("SELECT * FROM workflow_executions WHERE id=?", (exec_id,)).fetchone()
        if not row:
            return None
        return ExecutionRecord(
            id=row["id"],
            workflow_id=row["workflow_id"],
            version=row["version"],
            status=ExecutionStatus(row["status"]),
            concurrency_mode=ConcurrencyMode(row["concurrency_mode"]),
            max_duration=row["max_duration"] or 0,
            error_policy=ErrorPolicy(row["error_policy"]),
            rollback_policy=RollbackPolicy(row["rollback_policy"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            triggered_by=row["triggered_by"],
            triggered_by_user=row["triggered_by_user"],
            current_step_index=row["current_step_index"] or 0,
        )

    def list_executions(self, workflow_id: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM workflow_executions"
        conditions = []
        params = []
        if workflow_id:
            conditions.append("workflow_id=?")
            params.append(workflow_id)
        if status:
            conditions.append("status=?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY started_at DESC LIMIT {limit}"
        rows = self.db.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ── Step CRUD ─────────────────────────────────────────────────────────────

    def save_step(self, exec_id: str, step_index: int, step_name: str, step_type: str, input_json: str = "{}", output_json: str = "", status: str = "pending", error: str = "", retry_count: int = 0, checkpoint_json: str = "") -> str:
        step_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO execution_steps (id, execution_id, step_index, step_name, step_type, input_json, output_json, status, error, retry_count, checkpoint_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (step_id, exec_id, step_index, step_name, step_type, input_json, output_json, status, error, retry_count, checkpoint_json),
        )
        self.db.commit()
        return step_id

    def update_step(self, step_id: str, status: Optional[str] = None, output_json: Optional[str] = None, error: Optional[str] = None, retry_count: Optional[int] = None, checkpoint_json: Optional[str] = None, started_at: Optional[str] = None, ended_at: Optional[str] = None) -> None:
        fields = []
        vals = []
        if status is not None:
            fields.append("status=?")
            vals.append(status)
        if output_json is not None:
            fields.append("output_json=?")
            vals.append(output_json)
        if error is not None:
            fields.append("error=?")
            vals.append(error)
        if retry_count is not None:
            fields.append("retry_count=?")
            vals.append(retry_count)
        if checkpoint_json is not None:
            fields.append("checkpoint_json=?")
            vals.append(checkpoint_json)
        if started_at is not None:
            fields.append("started_at=?")
            vals.append(started_at)
        if ended_at is not None:
            fields.append("ended_at=?")
            vals.append(ended_at)
        if not fields:
            return
        vals.append(step_id)
        self.db.execute(f"UPDATE execution_steps SET {', '.join(fields)} WHERE id=?", vals)
        self.db.commit()

    def get_steps(self, exec_id: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM execution_steps WHERE execution_id=? ORDER BY step_index", (exec_id,)).fetchall()
        return [dict(r) for r in rows]

    def save_checkpoint(self, exec_id: str, step_index: int, checkpoint_data: dict) -> None:
        import json
        self.db.execute(
            "UPDATE execution_steps SET checkpoint_json=? WHERE execution_id=? AND step_index=?",
            (json.dumps(checkpoint_data, default=str), exec_id, step_index),
        )
        self.db.commit()

    def get_last_checkpoint(self, exec_id: str) -> Optional[dict]:
        import json
        row = self.db.execute("SELECT checkpoint_json FROM execution_steps WHERE execution_id=? AND checkpoint_json IS NOT NULL AND checkpoint_json != '' ORDER BY step_index DESC LIMIT 1", (exec_id,)).fetchone()
        if row and row["checkpoint_json"]:
            return json.loads(row["checkpoint_json"])
        return None

    # ── Audit ─────────────────────────────────────────────────────────────────

    def log_audit(self, exec_id: str, action: str, step_id: Optional[str] = None, actor: Optional[str] = None, details: Optional[dict] = None) -> None:
        import getpass
        self.db.execute(
            "INSERT INTO audit_log (id, execution_id, step_id, action, actor, details_json, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), exec_id, step_id, action, actor or getpass.getuser(), json.dumps(details or {}, default=str), datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def get_audit_log(self, exec_id: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM audit_log WHERE execution_id=? ORDER BY timestamp", (exec_id,)).fetchall()
        return [dict(r) for r in rows]
