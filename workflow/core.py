"""Core workflow engine: definition, engine, and execution state."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from .context import WorkflowContext
from .security import PermissionScope

if TYPE_CHECKING:
    from storage.sqlite_store import ExecutionStore

# ── Enums ────────────────────────────────────────────────────────────────────


class StepType(str, Enum):
    TOOL = "tool"
    AGENT = "agent"
    PARALLEL_BRANCH = "parallel_branch"
    EVENT = "event"
    CHECKPOINT = "checkpoint"


class ExecutionStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    TERMINATED = "terminated"


class ConcurrencyMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    HYBRID = "hybrid"


class ErrorPolicy(str, Enum):
    FAIL_FAST = "fail_fast"
    RETRY = "retry"
    DEGRADE = "degrade"
    MANUAL = "manual"


class RollbackPolicy(str, Enum):
    NONE = "none"
    CHECKPOINT = "checkpoint"
    SAGA = "saga"
    VERSION = "version"


class StepErrorAction(str, Enum):
    STOP = "stop"
    SKIP = "skip"
    CONTINUE = "continue"
    ROLLBACK = "rollback"
    DEGRADE = "degrade"
    RETRY = "retry"


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff: str = "exponential"  # linear | exponential
    backoff_base: float = 2.0
    initial_delay: float = 1.0

    @classmethod
    def from_dict(cls, d: dict | None) -> RetryConfig | None:
        if not d:
            return None
        return cls(**d)


@dataclass
class CompensateConfig:
    tool: str
    args: dict

    @classmethod
    def from_dict(cls, d: dict | None) -> CompensateConfig | None:
        if not d:
            return None
        if not isinstance(d.get("tool"), str) or not d["tool"].strip():
            raise ValueError("compensate.tool must be a non-empty string")
        return cls(**d)


@dataclass
class ParallelBranch:
    name: str
    steps: list[Step]


@dataclass
class Step:
    name: str
    step_type: StepType
    args: dict = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)
    on_error: StepErrorAction = StepErrorAction.STOP
    retry: RetryConfig | None = None
    compensate: CompensateConfig | None = None
    agent_profile: str | None = None
    agent_goal: str | None = None
    branches: list[ParallelBranch] | None = None
    event_name: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Step:
        step_type = StepType(d.get("type", "tool"))
        agent_data = d.get("agent") or {}
        branches_data = d.get("branches") or []
        return cls(
            name=d["name"],
            step_type=step_type,
            args=d.get("args", {}),
            requires=d.get("requires", []),
            on_error=StepErrorAction(d.get("on_error", "stop")),
            retry=RetryConfig.from_dict(d.get("retry")),
            compensate=CompensateConfig.from_dict(d.get("compensate")),
            agent_profile=agent_data.get("profile"),
            agent_goal=agent_data.get("goal"),
            branches=[
                ParallelBranch(name=b["name"], steps=[Step.from_dict(s) for s in b["steps"]]) for b in branches_data
            ],
            event_name=d.get("event"),
        )


@dataclass
class WorkflowDefinition:
    name: str
    version: int = 1
    description: str = ""
    concurrency: ConcurrencyMode = ConcurrencyMode.SEQUENTIAL
    max_duration: int = 0  # 0 = no limit
    error_policy: ErrorPolicy = ErrorPolicy.FAIL_FAST
    rollback_policy: RollbackPolicy = RollbackPolicy.CHECKPOINT
    permission_scope: PermissionScope | None = None  # None = open (all tools allowed)
    context_schema: dict = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    definition_yaml: str = ""

    @classmethod
    def from_dict(cls, d: dict, yaml_str: str = "") -> WorkflowDefinition:
        perm = d.get("permission")
        scope = PermissionScope.from_workflow_definition(perm) if perm else None
        return cls(
            name=d["name"],
            version=d.get("version", 1),
            description=d.get("description", ""),
            concurrency=ConcurrencyMode(d.get("concurrency", "sequential")),
            max_duration=d.get("max_duration", 0),
            error_policy=ErrorPolicy(d.get("error_policy", "fail_fast")),
            rollback_policy=RollbackPolicy(d.get("rollback_policy", "checkpoint")),
            permission_scope=scope,
            context_schema=d.get("context", {}),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            definition_yaml=yaml_str,
        )

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "concurrency": self.concurrency.value,
            "max_duration": self.max_duration,
            "error_policy": self.error_policy.value,
            "rollback_policy": self.rollback_policy.value,
            "context": self.context_schema,
            "steps": [
                {
                    "name": s.name,
                    "type": s.step_type.value,
                    "args": s.args,
                    "requires": s.requires,
                    "on_error": s.on_error.value,
                    "retry": {"max_attempts": s.retry.max_attempts, "backoff": s.retry.backoff} if s.retry else None,
                    "compensate": {"tool": s.compensate.tool, "args": s.compensate.args} if s.compensate else None,
                    "agent": {"profile": s.agent_profile, "goal": s.agent_goal} if s.agent_profile else None,
                    "branches": [
                        {
                            "name": b.name,
                            "steps": [{"name": s.name, "type": s.step_type.value, "args": s.args} for s in b.steps],
                        }
                        for b in s.branches
                    ]
                    if s.branches
                    else None,
                    "event": s.event_name,
                }
                for s in self.steps
            ],
        }
        if self.permission_scope:
            perm = {}
            if self.permission_scope.allowed_tools:
                perm["allowed_tools"] = sorted(self.permission_scope.allowed_tools)
            if self.permission_scope.blocked_tools:
                perm["blocked_tools"] = sorted(self.permission_scope.blocked_tools)
            if perm:
                result["permission"] = perm
        return result


@dataclass
class ExecutionRecord:
    id: str
    workflow_id: str
    version: int
    status: ExecutionStatus
    concurrency_mode: ConcurrencyMode
    max_duration: int
    error_policy: ErrorPolicy
    rollback_policy: RollbackPolicy
    started_at: str
    ended_at: str | None = None
    triggered_by: str = "manual"
    triggered_by_user: str | None = None
    current_step_index: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "status": self.status.value,
            "concurrency_mode": self.concurrency_mode.value,
            "max_duration": self.max_duration,
            "error_policy": self.error_policy.value,
            "rollback_policy": self.rollback_policy.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "triggered_by": self.triggered_by,
            "triggered_by_user": self.triggered_by_user,
            "current_step_index": self.current_step_index,
        }


# ── WorkflowEngine ───────────────────────────────────────────────────────────


class WorkflowEngine:
    """Executes a WorkflowDefinition within a WorkflowContext."""

    def __init__(
        self,
        definition: WorkflowDefinition,
        context: WorkflowContext,
        record: ExecutionRecord,
        store: ExecutionStore,
    ):
        self.definition = definition
        self.context = context
        self.record = record
        self.store = store
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def stop(self):
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def get_status(self) -> ExecutionStatus:
        return self.record.status

    def update_status(self, status: ExecutionStatus):
        with self._lock:
            self.record.status = status
            if status in (
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
                ExecutionStatus.ROLLED_BACK,
                ExecutionStatus.TERMINATED,
            ):
                from datetime import datetime, timezone

                self.record.ended_at = datetime.now(timezone.utc).isoformat()
            self.store.save_execution(self.record)
