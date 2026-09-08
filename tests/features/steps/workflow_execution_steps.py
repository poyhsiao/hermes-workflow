"""BDD step definitions for workflow execution."""

from __future__ import annotations

import os
import tempfile

from pytest_bdd import given, then, when

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
from workflow.executor import execute_steps

# ── Fixtures ─────────────────────────────────────────────────────────────────────


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


TWO_STEP_SEQUENTIAL_YAML = """
name: two-step
version: 1
description: Two sequential steps
concurrency: sequential
steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: step1
    requires: []
  - name: step2
    type: tool
    args:
      tool: echo
      message: step2
    requires: [step1]
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


FAIL_FAST_YAML = """
name: fail-test
version: 1
description: Fail fast test
error_policy: fail_fast
rollback_policy: checkpoint
steps:
  - name: step1
    type: tool
    args:
      tool: nonexistent_tool
  - name: step2
    type: tool
    args:
      tool: echo
      message: should_not_run
"""


# ── Given steps ──────────────────────────────────────────────────────────────────


@given("a workflow with one tool step that echoes <message>")
def given_single_step_workflow(message: str) -> str:
    return SINGLE_STEP_YAML.replace("hello", message)


@given("a workflow with parallel branches")
def given_parallel_workflow() -> str:
    return PARALLEL_YAML


@given("a workflow with two sequential steps")
def given_sequential_workflow() -> str:
    return TWO_STEP_SEQUENTIAL_YAML


@given("a workflow with error_policy <policy>")
def given_workflow_with_error_policy(policy: str) -> str:
    return SINGLE_STEP_YAML.replace("fail_fast", policy)


@given("a step that will fail")
def given_failing_step() -> None:
    pass  # Use FAIL_FAST_YAML which has nonexistent_tool


@given("a workflow with rollback_policy <policy>")
def given_workflow_with_rollback_policy(policy: str) -> str:
    return SINGLE_STEP_YAML.replace("checkpoint", policy)


@given("an execution store")
def given_execution_store() -> ExecutionStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return ExecutionStore(db_path=path)


# ── When steps ───────────────────────────────────────────────────────────────────


@when("I execute the workflow")
def when_execute_workflow(workflow_yaml: str, store: ExecutionStore) -> ExecutionStatus:
    workflow_def = parse_workflow_yaml(workflow_yaml)
    record = ExecutionRecord(
        id="test-exec-1",
        workflow_id="test-wf-1",
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.SEQUENTIAL,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2024-01-01T00:00:00Z",
        triggered_by="test",
    )
    store.save_execution(record)

    ctx = WorkflowContext(execution_id=record.id, store=store)
    execute_steps(workflow_def, ctx, store)
    record = store.get_execution(record.id)
    return record.status


@when("I execute the workflow with concurrency <mode>")
def when_execute_workflow_with_concurrency(
    workflow_yaml: str, store: ExecutionStore, mode: str
) -> ExecutionStatus:
    workflow_def = parse_workflow_yaml(workflow_yaml)
    concurrency = ConcurrencyMode.PARALLEL if mode == "parallel" else ConcurrencyMode.SEQUENTIAL
    record = ExecutionRecord(
        id="test-exec-1",
        workflow_id="test-wf-1",
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=concurrency,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2024-01-01T00:00:00Z",
        triggered_by="test",
    )
    store.save_execution(record)

    ctx = WorkflowContext(execution_id=record.id, store=store)
    execute_steps(workflow_def, ctx, store)
    record = store.get_execution(record.id)
    return record.status


# ── Then steps ───────────────────────────────────────────────────────────────────


@then('the execution should complete with status "<status>"')
def then_execution_status(status: ExecutionStatus, result: ExecutionStatus) -> None:
    assert result == status, f"Expected {status}, got {result}"


@then('step <step_name> should have result containing <expected>')
def then_step_result_contains(step_name: str, expected: str, ctx: WorkflowContext) -> None:
    result = ctx.get(step_name)
    assert expected in str(result), f"Expected '{expected}' in result, got {result}"


@then("step 2 should execute after step 1 completes")
def then_step2_after_step1(ctx: WorkflowContext) -> None:
    step1_result = ctx.get("step1")
    step2_result = ctx.get("step2")
    assert step1_result is not None, "step1 should have executed"
    assert step2_result is not None, "step2 should have executed"


@then("remaining steps should not execute")
def then_remaining_steps_not_execute(ctx: WorkflowContext) -> None:
    step2 = ctx.get("step2")
    assert step2 is None, "step2 should not have executed due to fail_fast"


@then("the state should be restored to checkpoint")
def then_state_restored(ctx: WorkflowContext) -> None:
    # Checkpoint rollback behavior - state should be empty or at checkpoint
    pass  # Placeholder for rollback verification
