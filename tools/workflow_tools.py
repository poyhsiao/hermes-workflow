"""Hermes tools exposed by the workflow plugin."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.sqlite_store import ExecutionStore
from workflow.context import WorkflowContext as WfCtx
from workflow.core import (
    ExecutionRecord,
    ExecutionStatus,
    WorkflowDefinition,
    WorkflowEngine,
)
from workflow.definitions import dump_workflow_yaml, parse_workflow_yaml
from workflow.executor import execute_steps
from workflow.security import AuditLogger
from workflow.versioned_store import VersionedStore

# Global store + running engines (thread-safe)
_store: ExecutionStore | None = None
_engines: dict[str, WorkflowEngine] = {}
_engines_lock = threading.Lock()

# Module-level plugin context — set during register(), used for dispatch_tool in agent steps
_plugin_ctx: Any = None


def _set_plugin_ctx(ctx) -> None:
    """Store plugin context at registration time for use in agent steps."""
    global _plugin_ctx
    _plugin_ctx = ctx


def _get_store() -> ExecutionStore:
    global _store
    if _store is None:
        _store = ExecutionStore()
    return _store


_VALID_CONTEXT_TYPES: set = {str, int, float, bool, list, dict}


def _validate_context_arg(key: str, value: Any, allowed_keys: set[str]) -> tuple[bool, str]:
    """Validate a single context arg against schema. Returns (ok, error_msg)."""
    if key not in allowed_keys:
        return False, f"Unknown context key '{key}' — not defined in workflow context_schema"
    # Reject non-serializable or nested callable values
    if isinstance(value, type):
        return False, f"context key '{key}': type objects not allowed"
    if not isinstance(value, (type(None), str, int, float, bool, list, dict)):
        return (
            False,
            f"context key '{key}': type {type(value).__name__} not allowed (must be str|int|float|bool|list|dict)",
        )
    return True, ""


def workflow_run(
    name: str,
    args: dict | None = None,
    context_overrides: dict | None = None,
    triggered_by: str = "tool",
    triggered_by_user: str | None = None,
    **kwargs,
) -> dict:
    """Run a named workflow with given args. Returns execution_id immediately."""
    store = _get_store()
    defn = store.get_definition(name)
    if not defn:
        return {"ok": False, "error": f"Workflow '{name}' not found. Use 'workflow_define' to create it."}

    # Merge context_overrides (schema param) with args; both validated against context_schema
    args = args or {}
    context_overrides = context_overrides or {}
    merged = {**args, **context_overrides}
    exec_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Build allowed keys from context_schema; reject unknown keys (YAML-only fields)
    allowed_keys = set(defn.context_schema.keys())
    for key, val in merged.items():
        ok, err = _validate_context_arg(key, val, allowed_keys)
        if not ok:
            return {"ok": False, "error": err}
    # Merge validated args into context schema
    context = dict(defn.context_schema)
    context.update(merged)

    record = ExecutionRecord(
        id=exec_id,
        workflow_id=name,
        version=defn.version,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=defn.concurrency,
        max_duration=defn.max_duration,
        error_policy=defn.error_policy,
        rollback_policy=defn.rollback_policy,
        started_at=now,
        triggered_by=triggered_by,
        triggered_by_user=triggered_by_user,
    )

    ctx = WfCtx(workflow_id=name, execution_id=exec_id)
    for k, v in context.items():
        ctx.set(k, v)

    store.create_execution(record, ctx.to_json())

    engine = WorkflowEngine(defn, ctx, record, store)
    audit = AuditLogger(store)
    audit.log(exec_id, "workflow_run.started", details={"name": name, "args": args})

    with _engines_lock:
        _engines[exec_id] = engine

    # Run in background thread
    def _run():
        try:
            execute_steps(defn, ctx, record, store, audit, stop_event=engine._stop_event, plugin_ctx=_plugin_ctx)
        finally:
            with _engines_lock:
                _engines.pop(exec_id, None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return {"ok": True, "execution_id": exec_id, "status": ExecutionStatus.RUNNING.value, "name": name}


def workflow_stop(execution_id: str) -> dict:
    """Stop a running workflow execution."""
    with _engines_lock:
        engine = _engines.get(execution_id)
    if not engine:
        return {"ok": False, "error": f"No running execution '{execution_id}'"}
    engine.stop()
    engine.update_status(ExecutionStatus.TERMINATED)
    return {"ok": True, "execution_id": execution_id, "status": ExecutionStatus.TERMINATED.value}


def workflow_status(execution_id: str) -> dict:
    """Get status of a workflow execution."""
    store = _get_store()
    record = store.get_execution(execution_id)
    if not record:
        return {"ok": False, "error": f"Execution '{execution_id}' not found"}
    steps = store.get_steps(execution_id)
    return {
        "ok": True,
        "execution_id": execution_id,
        "workflow_id": record.workflow_id,
        "version": record.version,
        "status": record.status.value,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "steps": [
            {"index": s["step_index"], "name": s["step_name"], "type": s["step_type"], "status": s["status"]}
            for s in steps
        ],
    }


def workflow_define(name: str, yaml: str, created_by: str | None = None) -> dict:
    """Define or update a workflow from YAML content."""
    store = _get_store()
    try:
        defn = parse_workflow_yaml(yaml)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Invalid YAML: {e}"}

    defn.name = name
    vs = VersionedStore(store)
    wf_id = store.db.execute("SELECT id FROM workflow_definitions WHERE name=?", (name,)).fetchone()
    if wf_id:
        try:
            vs.save(defn, created_by, "updated via workflow_define")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Failed to update workflow: {e}"}
        return {"ok": True, "name": name, "version": defn.version + 1, "updated": True}
    else:
        try:
            new_id = store.save_definition(defn, created_by)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Failed to save workflow: {e}"}
        return {"ok": True, "name": name, "version": defn.version, "id": new_id, "created": True}


def workflow_delete(name: str) -> dict:
    """Delete a workflow definition."""
    store = _get_store()
    row = store.db.execute("SELECT id FROM workflow_definitions WHERE name=?", (name,)).fetchone()
    if not row:
        return {"ok": False, "error": f"Workflow '{name}' not found"}
    try:
        store.delete_definition(row["id"])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to delete workflow: {e}"}
    return {"ok": True, "deleted": name}


def workflow_list() -> dict:
    """List all workflow definitions."""
    store = _get_store()
    defs = store.list_definitions()
    return {"ok": True, "workflows": defs}


def workflow_show(name: str) -> dict:
    """Show the YAML definition of a named workflow."""
    store = _get_store()
    defn = store.get_definition(name)
    if not defn:
        return {"ok": False, "error": f"Workflow '{name}' not found"}
    return {"ok": True, "name": name, "yaml": dump_workflow_yaml(defn)}


def workflow_history(workflow_name: str | None = None, limit: int = 50) -> dict:
    """Show execution history."""
    store = _get_store()
    wf_id = None
    if workflow_name:
        row = store.db.execute("SELECT id FROM workflow_definitions WHERE name=?", (workflow_name,)).fetchone()
        if row:
            wf_id = row["id"]
        else:
            return {"ok": False, "error": f"Workflow '{workflow_name}' not found"}
    rows = store.list_executions(workflow_id=wf_id, limit=limit)
    return {"ok": True, "executions": rows}


def workflow_rollback(
    execution_id: str,
    to_version: int | None = None,
    triggered_by: str = "tool",
    triggered_by_user: str | None = None,
) -> dict:
    """
    Rollback an execution to its last checkpoint, or to a specific definition version.

    to_version: if provided, rollback the workflow definition to that version and re-run.
    Otherwise, re-run from the last checkpoint.
    """
    store = _get_store()
    record = store.get_execution(execution_id)
    if not record:
        return {"ok": False, "error": f"Execution '{execution_id}' not found"}

    vs = VersionedStore(store)
    defn: WorkflowDefinition | None = None
    checkpoint = None

    if to_version is not None:
        # Version rollback: restore old definition as new version, then re-run
        old_defn = vs.get(record.workflow_id, to_version)
        if not old_defn:
            return {"ok": False, "error": f"Version {to_version} not found for '{record.workflow_id}'"}
        new_defn = vs.rollback_definition(record.workflow_id, to_version, changed_by=triggered_by_user)
        if not new_defn:
            return {"ok": False, "error": f"Rollback failed for '{record.workflow_id}'"}
        defn = new_defn
        checkpoint = None
    else:
        # Checkpoint rollback: re-run from last checkpoint
        defn = store.get_definition(record.workflow_id)
        if not defn:
            return {"ok": False, "error": f"Workflow '{record.workflow_id}' not found"}
        checkpoint = store.get_last_checkpoint(execution_id)
        if not checkpoint:
            return {"ok": False, "error": "No checkpoint found for this execution"}

    # Create new execution with restored context
    import uuid

    new_exec_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    new_record = ExecutionRecord(
        id=new_exec_id,
        workflow_id=record.workflow_id,
        version=defn.version,
        status=ExecutionStatus.RUNNING,
        concurrency_mode=defn.concurrency,
        max_duration=defn.max_duration,
        error_policy=defn.error_policy,
        rollback_policy=defn.rollback_policy,
        started_at=now,
        triggered_by=triggered_by,
        triggered_by_user=triggered_by_user,
    )

    # Restore context from checkpoint or start fresh
    if checkpoint:
        ctx = WfCtx(
            workflow_id=record.workflow_id,
            execution_id=new_exec_id,
            shared=dict(checkpoint.get("shared", {})),
            pipeline=list(checkpoint.get("pipeline", [])),
            events=list(checkpoint.get("events", [])),
        )
    else:
        ctx = WfCtx(workflow_id=record.workflow_id, execution_id=new_exec_id)

    try:
        store.create_execution(new_record, ctx.to_json())

        audit = AuditLogger(store)
        audit.log(
            new_exec_id,
            "workflow.rollback",
            details={
                "from_execution": execution_id,
                "checkpoint_restored": checkpoint is not None,
                "version": to_version,
            },
        )

        engine = WorkflowEngine(defn, ctx, new_record, store)
        with _engines_lock:
            _engines[new_exec_id] = engine

        def _run():
            try:
                # ponytail: resume from checkpoint step_index (skip already-completed steps)
                from workflow.executor import execute_steps

                step_offset = checkpoint.get("step_index", 0) if checkpoint else 0
                ctx.checkpoints.clear()  # fresh checkpoint chain for this run
                execute_steps(
                    defn,
                    ctx,
                    new_record,
                    store,
                    audit,
                    stop_event=engine._stop_event,
                    resume_from_step=step_offset,
                    plugin_ctx=_plugin_ctx,
                )
            finally:
                with _engines_lock:
                    _engines.pop(new_exec_id, None)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return {
            "ok": True,
            "execution_id": new_exec_id,
            "status": ExecutionStatus.RUNNING.value,
            "name": record.workflow_id,
            "restored_from": execution_id,
            "version": defn.version,
            "checkpoint_restored": checkpoint is not None,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Rollback execution failed: {e}"}


def workflow_diff(name: str, v1: int, v2: int) -> dict:
    """Compare two versions of a workflow definition."""
    store = _get_store()
    vs = VersionedStore(store)
    return vs.diff(name, v1, v2)


def workflow_export(name: str, format: str = "yaml") -> dict:
    """Export a workflow definition as YAML or JSON."""
    store = _get_store()
    defn = store.get_definition(name)
    if not defn:
        return {"ok": False, "error": f"Workflow '{name}' not found"}
    if format == "json":
        return {"ok": True, "name": name, "format": "json", "data": defn.to_dict()}
    return {"ok": True, "name": name, "format": "yaml", "yaml": dump_workflow_yaml(defn)}


def workflow_import(yaml: str, as_template: bool = False) -> dict:
    """Import a workflow from YAML content."""
    try:
        defn = parse_workflow_yaml(yaml)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Invalid YAML: {e}"}
    store = _get_store()
    vs = VersionedStore(store)
    try:
        vs.save(defn)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to import workflow: {e}"}
    return {"ok": True, "name": defn.name, "version": defn.version, "imported": True}


def workflow_suggest(context_messages: list[dict] | None = None, limit: int = 3) -> dict:
    """Suggest workflows based on recent conversation messages or global limit.

    Called two ways:
    - From dispatcher: context_messages=list of recent messages (backward compat)
    - From Hermes schema: limit=N (returns top N suggestions)
    """
    store = _get_store()
    defs = store.list_definitions()
    suggestions = []
    if context_messages:
        # Backward compat: keyword-match against conversation context
        context_text = " ".join(
            m.get("content", "") if isinstance(m, dict) else str(m) for m in context_messages[-5:]
        ).lower()
        for d in defs:
            name = d["name"].lower()
            if name in context_text or any(word in context_text for word in name.split("_")):
                suggestions.append({"name": d["name"], "reason": f"'{name}' matches recent conversation"})
    else:
        # Hermes schema path: return top-N by name order
        for d in defs[:limit]:
            suggestions.append({"name": d["name"], "reason": "top workflow"})
    return {"ok": True, "suggestions": suggestions[:limit]}


def workflow_metrics(workflow_name: str | None = None) -> dict:
    """Return Prometheus-format metrics for workflow executions.

    Args:
        workflow_name: filter metrics to a specific workflow (CLI path)
    """
    from observability.logger import get_prometheus_metrics

    store = _get_store()
    metrics = get_prometheus_metrics(store)
    if workflow_name:
        return {
            "ok": True,
            "workflow": workflow_name,
            "metrics": {k: v for k, v in metrics.items() if workflow_name in k},
        }
    return {"ok": True, "metrics": metrics}


def workflow_template_save(name: str, yaml_content: str, description: str = "", tags: list[str] | None = None) -> dict:
    """Save a workflow as a reusable template."""
    from storage.templates import TemplateRegistry

    try:
        path = TemplateRegistry().save(name, yaml_content, description, tags)
        return {"ok": True, "name": name, "path": path}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def workflow_template_list() -> dict:
    """List all saved templates."""
    from storage.templates import TemplateRegistry

    try:
        templates = TemplateRegistry().list()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to list templates: {e}"}
    return {"ok": True, "templates": templates}


def workflow_template_load(name: str) -> dict:
    """Load a template's YAML content."""
    from storage.templates import TemplateRegistry

    try:
        content = TemplateRegistry().load(name)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to load template: {e}"}
    if content is None:
        return {"ok": False, "error": f"Template '{name}' not found"}
    return {"ok": True, "name": name, "yaml": content}


def workflow_template_delete(name: str) -> dict:
    """Delete a saved template."""
    from storage.templates import TemplateRegistry

    try:
        removed = TemplateRegistry().delete(name)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to delete template: {e}"}
    if not removed:
        return {"ok": False, "error": f"Template '{name}' not found"}
    return {"ok": True, "name": name, "deleted": True}
