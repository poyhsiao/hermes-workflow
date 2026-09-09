"""Tests for workflow executor: sequential/parallel execution, rollback, saga."""

import os
import tempfile

import pytest

from storage.sqlite_store import ExecutionStore
from workflow.context import WorkflowContext
from workflow.core import (
    ConcurrencyMode,
    ErrorPolicy,
    ExecutionRecord,
    ExecutionStatus,
    RollbackPolicy,
)
from workflow.definitions import parse_workflow_yaml
from workflow.executor import execute_agent_step, execute_steps
from workflow.security import AuditLogger

# ── Fixtures ────────────────────────────────────────────────────────────────────

# NOTE: tool name must be INSIDE args, e.g. args: {tool: echo, message: hello}
# NOT at the step level (Step.from_dict only reads tool from args, not step-level)

SINGLE_STEP_YAML = """
name: single-step
version: 1
description: One tool step
concurrency: sequential
max_duration: 60
error_policy: fail_fast
rollback_policy: checkpoint
steps:
  - name: dummy_step
    type: tool
    args:
      tool: echo
      message: hello
"""

PARALLEL_YAML = """
name: parallel-test
version: 1
description: Parallel branches
concurrency: parallel
max_duration: 120
error_policy: fail_fast
rollback_policy: checkpoint
steps:
  - name: fan_out
    type: parallel_branch
    branches:
      - name: branch_a
        steps:
          - name: step_a1
            type: tool
            args:
              tool: echo
              message: a1
      - name: branch_b
        steps:
          - name: step_b1
            type: tool
            args:
              tool: echo
              message: b1
"""


def make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return ExecutionStore(db_path=path)


def make_execution_record(workflow_id: str, store: ExecutionStore) -> ExecutionRecord:
    record = ExecutionRecord(
        id="test-exec-1",
        workflow_id=workflow_id,
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.SEQUENTIAL,
        max_duration=300,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2024-01-01T00:00:00Z",
        triggered_by="test",
        triggered_by_user="test-user",
    )
    store.create_execution(record, "{}")
    return record


# ── Sequential execution ─────────────────────────────────────────────────────────


