"""BDD step definitions for workflow definitions."""

from __future__ import annotations

import pytest
from pytest_bdd import given, then, when

from workflow.definitions import (
    ValidationError,
    dump_workflow_yaml,
    parse_workflow_yaml,
    validate_workflow,
)

# ── Fixtures / helpers ───────────────────────────────────────────────────────────

VALID_MINIMAL = {
    "name": "test-wf",
    "version": 1,
    "steps": [],
}


# ── Given steps ──────────────────────────────────────────────────────────────────


@given("a minimal workflow with name test-wf and empty steps")
def given_minimal_workflow() -> dict:
    return {"name": "test-wf", "version": 1, "steps": []}


@given("a workflow with missing name")
def given_workflow_missing_name() -> dict:
    return {"version": 1, "steps": []}


@given("a workflow with missing steps")
def given_workflow_missing_steps() -> dict:
    return {"name": "test", "version": 1}


@given("a workflow with a step missing name")
def given_step_missing_name() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [{"type": "tool"}],
    }


@given("a workflow with step type bad_type")
def given_invalid_step_type() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [{"name": "s1", "type": "bad_type"}],
    }


@given("a workflow with permission allowed_tools as string")
def given_permission_allowed_tools_string() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [],
        "permission": {"allowed_tools": "not_a_list"},
    }


@given("a workflow with parallel_branch missing branches")
def given_parallel_branch_missing_branches() -> dict:
    return {
        "name": "test",
        "version": 1,
        "steps": [{"name": "pb1", "type": "parallel_branch"}],
    }


@given("a workflow with parallel_branch branch missing name")
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


@given("a valid workflow YAML")
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
def when_validate_workflow(workflow: dict) -> None:
    validate_workflow(workflow)


@when("I parse it to dict and dump back to YAML")
def when_parse_and_dump(workflow_yaml: str) -> tuple[dict, str]:
    parsed = parse_workflow_yaml(workflow_yaml)
    dumped = dump_workflow_yaml(parsed)
    return parsed, dumped


# ── Then steps ───────────────────────────────────────────────────────────────────


@then("validation should pass")
def then_validation_passes(workflow: dict) -> None:
    validate_workflow(workflow)  # Should not raise


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
    parsed, _ = parsed_dumped
    validate_workflow(parsed)  # Should not raise


# ── Scenario Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def workflow(request) -> dict:
    """Dynamic workflow fixture based on scenario context."""
    return getattr(request, "_workflow", VALID_MINIMAL)
