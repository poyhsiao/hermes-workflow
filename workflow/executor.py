"""Workflow step executor: sequential, parallel, and hybrid concurrency."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from observability.trace import trace_step
from workflow.context import WorkflowContext
from workflow.core import (
    ExecutionRecord,
    ExecutionStatus,
    ParallelBranch,
    Step,
    StepType,
)
from workflow.error_handling import ErrorAction, strategy_for
from workflow.events import (
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_RETRIED,
    STEP_STARTED,
    EventBus,
)
from workflow.security import AuditLogger

if TYPE_CHECKING:
    from storage.sqlite_store import ExecutionStore

# ── Step executors ──────────────────────────────────────────────────────────────


def execute_tool_step(step: Step, ctx: WorkflowContext, audit: AuditLogger) -> Any:
    """Execute a tool step. Tool name is in step.args['command'] or step.args['tool']."""
    # Resolve {{ var }} in args
    resolved_args = ctx.resolve_args(step.args)

    # Determine tool to call
    tool_name = resolved_args.pop("tool", resolved_args.pop("command", None))
    if not tool_name:
        raise ValueError(f"Step '{step.name}': no tool specified")

    # Try Hermes tool registry first, fall back to subprocess for shell tools
    result = None
    try:
        from tools.registry import get_tool
        tool_fn = get_tool(tool_name)
        if tool_fn:
            result = tool_fn(**resolved_args)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug("Tool '%s' not found in registry: %s", tool_name, e)

    if result is None:
        # Fallback: subprocess for shell-like commands
        import re
        import subprocess

        from workflow.security import PermissionScope
        scope = PermissionScope()
        cmd = resolved_args.get("command") or resolved_args.get("cmd", "")
        if not cmd:
            raise RuntimeError(f"Step '{step.name}': tool '{tool_name}' produced no result and no fallback available")
        if scope.is_destructive(cmd):
            raise PermissionError(f"Step '{step.name}': command '{cmd}' is destructive and blocked")
        # Block command substitution operators to prevent injection via $(...) or `...`
        if re.search(r"\$\(|[`]", cmd):
            raise PermissionError(f"Step '{step.name}': command contains disallowed substitution syntax")
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, check=False)
        result = {"stdout": out.stdout, "stderr": out.stderr, "returncode": out.returncode}

    # Store result in context
    ctx.set(step.name, result)
    ctx.push(result)
    audit.log(ctx.execution_id, "step.tool", step_id=step.name, details={"tool": tool_name})
    return result


def execute_agent_step(step: Step, ctx: WorkflowContext, audit: AuditLogger) -> Any:
    """Execute an agent step via delegate_task."""
    resolved_goal = ctx.resolve_var(step.agent_goal or "")
    resolved_profile = ctx.resolve_var(step.agent_profile or "")

    try:
        from tools.delegate_tool import delegate_task
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Step '{step.name}': delegate_task not available in Hermes") from e

    result = delegate_task(
        profile=resolved_profile or "default",
        goal=resolved_goal,
        context=ctx.shared,
    )
    ctx.set(step.name, result)
    ctx.push(result)
    audit.log(ctx.execution_id, "step.agent", step_id=step.name, details={"profile": resolved_profile})
    return result


def execute_parallel_branch(step: Step, ctx: WorkflowContext, audit: AuditLogger) -> dict:
    """Execute a parallel_branch step: all sub-branches run concurrently."""
    if not step.branches:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=len(step.branches)) as executor:
        futures = {
            executor.submit(_execute_branch, branch, ctx, audit): branch
            for branch in step.branches
        }
        for future in as_completed(futures):
            branch = futures[future]
            try:
                branch_result = future.result()
                results[branch.name] = branch_result
                ctx.set(f"{step.name}.{branch.name}", branch_result)
            except Exception as e:  # noqa: BLE001
                results[branch.name] = {"error": str(e)}
                ctx.set(f"{step.name}.{branch.name}", {"error": str(e)})
    return results


def _execute_branch(branch: ParallelBranch, ctx: WorkflowContext, audit: AuditLogger) -> Any:
    results = []
    for s in branch.steps:
        r = _execute_single_step(s, ctx, audit)
        results.append(r)
    return results


def execute_checkpoint_step(step: Step, ctx: WorkflowContext, audit: AuditLogger) -> dict:
    """Save a named checkpoint."""
    metadata = {
        "step_name": step.name,
        "compensate": {"tool": step.compensate.tool, "args": step.compensate.args} if step.compensate else None,
    }
    snap = ctx.checkpoint(step_index=0, metadata=metadata)
    return {"checkpoint_id": len(ctx.checkpoints), "checkpoint": snap}


# ── Single step execution with error handling ───────────────────────────────────


def _execute_single_step(step: Step, ctx: WorkflowContext, audit: AuditLogger) -> Any:
    """Execute one step with error handling, retry, and checkpoint."""
    event_bus = EventBus.get_instance()

    with trace_step(step.name, step.step_type.value, ctx.execution_id):
        # Emit step started
        event_bus.emit(ctx.workflow_id, ctx.execution_id, STEP_STARTED, {"step": step.name, "type": step.step_type.value})

        try:
            if step.step_type == StepType.TOOL:
                result = execute_tool_step(step, ctx, audit)
            elif step.step_type == StepType.AGENT:
                result = execute_agent_step(step, ctx, audit)
            elif step.step_type == StepType.PARALLEL_BRANCH:
                result = execute_parallel_branch(step, ctx, audit)
            elif step.step_type == StepType.CHECKPOINT:
                result = execute_checkpoint_step(step, ctx, audit)
            elif step.step_type == StepType.EVENT:
                ctx.emit_event(step.event_name or step.name, step.args)
                result = {"event": step.event_name, "emitted": True}
            else:
                raise ValueError(f"Unknown step type: {step.step_type}")

            event_bus.emit(ctx.workflow_id, ctx.execution_id, STEP_COMPLETED, {"step": step.name, "result": str(result)[:200]})
            return result

        except Exception as e:
            event_bus.emit(ctx.workflow_id, ctx.execution_id, STEP_FAILED, {"step": step.name, "error": str(e)})
            raise


# ── Main executor ──────────────────────────────────────────────────────────────


def execute_steps(
    definition,
    ctx: WorkflowContext,
    record: ExecutionRecord,
    store: ExecutionStore,
    audit: AuditLogger,
    stop_event: threading.Event | None = None,
    resume_from_step: int = 0,
) -> ExecutionStatus:
    """Execute all steps of a workflow according to dependency graph.

    resume_from_step: skip steps before this index (for checkpoint resume).
    Steps before the index are treated as already completed so dependents unblock normally.
    """

    # Build dependency graph
    step_map = {s.name: s for s in definition.steps}
    completed = set()
    failed_step: str | None = None
    _error: Exception | None = None
    workflow_started_at = datetime.now(timezone.utc)

    # ponytail: resume support — pre-mark earlier steps as completed
    if resume_from_step > 0:
        for s in definition.steps[:resume_from_step]:
            completed.add(s.name)

    # Topological sort — Kahn's algorithm
    in_degree = {s.name: len([d for d in s.requires if d in step_map]) for s in definition.steps}
    # For resume: reduce in_degree for steps whose deps are now in completed
    for s in definition.steps[resume_from_step:]:
        remaining_deps = [d for d in s.requires if d in step_map and d not in completed]
        in_degree[s.name] = len(remaining_deps)
    ready = [s for s in definition.steps[resume_from_step:] if in_degree[s.name] == 0]

    def _should_stop() -> bool:
        if record.status == ExecutionStatus.TERMINATED:
            return True
        if record.status == ExecutionStatus.TERMINATED:
            return True
        return bool(stop_event and stop_event.is_set())

    while ready:
        if _should_stop():
            break

        # ponytail: max_duration enforcement
        if record.max_duration > 0:
            elapsed = (datetime.now(timezone.utc) - workflow_started_at).total_seconds()
            if elapsed > record.max_duration:
                record.status = ExecutionStatus.TERMINATED
                store.save_execution(record)
                audit.log(record.id, "workflow.max_duration_exceeded", details={"elapsed": elapsed, "limit": record.max_duration})
                return ExecutionStatus.TERMINATED

        batch = ready if definition.concurrency.value == "parallel" else [ready[0]]
        ready = ready[len(batch):]

        # Execute batch
        for step in batch:
            if _should_stop():
                break

            # Save step record
            step_id = store.save_step(
                record.id, definition.steps.index(step), step.name, step.step_type.value,
                input_json=json.dumps(step.args, default=str),
            )

            # Checkpoint before step (include compensate so saga rollback can access it later)
            _pre_snap = ctx.checkpoint(
                step_index=definition.steps.index(step),
                metadata={"step": step.name, "pre": True, "compensate": {"tool": step.compensate.tool, "args": step.compensate.args} if step.compensate else None},
            )

            strategy = strategy_for(step)
            attempt = 0
            done = False

            while not done:
                started_at = datetime.now(timezone.utc).isoformat()
                store.update_step(step_id, started_at=started_at)

                try:
                    result = _execute_single_step(step, ctx, audit)
                    # Persist latest checkpoint to DB so saga rollback can retrieve it
                    latest_checkpoint = ctx.checkpoints[-1] if ctx.checkpoints else None
                    ckpt_json = json.dumps(latest_checkpoint, default=str) if latest_checkpoint else ""
                    store.update_step(step_id, status="completed", output_json=json.dumps(result, default=str), checkpoint_json=ckpt_json, ended_at=datetime.now(timezone.utc).isoformat())
                    completed.add(step.name)
                    done = True
                except Exception as e:  # noqa: BLE001
                    action = strategy.decide(step, e)

                    if action == ErrorAction.RETRY and strategy.should_retry(attempt + 1):
                        attempt += 1
                        store.update_step(step_id, retry_count=attempt)
                        event_bus = EventBus.get_instance()
                        event_bus.emit(ctx.workflow_id, ctx.execution_id, STEP_RETRIED, {"step": step.name, "attempt": attempt})
                        time.sleep(strategy.delay(attempt))
                        continue

                    # Non-retry or exhausted
                    store.update_step(step_id, status="failed", error=str(e), ended_at=datetime.now(timezone.utc).isoformat())
                    failed_step = step.name

                    if action == ErrorAction.STOP:
                        record.status = ExecutionStatus.FAILED
                        store.save_execution(record)
                        return ExecutionStatus.FAILED
                    elif action == ErrorAction.SKIP:
                        store.update_step(step_id, status="skipped")
                        completed.add(step.name)
                        done = True
                        # ponytail: in_degree update still runs below for dependents
                        continue
                    elif action == ErrorAction.CONTINUE:
                        completed.add(step.name)
                        done = True
                    elif action == ErrorAction.ROLLBACK:
                        # Trigger rollback
                        _do_rollback(ctx, record, store, audit)
                        return ExecutionStatus.ROLLED_BACK
                    elif action == ErrorAction.DEGRADE:
                        completed.add(step.name)
                        done = True
                    else:
                        record.status = ExecutionStatus.FAILED
                        store.save_execution(record)
                        return ExecutionStatus.FAILED

            # Update in-degrees for dependents (Kahn's algorithm — only enqueue when all deps satisfied)
            for s in definition.steps:
                if step.name in s.requires and s.name not in completed:
                    in_degree[s.name] -= 1
                    # ponytail: only add to ready when ALL dependencies are in completed (avoids parallel violation)
                    if in_degree[s.name] == 0 and all(d in completed for d in s.requires if d in step_map):
                        ready.append(s)

    # All done
    if failed_step:
        record.status = ExecutionStatus.FAILED
    else:
        record.status = ExecutionStatus.COMPLETED
    store.save_execution(record)
    return record.status


def _do_rollback(ctx: WorkflowContext, record: ExecutionRecord, store: ExecutionStore, audit: AuditLogger):
    """Perform rollback to last checkpoint (checkpoint + saga compensate)."""
    if record.rollback_policy.value == "saga":
        # Execute compensate functions in reverse order for completed steps
        steps = store.get_steps(record.id)
        completed_steps = [s for s in reversed(steps) if s["status"] == "completed" and s.get("checkpoint_json")]
        for s in completed_steps:
            import json
            ckpt = json.loads(s["checkpoint_json"])
            compensate = ckpt.get("metadata", {}).get("compensate")
            if compensate:
                step = Step(
                    name=s["step_name"],
                    step_type=StepType.TOOL,
                    args={**(compensate.get("args", {})), "tool": compensate.get("tool", "")},
                )
                try:
                    execute_tool_step(step, ctx, audit)
                    audit.log(record.id, "saga.compensate", step_id=s["step_name"], details={"ok": True})
                except Exception as e:  # noqa: BLE001
                    audit.log(record.id, "saga.compensate.failed", step_id=s["step_name"], details={"error": str(e)})

    checkpoint = store.get_last_checkpoint(record.id)
    if checkpoint:
        ctx.rollback_to(checkpoint)
    record.status = ExecutionStatus.ROLLED_BACK
    store.save_execution(record)
    audit.log(record.id, "rollback", details={"checkpoint": checkpoint, "policy": record.rollback_policy.value})
