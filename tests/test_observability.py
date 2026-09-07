"""Tests for observability/logger: Prometheus metrics label escaping."""

import os
import tempfile

import pytest

from observability.logger import get_prometheus_metrics
from storage.sqlite_store import ExecutionStore


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return ExecutionStore(db_path=path)


class TestPrometheusLabelEscaping:
    def _insert_execution(self, store, exec_id, workflow_id, status):
        store.db.execute(
            "INSERT INTO workflow_executions (id, workflow_id, version, status, context_json, concurrency_mode, error_policy, rollback_policy, started_at, triggered_by, triggered_by_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exec_id, workflow_id, 1, status, "{}", "sequential", "fail_fast", "none", "2024-01-01T00:00:00", "test", "test"),
        )
        store.db.commit()

    def test_double_quote_in_workflow_id_escaped(self, store):
        self._insert_execution(store, "exec-1", 'wf"quote', "completed")
        metrics = get_prometheus_metrics(store)
        label_keys = list(metrics["workflow_executions_total"].keys())
        assert any('wf\\"quote' in k for k in label_keys), f"Got: {label_keys}"

    def test_newline_in_workflow_id_escaped(self, store):
        self._insert_execution(store, "exec-2", "wf\nnewline", "completed")
        metrics = get_prometheus_metrics(store)
        label_keys = list(metrics["workflow_executions_total"].keys())
        assert any("wf\\nnewline" in k for k in label_keys), f"Got: {label_keys}"

    def test_backslash_in_workflow_id_escaped(self, store):
        self._insert_execution(store, "exec-3", "wf\\backslash", "completed")
        metrics = get_prometheus_metrics(store)
        label_keys = list(metrics["workflow_executions_total"].keys())
        assert any("wf\\\\backslash" in k for k in label_keys), f"Got: {label_keys}"

    def test_curly_brace_in_workflow_id_escaped(self, store):
        self._insert_execution(store, "exec-4", "wf{{brace", "completed")
        metrics = get_prometheus_metrics(store)
        label_keys = list(metrics["workflow_executions_total"].keys())
        assert any("wf{{brace" in k for k in label_keys), f"Got: {label_keys}"

    def test_normal_workflow_id_no_escapes(self, store):
        self._insert_execution(store, "exec-5", "normal-workflow", "completed")
        metrics = get_prometheus_metrics(store)
        label_keys = list(metrics["workflow_executions_total"].keys())
        assert any('workflow="normal-workflow"' in k for k in label_keys), f"Got: {label_keys}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
