"""Comprehensive tests for workflow definitions: validation, parse/dump roundtrip."""

import pytest

from workflow.definitions import (
    ValidationError,
    dump_workflow_yaml,
    parse_workflow_yaml,
    validate_workflow,
)

VALID_MINIMAL = {
    "name": "test-wf",
    "version": 1,
    "steps": [],
}


class TestValidateWorkflowPermission:
    def test_permission_missing_is_ok(self):
        validate_workflow({**VALID_MINIMAL, "permission": None})

    def test_permission_not_dict_rejects(self):
        with pytest.raises(ValidationError, match="permission"):
            validate_workflow({**VALID_MINIMAL, "permission": "invalid"})

    def test_allowed_tools_not_list_rejects(self):
        with pytest.raises(ValidationError, match="allowed_tools"):
            validate_workflow({**VALID_MINIMAL, "permission": {"allowed_tools": "not_a_list"}})

    def test_blocked_tools_not_list_rejects(self):
        with pytest.raises(ValidationError, match="blocked_tools"):
            validate_workflow({**VALID_MINIMAL, "permission": {"blocked_tools": "also_not_list"}})

    def test_permission_valid(self):
        validate_workflow({**VALID_MINIMAL, "permission": {"allowed_tools": ["a", "b"], "blocked_tools": ["c"]}})


class TestValidateWorkflowSteps:
    def test_steps_not_list_rejects(self):
        with pytest.raises(ValidationError, match="steps"):
            validate_workflow({**VALID_MINIMAL, "steps": "not a list"})

    def test_step_not_dict_rejects(self):
        with pytest.raises(ValidationError, match="Step 0 must be a dict"):
            validate_workflow({**VALID_MINIMAL, "steps": ["not a dict"]})

    def test_step_missing_name_rejects(self):
        with pytest.raises(ValidationError, match="missing 'name'"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"type": "tool"}]})

    def test_invalid_step_type_rejects(self):
        with pytest.raises(ValidationError, match="invalid type"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "type": "bad_type"}]})


class TestValidateParallelBranch:
    def test_parallel_branch_missing_branches_rejects(self):
        with pytest.raises(ValidationError, match="parallel_branch requires 'branches'"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "pb1", "type": "parallel_branch"}]})

    def test_parallel_branch_branch_missing_name_rejects(self):
        with pytest.raises(ValidationError, match="branch missing 'name'"):
            validate_workflow(
                {
                    **VALID_MINIMAL,
                    "steps": [{"name": "pb1", "type": "parallel_branch", "branches": [{"steps": []}]}],
                }
            )

    def test_parallel_branch_branch_missing_steps_rejects(self):
        with pytest.raises(ValidationError, match="branch missing 'name'"):
            validate_workflow(
                {
                    **VALID_MINIMAL,
                    "steps": [{"name": "pb1", "type": "parallel_branch", "branches": [{"name": "b1"}]}],
                }
            )


class TestValidateAgent:
    def test_agent_missing_profile_and_goal_rejects(self):
        with pytest.raises(ValidationError, match="agent requires"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "a1", "type": "agent", "agent": {}}]})

    def test_agent_with_profile_valid(self):
        validate_workflow(
            {
                **VALID_MINIMAL,
                "steps": [{"name": "a1", "type": "agent", "agent": {"profile": "coder"}}],
            }
        )

    def test_agent_with_goal_valid(self):
        validate_workflow(
            {
                **VALID_MINIMAL,
                "steps": [{"name": "a1", "type": "agent", "agent": {"goal": "fix the bug"}}],
            }
        )


class TestValidateCompensate:
    def test_compensate_not_dict_rejects(self):
        with pytest.raises(ValidationError, match="'compensate' must be a dict"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "compensate": "not_dict"}]})

    def test_compensate_missing_tool_rejects(self):
        with pytest.raises(ValidationError, match="compensate.tool"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "compensate": {"args": {}}}]})

    def test_compensate_empty_tool_rejects(self):
        with pytest.raises(ValidationError, match="compensate.tool"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "compensate": {"tool": "  ", "args": {}}}]})

    def test_compensate_missing_args_rejects(self):
        with pytest.raises(ValidationError, match="compensate.args"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "compensate": {"tool": "undo"}}]})

    def test_compensate_valid(self):
        validate_workflow(
            {
                **VALID_MINIMAL,
                "steps": [{"name": "s1", "compensate": {"tool": "undo", "args": {"undo_action": "delete"}}}],
            }
        )


class TestValidateRetry:
    def test_retry_not_dict_rejects(self):
        with pytest.raises(ValidationError, match="'retry' must be a dict"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "retry": "not_dict"}]})

    def test_retry_max_attempts_not_positive_int_rejects(self):
        with pytest.raises(ValidationError, match="retry.max_attempts"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "retry": {"max_attempts": 0}}]})

    def test_retry_max_attempts_negative_rejects(self):
        with pytest.raises(ValidationError, match="retry.max_attempts"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "retry": {"max_attempts": -1}}]})

    def test_retry_max_attempts_string_rejects(self):
        with pytest.raises(ValidationError, match="retry.max_attempts"):
            validate_workflow({**VALID_MINIMAL, "steps": [{"name": "s1", "retry": {"max_attempts": "three"}}]})


class TestParseInvalidYAML:
    def test_invalid_yaml_syntax_raises(self):
        with pytest.raises(ValidationError, match="Invalid YAML"):
            parse_workflow_yaml("name: test\n: invalid\n")


class TestDumpWorkflowYAML:
    def test_dump_roundtrip(self):
        yaml_str = """
name: roundtrip-test
version: 1
description: Test roundtrip
concurrency: parallel
max_duration: 120
error_policy: fail_fast
rollback_policy: checkpoint
permission:
  allowed_tools:
    - echo
steps:
  - name: step1
    type: tool
    tool: echo
    args:
      msg: hello
"""
        defn = parse_workflow_yaml(yaml_str)
        dumped = dump_workflow_yaml(defn)
        # Should be valid YAML
        defn2 = parse_workflow_yaml(dumped)
        assert defn2.name == "roundtrip-test"
        assert defn2.description == "Test roundtrip"
        assert defn2.concurrency.value == "parallel"
        assert len(defn2.steps) == 1
        assert defn2.steps[0].name == "step1"

    def test_dump_parallel_branch_workflow(self):
        yaml_str = """
name: parallel-dump
version: 1
steps:
  - name: fan_out
    type: parallel_branch
    branches:
      - name: branch_a
        steps:
          - name: a1
            type: tool
            tool: echo
            args:
              msg: a
      - name: branch_b
        steps:
          - name: b1
            type: tool
            tool: echo
            args:
              msg: b
"""
        defn = parse_workflow_yaml(yaml_str)
        dumped = dump_workflow_yaml(defn)
        defn2 = parse_workflow_yaml(dumped)
        assert defn2.steps[0].name == "fan_out"
        assert defn2.steps[0].step_type.value == "parallel_branch"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
