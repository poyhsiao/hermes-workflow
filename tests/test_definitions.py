"""Tests for SQLite storage."""

import os
import tempfile

import pytest

from storage.sqlite_store import ExecutionStore
from workflow.core import (
    ConcurrencyMode,
    ErrorPolicy,
    ExecutionRecord,
    ExecutionStatus,
    RollbackPolicy,
)


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    s = ExecutionStore(db_path=path)
    yield s
    os.unlink(path)


def test_save_and_get_definition(store):
    yaml_str = "name: test\nversion: 1\nsteps: []\n"
    from workflow.definitions import parse_workflow_yaml

    defn = parse_workflow_yaml(yaml_str)
    wf_id = store.save_definition(defn, created_by="testuser")
    assert wf_id

    defn2 = store.get_definition("test")
    assert defn2 is not None
    assert defn2.name == "test"


def test_list_definitions(store):
    yaml_str = "name: list-test\nversion: 1\nsteps: []\n"
    from workflow.definitions import parse_workflow_yaml

    store.save_definition(parse_workflow_yaml(yaml_str))
    defs = store.list_definitions()
    assert any(d["name"] == "list-test" for d in defs)


def test_create_and_get_execution(store):
    record = ExecutionRecord(
        id="exec-001",
        workflow_id="wf-001",
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.SEQUENTIAL,
        max_duration=300,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2026-01-01T00:00:00",
    )
    store.create_execution(record)

    record2 = store.get_execution("exec-001")
    assert record2 is not None
    assert record2.status == ExecutionStatus.RUNNING


def test_save_and_update_step(store):
    record = ExecutionRecord(
        id="exec-step",
        workflow_id="wf-step",
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.SEQUENTIAL,
        max_duration=0,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2026-01-01T00:00:00",
    )
    store.create_execution(record)

    step_id = store.save_step("exec-step", 0, "step1", "tool", "{}", "ok", "completed")
    assert step_id

    steps = store.get_steps("exec-step")
    assert len(steps) == 1
    assert steps[0]["step_name"] == "step1"


def test_checkpoint_save_and_restore(store):
    record = ExecutionRecord(
        id="exec-cp",
        workflow_id="wf-cp",
        version=1,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=ConcurrencyMode.SEQUENTIAL,
        max_duration=0,
        error_policy=ErrorPolicy.FAIL_FAST,
        rollback_policy=RollbackPolicy.CHECKPOINT,
        started_at="2026-01-01T00:00:00",
    )
    store.create_execution(record)

    store.save_step("exec-cp", 0, "s1", "tool")
    store.save_checkpoint("exec-cp", 0, {"shared": {"x": 1}, "pipeline": ["out1"]})

    cp = store.get_last_checkpoint("exec-cp")
    assert cp is not None
    assert cp["shared"]["x"] == 1


def test_audit_log(store):
    store.log_audit("exec-audit", "workflow.start", details={"foo": "bar"})
    logs = store.get_audit_log("exec-audit")
    assert len(logs) == 1
    assert logs[0]["action"] == "workflow.start"


def test_versioning(store):
    yaml_str = "name: ver-test\nversion: 1\nsteps: []\n"
    from workflow.definitions import parse_workflow_yaml

    defn = parse_workflow_yaml(yaml_str)
    wf_id = store.save_definition(defn)

    # Update
    defn2 = parse_workflow_yaml(yaml_str.replace("version: 1", "version: 1\ndescription: updated"))
    store.update_definition(wf_id, defn2, changed_by="test", change_summary="add description")

    defn3 = store.get_definition("ver-test")
    assert defn3.description == "updated"


def test_non_string_description_normalized(store):
    from workflow.core import WorkflowDefinition

    defn = WorkflowDefinition.from_dict({"name": "non-string-desc-test", "version": 1, "steps": [], "description": ["foo", "bar"]})
    assert defn.description == ""
    wf_id = store.save_definition(defn)
    defs = store.list_definitions()
    matching = [d for d in defs if d["name"] == "non-string-desc-test"]
    assert len(matching) == 1
    assert matching[0]["description"] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
