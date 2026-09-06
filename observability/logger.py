"""Structured audit logging and Prometheus metrics for workflow executions."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from storage.sqlite_store import ExecutionStore


class AuditLogWriter:
    """Thread-safe writer to the audit_log table."""

    _INSERT = """
        INSERT INTO audit_log (id, execution_id, step_id, action, actor, details_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, store: ExecutionStore):
        self.store = store
        self._lock = threading.Lock()

    def write(
        self,
        execution_id: str,
        action: str,
        step_id: str | None = None,
        actor: str | None = None,
        details: dict | None = None,
    ) -> None:
        import getpass
        import json as _json
        import uuid
        with self._lock:
            self.store.db.execute(
                self._INSERT,
                (str(uuid.uuid4()), execution_id, step_id, action, actor or getpass.getuser(), _json.dumps(details or {}, default=str), datetime.now(timezone.utc).isoformat()),
            )
            self.store.db.commit()


def get_prometheus_metrics(store: ExecutionStore) -> dict:
    """Query execution stats for Prometheus exposition format."""
    metrics: dict[str, Any] = {
        "workflow_executions_total": {},
        "workflow_execution_duration_seconds": {},
        "workflow_active_runs": 0,
        "workflow_retries_total": {},
    }

    rows = store.db.execute("""
        SELECT
            workflow_id,
            status,
            COUNT(*) as count,
            AVG(
                CASE
                    WHEN ended_at IS NOT NULL
                    THEN (julianday(ended_at) - julianday(started_at)) * 86400.0
                    ELSE NULL
                END
            ) as avg_duration
        FROM workflow_executions
        GROUP BY workflow_id, status
    """).fetchall()

    active = 0
    for r in rows:
        wf = r["workflow_id"]
        status = r["status"]
        cnt = r["count"]
        metrics["workflow_executions_total"][f"{{workflow=\"{wf}\",status=\"{status}\"}}"] = cnt
        if status == "running":
            active = cnt
        if r["avg_duration"] is not None:
            metrics["workflow_execution_duration_seconds"][f"{{workflow=\"{wf}\"}}"] = round(r["avg_duration"], 2)

    metrics["workflow_active_runs"] = active

    retry_rows = store.db.execute("""
        SELECT workflow_id, SUM(retry_count) as total_retries
        FROM execution_steps
        GROUP BY execution_id IN (SELECT id FROM workflow_executions WHERE workflow_id IS NOT NULL)
    """).fetchall()

    for r in retry_rows:
        wf = r["workflow_id"] or "unknown"
        metrics["workflow_retries_total"][f"{{workflow=\"{wf}\"}}"] = r["total_retries"] or 0

    return metrics
