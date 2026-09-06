"""Tests for workflow engine: YAML parsing, engine execution, rollback."""


import pytest

from workflow.context import WorkflowContext
from workflow.core import (
    ConcurrencyMode,
    StepType,
)
from workflow.definitions import ValidationError, parse_workflow_yaml, validate_workflow
from workflow.error_handling import RetryConfig, RetryOnError

YAML_VALID = """
name: test-workflow
version: 1
description: Test workflow
concurrency: sequential
max_duration: 300
error_policy: fail_fast
rollback_policy: checkpoint

steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: "hello"
    requires: []
    on_error: stop

  - name: step2
    type: tool
    args:
      tool: echo
      message: "world"
    requires: [step1]
    on_error: skip
"""

YAML_WITH_AGENT = """
name: agent-workflow
version: 1
steps:
  - name: analyze
    type: agent
    agent:
      profile: analyst
      goal: "Analyze this code"
"""

YAML_PARALLEL = """
name: parallel-test
version: 1
concurrency: parallel
steps:
  - name: task1
    type: tool
    args:
      tool: echo
      message: "one"
  - name: task2
    type: tool
    args:
      tool: echo
      message: "two"
"""


def test_parse_valid_yaml():
    defn = parse_workflow_yaml(YAML_VALID)
    assert defn.name == "test-workflow"
    assert defn.version == 1
    assert defn.concurrency == ConcurrencyMode.SEQUENTIAL
    assert len(defn.steps) == 2
    assert defn.steps[0].name == "step1"
    assert defn.steps[1].name == "step2"


def test_parse_with_agent():
    defn = parse_workflow_yaml(YAML_WITH_AGENT)
    assert defn.steps[0].step_type == StepType.AGENT
    assert defn.steps[0].agent_profile == "analyst"


def test_parse_parallel():
    defn = parse_workflow_yaml(YAML_PARALLEL)
    assert defn.concurrency == ConcurrencyMode.PARALLEL


def test_validate_missing_name():
    with pytest.raises(ValidationError, match="name"):
        validate_workflow({"steps": []})


def test_validate_duplicate_step_name():
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_workflow({
            "name": "dup",
            "steps": [
                {"name": "a", "type": "tool"},
                {"name": "a", "type": "tool"},
            ],
        })


def test_validate_unknown_requires():
    with pytest.raises(ValidationError, match="requires unknown"):
        validate_workflow({
            "name": "bad-dep",
            "steps": [
                {"name": "a", "type": "tool", "requires": ["nonexistent"]},
            ],
        })


def test_workflow_context_shared():
    ctx = WorkflowContext(workflow_id="wf1", execution_id="ex1")
    ctx.set("name", "Alice")
    assert ctx.get("name") == "Alice"
    assert ctx.get("missing", "default") == "default"


def test_workflow_context_resolve_vars():
    ctx = WorkflowContext(workflow_id="wf1", execution_id="ex1")
    ctx.set("greeting", "Hello")
    ctx.set("who", "World")
    resolved = ctx.resolve_var("{{ greeting }}, {{ who }}!")
    assert resolved == "Hello, World!"


def test_workflow_context_pipeline():
    ctx = WorkflowContext(workflow_id="wf1", execution_id="ex1")
    ctx.push("result1")
    ctx.push("result2")
    assert ctx.last_output() == "result2"
    assert ctx.pipeline == ["result1", "result2"]


def test_workflow_context_checkpoint_and_rollback():
    ctx = WorkflowContext(workflow_id="wf1", execution_id="ex1")
    ctx.set("counter", 1)
    ctx.push("out1")
    snap1 = ctx.checkpoint(step_index=0, metadata={"label": "after_step1"})
    ctx.set("counter", 2)
    ctx.push("out2")
    snap2 = ctx.checkpoint(step_index=1, metadata={"label": "after_step2"})

    # Rollback to snap1
    ctx.rollback_to(snap1)
    assert ctx.get("counter") == 1
    assert ctx.pipeline == ["out1"]
    assert ctx.checkpoints == [snap1]  # snap2 removed


def test_retry_config_defaults():
    cfg = RetryConfig()
    assert cfg.max_attempts == 3
    assert cfg.backoff == "exponential"
    assert cfg.backoff_base == 2.0
    assert cfg.initial_delay == 1.0


def test_retry_backoff_exponential():
    cfg = RetryConfig(initial_delay=1.0, backoff_base=2.0)
    s = RetryOnError(cfg)
    assert s.delay(1) == 1.0
    assert s.delay(2) == 2.0
    assert s.delay(3) == 4.0


def test_retry_backoff_linear():
    cfg = RetryConfig(initial_delay=1.0, backoff="linear")
    s = RetryOnError(cfg)
    assert s.delay(1) == 1.0
    assert s.delay(2) == 2.0
    assert s.delay(3) == 3.0


def test_workflow_definition_roundtrip():
    defn = parse_workflow_yaml(YAML_VALID)
    d = defn.to_dict()
    assert d["name"] == "test-workflow"
    assert len(d["steps"]) == 2
    assert d["steps"][0]["type"] == "tool"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
