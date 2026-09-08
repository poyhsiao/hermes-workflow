"""Workflow execution context: shared state + pipeline output."""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowContext:
    """Mutable execution context shared across all steps in a workflow run."""

    workflow_id: str
    execution_id: str
    shared: dict[str, Any] = field(default_factory=dict)
    pipeline: list[Any] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    # ponytail: lock for thread-safe writes in parallel branches
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.shared[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.shared.get(key, default)

    def push(self, value: Any) -> None:
        """Append to pipeline (pipe output from previous step)."""
        with self._lock:
            self.pipeline.append(value)

    def pop(self) -> Any:
        with self._lock:
            return self.pipeline.pop() if self.pipeline else None

    def last_output(self) -> Any:
        return self.pipeline[-1] if self.pipeline else None

    def emit_event(self, name: str, payload: Any = None) -> None:
        with self._lock:
            self.events.append({"name": name, "payload": payload})

    def checkpoint(self, step_index: int, metadata: dict | None = None) -> dict:
        with self._lock:
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
        with self._lock:
            self.shared = copy.deepcopy(checkpoint.get("shared", {}))
            self.pipeline = list(checkpoint.get("pipeline", []))
            self.events = list(checkpoint.get("events", []))
            # Trim checkpoints after the rollback point
            for idx, cp in enumerate(self.checkpoints):
                if cp.get("step_index") == checkpoint.get("step_index") and cp.get("shared") == checkpoint.get("shared"):
                    self.checkpoints = self.checkpoints[: idx + 1]
                    break

    def resolve_var(self, template: str) -> str:
        """Simple {{ var }} substitution from shared context.

        Shell-context only: escapes $ ` ; & | < > " ' \\ and newlines
        to prevent command injection when the result is interpolated into
        a shell command. For non-shell uses (LLM prompts, SQL, HTML, file
        paths), use resolve_var_raw() instead.

        ponytail: entire substitution runs under _lock to prevent TOCTOU
        races where shared mutates between snapshot capture and string
        replacement (would cause inconsistent placeholder resolution).
        """
        if not isinstance(template, str):
            return template
        result = template
        with self._lock:
            shared_items = list(self.shared.items())
            for key, val in shared_items:
                placeholder = "{{ " + key + " }}"
                if placeholder in result:
                    replacement = str(val)
                    escaped: list[str] = []
                    for ch in replacement:
                        if ch == "\\":
                            escaped.append("\\\\")
                        elif ch == "\n":
                            escaped.append("\\n")
                        elif ch in ("$", "`", ";", "&", "|", "<", ">", '"', "'"):
                            escaped.append("\\" + ch)
                        else:
                            escaped.append(ch)
                    result = result.replace(placeholder, "".join(escaped))
        return result

    def resolve_var_raw(self, template: str) -> str:
        """{{ var }} substitution without any escaping.

        Use for non-shell contexts (LLM prompts, SQL, HTML, file paths).
        Callers are responsible for context-appropriate encoding.
        """
        if not isinstance(template, str):
            return template
        result = template
        with self._lock:
            shared_items = list(self.shared.items())
            for key, val in shared_items:
                placeholder = "{{ " + key + " }}"
                if placeholder in result:
                    result = result.replace(placeholder, str(val))
        return result

    def resolve_args(self, args: dict) -> dict:
        """Resolve {{ var }} placeholders in all string values of args dict."""
        resolved: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str):
                resolved[k] = self.resolve_var_raw(v)
            elif isinstance(v, dict):
                resolved[k] = self.resolve_args(v)
            elif isinstance(v, list):
                resolved[k] = [self.resolve_var_raw(item) if isinstance(item, str) else item for item in v]
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
    def from_json(cls, json_str: str) -> WorkflowContext:
        d = json.loads(json_str)
        return cls(
            workflow_id=d["workflow_id"],
            execution_id=d["execution_id"],
            shared=d.get("shared", {}),
            pipeline=d.get("pipeline", []),
            events=d.get("events", []),
        )
