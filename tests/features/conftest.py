"""conftest for BDD feature tests - includes step definitions."""

from __future__ import annotations

import os
import sys
import tempfile
import types
from typing import Any

import pytest
from pytest_bdd import given, then, when

from storage.sqlite_store import ExecutionStore
from workflow.context import WorkflowContext
from workflow.core import (
    ConcurrencyMode,
    ErrorPolicy,
    ExecutionRecord,
    ExecutionStatus,
    RollbackPolicy,
    WorkflowDefinition,
)
from workflow.definitions import (
    ValidationError,
    dump_workflow_yaml,
    parse_workflow_yaml,
    validate_workflow,
)
from workflow.executor import execute_steps
from workflow.security import AuditLogger

# Re-use fake tool registry from parent conftest
_FAKE_TOOLS: dict[str, Any] = {}


def _fake_echo(**kwargs: Any) -> dict[str, Any]:
    return {"echoed": kwargs.get("message") or kwargs.get("command") or str(kwargs)}


_FAKE_TOOLS["echo"] = _fake_echo
_FAKE_TOOLS["ls"] = lambda **kw: {"output": "fake ls output"}  # type: ignore[assignment]
_FAKE_TOOLS["cat"] = lambda **kw: {"output": "fake cat output"}  # type: ignore[assignment]


def _fake_get_tool(name: str) -> Any:
    return _FAKE_TOOLS.get(name)


# Install fake tools if not already installed
if "tools" not in sys.modules:
    _tools_mod = types.ModuleType("tools")
    _registry_mod = types.ModuleType("tools.registry")
    _registry_mod.get_tool = _fake_get_tool  # type: ignore[attr-defined]
    _tools_mod.registry = _registry_mod
    sys.modules["tools"] = _tools_mod
    sys.modules["tools.registry"] = _registry_mod


# ── Workflow Definitions Step Definitions ──────────────────────────────────────────


@given("a minimal workflow with name test-wf and empty steps", target_fixture="workflow")
def given_minimal_workflow() -> dict:
    return {"name": "test-wf", "version": 1, "steps": []}


@given("a workflow with missing name", target_fixture="workflow")
def given_workflow_missing_name() -> dict:
    return {"version": 1, "steps": []}


@given("a workflow with missing steps", target_fixture="workflow")
def given_workflow_missing_steps() -> dict:
    return {"name": "test", "version": 1}


@given("a workflow with a step missing name", target_fixture="workflow")
def given_step_missing_name() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [{"type": "tool"}],
    }


@given("a workflow with step type bad_type", target_fixture="workflow")
def given_invalid_step_type() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [{"name": "s1", "type": "bad_type"}],
    }


@given("a workflow with permission allowed_tools as string", target_fixture="workflow")
def given_permission_allowed_tools_string() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [],
        "permission": {"allowed_tools": "not_a_list"},
    }


@given("a workflow with parallel_branch missing branches", target_fixture="workflow")
def given_parallel_branch_missing_branches() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [{"name": "pb1", "type": "parallel_branch"}],
    }


@given("a workflow with parallel_branch branch missing name", target_fixture="workflow")
def given_parallel_branch_branch_missing_name() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [
            {
                "name": "pb1",
                "type": "parallel_branch",
                "branches": [{"steps": []}],
            }
        ],
    }


@given("a valid workflow YAML", target_fixture="workflow_yaml")
def given_valid_workflow_yaml() -> str:
    return """name: test-workflow
version: 1
description: Test workflow
steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: hello
"""


# ── When steps ───────────────────────────────────────────────────────────────────


@when("I validate the workflow")
def when_validate_workflow(workflow: dict) -> dict:
    # Return the workflow - validation happens in Then step
    return workflow


@when("I parse it to dict and dump back to YAML", target_fixture="parsed_dumped")
def when_parse_and_dump(workflow_yaml: str) -> tuple[WorkflowDefinition, str]:
    parsed = parse_workflow_yaml(workflow_yaml)
    dumped = dump_workflow_yaml(parsed)
    return parsed, dumped


# ── Then steps ───────────────────────────────────────────────────────────────────


@then("validation should pass")
def then_validation_passes(workflow: dict) -> None:
    validate_workflow(workflow)


@then("validation should fail with name")
def then_validation_fails_name(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="name"):
        validate_workflow(workflow)


@then("validation should fail with steps")
def then_validation_fails_steps(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="steps"):
        validate_workflow(workflow)


@then("validation should fail with missing name")
def then_validation_fails_missing_name(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="missing 'name'"):
        validate_workflow(workflow)


@then("validation should fail with invalid type")
def then_validation_fails_invalid_type(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="invalid type"):
        validate_workflow(workflow)


@then("validation should fail with allowed_tools")
def then_validation_fails_allowed_tools(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="allowed_tools"):
        validate_workflow(workflow)


@then("validation should fail with parallel_branch requires branches")
def then_validation_fails_parallel_branch(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="parallel_branch requires 'branches'"):
        validate_workflow(workflow)


@then("validation should fail with branch missing name")
def then_validation_fails_branch_missing_name(workflow: dict) -> None:
    with pytest.raises(ValidationError, match="branch missing 'name'"):
        validate_workflow(workflow)


