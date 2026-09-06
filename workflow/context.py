"""Workflow execution context: shared state + pipeline output."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional
import json


@dataclass
class WorkflowContext:
    """Mutable execution context shared across all steps in a workflow run."""

    workflow_id: str
    execution_id: str
    shared: dict[str, Any] = field(default_factory=dict)
    pipeline: list[Any] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def set(self, key: str, value: Any) -> None:
        self.shared[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.shared.get(key, default)

    def push(self, value: Any) -> None:
        """Append to pipeline (pipe output from previous step)."""
        self.pipeline.append(value)

    def pop(self) -> Any:
        return self.pipeline.pop() if self.pipeline else None

    def last_output(self) -> Any:
        return self.pipeline[-1] if self.pipeline else None

    def emit_event(self, name: str, payload: Any = None) -> None:
        self.events.append({"name": name, "payload": payload})

    def checkpoint(self, step_index: int, metadata: Optional[dict] = None) -> dict:
        snap = {
            "step_index": step_index,
            "shared": copy.deepcopy(self.shared),
            "pipeline": list(self.pipeline),
            "events": list(self.events),
            "metadata": metadata or {},
        }
        self.checkpoints.append(snap)
        return snap

    def rollback_to(self, checkpoint: dict) -> None:
        self.shared = copy.deepcopy(checkpoint.get("shared", {}))
        self.pipeline = list(checkpoint.get("pipeline", []))
        self.events = list(checkpoint.get("events", []))
        # Trim checkpoints after the rollback point
        idx = self.checkpoints.index(checkpoint) if checkpoint in self.checkpoints else -1
        if idx >= 0:
            self.checkpoints = self.checkpoints[: idx + 1]

    def resolve_var(self, template: str) -> str:
        """Simple {{ var }} substitution from shared context."""
        if not isinstance(template, str):
            return template
        result = template
        for key, val in self.shared.items():
            placeholder = "{{ " + key + " }}"
            if placeholder in result:
                result = result.replace(placeholder, str(val))
        return result

    def resolve_args(self, args: dict) -> dict:
        """Resolve {{ var }} placeholders in all string values of args dict."""
        resolved = {}
        for k, v in args.items():
            if isinstance(v, str):
                resolved[k] = self.resolve_var(v)
            elif isinstance(v, dict):
                resolved[k] = self.resolve_args(v)
            elif isinstance(v, list):
                resolved[k] = [self.resolve_var(i) if isinstance(i, str) else i for i in v]
            else:
                resolved[k] = v
        return resolved

    def to_json(self) -> str:
        return json.dumps({
            "workflow_id": self.workflow_id,
            "execution_id": self.execution_id,
            "shared": self.shared,
            "pipeline": self.pipeline,
            "events": self.events,
        }, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> "WorkflowContext":
        data = json.loads(json_str)
        ctx = cls(
            workflow_id=data["workflow_id"],
            execution_id=data["execution_id"],
            shared=data.get("shared", {}),
            pipeline=data.get("pipeline", []),
            events=data.get("events", []),
        )
        return ctx