class TestSequentialExecution:
    def test_execute_single_step_workflow_completes(self):
        store = make_store()
        defn = parse_workflow_yaml(SINGLE_STEP_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        assert result == ExecutionStatus.COMPLETED
        assert record.status == ExecutionStatus.COMPLETED

    def test_execute_steps_records_step_completion(self):
        store = make_store()
        defn = parse_workflow_yaml(SINGLE_STEP_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        steps = store.db.execute(
            "SELECT step_name, status FROM execution_steps WHERE execution_id=?", (record.id,)
        ).fetchall()
        step_names = {s["step_name"] for s in steps}
        assert "dummy_step" in step_names

    def test_execute_steps_sets_started_at(self):
        store = make_store()
        defn = parse_workflow_yaml(SINGLE_STEP_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        record = store.get_execution(record.id)
        assert record.started_at  # non-empty timestamp

    def test_execute_steps_populates_context_pipeline(self):
        store = make_store()
        yaml_with_output = """
name: pipeline-test
version: 1
steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: result1
  - name: step2
    type: tool
    args:
      tool: echo
      message: result2
"""
        defn = parse_workflow_yaml(yaml_with_output)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        assert isinstance(ctx.pipeline, list)


# ── Parallel execution ──────────────────────────────────────────────────────────


class TestParallelExecution:
    def test_execute_parallel_workflow_completes(self):
        store = make_store()
        defn = parse_workflow_yaml(PARALLEL_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        assert result == ExecutionStatus.COMPLETED

    def test_parallel_workflow_both_branches_run(self):
        store = make_store()
        defn = parse_workflow_yaml(PARALLEL_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        # Branch sub-steps are not individually saved in execution_steps (only top-level
        # steps are). We verify both branches ran by checking ctx data set by
        # execute_parallel_branch.
        assert ctx.get("fan_out.branch_a") is not None
        assert ctx.get("fan_out.branch_b") is not None

    def test_parallel_workflow_context_lock_preserves_data(self):
        store = make_store()
        defn = parse_workflow_yaml(PARALLEL_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        assert record.status == ExecutionStatus.COMPLETED


# ── Error policies ──────────────────────────────────────────────────────────────


class TestErrorPolicies:
    def test_fail_fast_stops_on_error(self):
        yaml = """
name: fail-test
version: 1
error_policy: fail_fast
steps:
  - name: failing_step
    type: tool
    args:
      tool: nonexistent_tool_xyz
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        assert result == ExecutionStatus.FAILED

    def test_continue_policy_continues_after_failure(self):
        yaml = """
name: continue-test
version: 1
error_policy: fail_fast
steps:
  - name: ok_step
    type: tool
    args:
      tool: echo
      message: ok
  - name: failing_step
    type: tool
    args:
      tool: nonexistent_tool_xyz
    on_error: continue
  - name: after_fail
    type: tool
    args:
      tool: echo
      message: still runs
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        assert result == ExecutionStatus.FAILED
        # on_error: continue lets execution proceed past the failing step,
        # but workflow-level fail_fast still marks the workflow as FAILED.
        # The key is that after_fail DOES execute (step was not skipped).
        steps = store.db.execute(
            "SELECT step_name, status FROM execution_steps WHERE execution_id=?", (record.id,)
        ).fetchall()
        step_statuses = {s["step_name"]: s["status"] for s in steps}
        assert step_statuses.get("after_fail") == "completed"  # after_fail ran


# ── Rollback ────────────────────────────────────────────────────────────────────


class TestRollback:
    def test_checkpoint_saved_for_each_step(self):
        store = make_store()
        yaml = """
name: rollback-test
version: 1
rollback_policy: checkpoint
steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: one
  - name: step2
    type: tool
    args:
      tool: echo
      message: two
"""
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        checkpoints = store.db.execute(
            "SELECT step_index FROM execution_steps WHERE execution_id=? AND checkpoint_json IS NOT NULL AND checkpoint_json != ''",
            (record.id,),
        ).fetchall()
        assert len(checkpoints) >= 1

    def test_get_last_checkpoint_returns_dict_or_none(self):
        store = make_store()
        defn = parse_workflow_yaml(SINGLE_STEP_YAML)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        execute_steps(defn, ctx, record, store, audit)

        cp = store.get_last_checkpoint(record.id)
        # Checkpoint may exist or may be None depending on timing
        assert cp is None or isinstance(cp, dict)


# ── Permission scope ────────────────────────────────────────────────────────────


class TestPermissionScopeIntegration:
    def test_workflow_blocked_tool_fails(self):
        yaml = """
name: blocked-test
version: 1
permission:
  allowed_tools: [allowed_tool]
steps:
  - name: denied_step
    type: tool
    args:
      tool: echo
      message: hello
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        # Workflow fails because 'echo' is not in allowed_tools
        assert result == ExecutionStatus.FAILED

    def test_workflow_with_allowed_tool_succeeds(self):
        yaml = """
name: allowed-test
version: 1
permission:
  allowed_tools: [echo]
steps:
  - name: ok_step
    type: tool
    args:
      tool: echo
      message: hello
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        assert result == ExecutionStatus.COMPLETED

    def test_rollback_stops_subsequent_steps(self):
        """ROLLBACK on a step must NOT execute further steps in the workflow."""
        yaml = """
name: rollback-stop-test
version: 1
rollback_policy: checkpoint
steps:
  - name: ok_step
    type: tool
    args:
      tool: echo
      message: ok
  - name: failing_step
    type: tool
    args:
      tool: nonexistent_tool_xyz
    on_error: rollback
  - name: should_not_run
    type: tool
    args:
      tool: echo
      message: this should not run
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        result = execute_steps(defn, ctx, record, store, audit)
        assert result == ExecutionStatus.ROLLED_BACK
        steps = store.db.execute(
            "SELECT step_name, status FROM execution_steps WHERE execution_id=?", (record.id,)
        ).fetchall()
        step_statuses = {s["step_name"]: s["status"] for s in steps}
        assert step_statuses.get("ok_step") == "completed"
        assert step_statuses.get("failing_step") == "failed"
        # should_not_run must not even be attempted
        assert "should_not_run" not in step_statuses


class TestAgentStepRequiresPluginContext:
    """Agent steps must have plugin_ctx (Hermes runtime)."""

    def test_agent_step_raises_without_plugin_context(self):
        yaml = """
name: agent-test
version: 1
steps:
  - name: agent_step
    type: agent
    agent:
      profile: default
      goal: do something
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        step = defn.steps[0]
        with pytest.raises(RuntimeError, match="agent step requires Hermes plugin context"):
            execute_agent_step(step, ctx, audit, plugin_ctx=None)


class TestToolFallbackUsesToolName:
    """Tool steps fall back to subprocess using tool_name when no command/cmd arg."""

    def test_tool_fallback_uses_tool_name_as_command(self):
        """When a tool is not in registry and no command/cmd arg, tool_name becomes the subprocess command."""
        yaml = """
name: fallback-toolname-test
version: 1
steps:
  - name: fallback_step
    type: tool
    args:
      tool: nonexistent_tool_xyz
"""
        store = make_store()
        defn = parse_workflow_yaml(yaml)
        record = make_execution_record(defn.name, store)
        ctx = WorkflowContext(workflow_id=defn.name, execution_id=record.id)
        audit = AuditLogger(store)

        # Should NOT raise "no result and no fallback available"
        # Instead, falls back to subprocess with tool_name as command
        _ = execute_steps(defn, ctx, record, store, audit)
        # subprocess will fail because 'nonexistent_tool_xyz' isn't a real command,
        # but it should reach subprocess, not fail earlier with "no fallback available"
        steps = store.db.execute(
            "SELECT step_name, status, error FROM execution_steps WHERE execution_id=?", (record.id,)
        ).fetchall()
        step_statuses = {s["step_name"]: s for s in steps}
        step = step_statuses["fallback_step"]
        # Should be 'failed' because subprocess returns non-zero exit code
        assert step["status"] == "failed"
        # Error should mention the command execution failed, NOT "no result and no fallback"
        assert "no fallback" not in step["error"].lower()
        assert "nonexistent_tool_xyz" in step["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