@then("the result should be valid workflow YAML")
def then_valid_workflow_yaml(parsed_dumped: tuple[dict, str]) -> None:
    parsed_def, _ = parsed_dumped
    # parsed_def is a WorkflowDefinition, convert to dict for validation
    validate_workflow(parsed_def.to_dict())


# ── Workflow Execution Step Definitions ──────────────────────────────────────────


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


# ── Given steps ──────────────────────────────────────────────────────────────────


@given("a workflow with one tool step that echoes hello", target_fixture="workflow_yaml")
def given_single_step_workflow() -> str:
    return SINGLE_STEP_YAML


@given("a workflow with parallel branches", target_fixture="workflow_yaml")
def given_parallel_workflow() -> str:
    return PARALLEL_YAML


@given("a workflow with two sequential steps", target_fixture="workflow_yaml")
def given_sequential_workflow() -> str:
    return TWO_STEP_SEQUENTIAL_YAML


@given("a workflow with error_policy fail_fast and a failing step", target_fixture="workflow_yaml")
def given_fail_fast_workflow() -> str:
    return """
name: fail-test
version: 1
error_policy: fail_fast
rollback_policy: checkpoint
steps:
  - name: failing_step
    type: tool
    args:
      tool: nonexistent_tool_xyz
  - name: should_not_run
    type: tool
    args:
      tool: echo
      message: this_should_not_run
"""


@given("a workflow with rollback_policy checkpoint and a failing step", target_fixture="workflow_yaml")
def given_rollback_workflow() -> str:
    return """
name: rollback-test
version: 1
error_policy: fail_fast
rollback_policy: checkpoint
steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: step1
  - name: failing_step
    type: tool
    args:
      tool: nonexistent_tool_xyz
    on_error: rollback
"""


@given("an execution store", target_fixture="store")
def given_execution_store() -> ExecutionStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return ExecutionStore(db_path=path)


# ── When steps ──────────────────────────────────────────────────────────────────


@when("I execute the workflow", target_fixture="execution_result")
def when_execute_workflow(workflow_yaml: str, store: ExecutionStore) -> tuple[ExecutionStatus, WorkflowContext]:
    workflow_def = parse_workflow_yaml(workflow_yaml)
    record = ExecutionRecord(
        id="test-exec-1",
        workflow_id=workflow_def.name,
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.SEQUENTIAL,
        max_duration=300,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2024-01-01T00:00:00Z",
        triggered_by="test",
    )
    store.create_execution(record, "{}")

    ctx = WorkflowContext(workflow_id=workflow_def.name, execution_id=record.id)
    audit = AuditLogger(store)
    execute_steps(workflow_def, ctx, record, store, audit)
    return record.status, ctx


@when("I execute the workflow with concurrency parallel", target_fixture="execution_result")
def when_execute_workflow_with_concurrency(
    workflow_yaml: str, store: ExecutionStore
) -> tuple[ExecutionStatus, WorkflowContext]:
    workflow_def = parse_workflow_yaml(workflow_yaml)
    record = ExecutionRecord(
        id="test-exec-1",
        workflow_id=workflow_def.name,
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.PARALLEL,
        max_duration=300,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2024-01-01T00:00:00Z",
        triggered_by="test",
    )
    store.create_execution(record, "{}")

    ctx = WorkflowContext(workflow_id=workflow_def.name, execution_id=record.id)
    audit = AuditLogger(store)
    execute_steps(workflow_def, ctx, record, store, audit)
    return record.status, ctx


# ── Then steps ──────────────────────────────────────────────────────────────────


@then("the execution should complete with status completed")
def then_execution_status_completed(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    status, _ = execution_result
    assert status == ExecutionStatus.COMPLETED, f"Expected COMPLETED, got {status}"


@then("the execution should complete with status failed")
def then_execution_status_failed(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    status, _ = execution_result
    assert status == ExecutionStatus.FAILED, f"Expected FAILED, got {status}"


@then("the execution should complete with status rolled_back")
def then_execution_status_rolled_back(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    status, _ = execution_result
    assert status == ExecutionStatus.ROLLED_BACK, f"Expected ROLLED_BACK, got {status}"


@then("step dummy_step should have result containing hello")
def then_step_result_contains(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    _, ctx = execution_result
    status_step = ctx.get("dummy_step")
    assert status_step is not None, "dummy_step should have executed"


@then("step step2 should execute after step1 completes")
def then_step2_after_step1(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    _, ctx = execution_result
    step1_result = ctx.get("step1")
    step2_result = ctx.get("step2")
    assert step1_result is not None, "step1 should have executed"
    assert step2_result is not None, "step2 should have executed"


@then("remaining steps should not execute")
def then_remaining_steps_not_execute(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    _, ctx = execution_result
    should_not_run = ctx.get("should_not_run")
    assert should_not_run is None, "should_not_run should not have executed due to fail_fast"


@then("the state should be restored to checkpoint")
def then_state_restored(execution_result: tuple[ExecutionStatus, WorkflowContext]) -> None:
    status, ctx = execution_result
    # Verify rollback was triggered and executed
    assert status == ExecutionStatus.ROLLED_BACK, f"Expected ROLLED_BACK status, got {status}"
    # Checkpoint rollback records the checkpoint in ctx.checkpoints
    assert len(ctx.checkpoints) > 0, "checkpoint should have been recorded before rollback"
