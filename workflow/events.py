"""Simple in-process event bus for workflow step signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List
import threading


@dataclass
class WorkflowEvent:
    workflow_id: str
    execution_id: str
    event_name: str
    payload: Any = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EventBus:
    """In-process pub/sub for workflow events."""

    _instance: "EventBus | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[WorkflowEvent], None]]] = {}
        self._sub_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def subscribe(self, event_name: str, callback: Callable[[WorkflowEvent], None]) -> None:
        with self._sub_lock:
            if event_name not in self._subscribers:
                self._subscribers[event_name] = []
            self._subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: Callable[[WorkflowEvent], None]) -> None:
        with self._sub_lock:
            if event_name in self._subscribers:
                self._subscribers[event_name] = [cb for cb in self._subscribers[event_name] if cb != callback]

    def publish(self, event: WorkflowEvent) -> None:
        with self._sub_lock:
            callbacks = list(self._subscribers.get(event.event_name, []))
        for cb in callbacks:
            try:
                cb(event)
            except Exception:
                pass  # Don't let subscriber errors break the bus

    def emit(self, workflow_id: str, execution_id: str, event_name: str, payload: Any = None) -> None:
        evt = WorkflowEvent(workflow_id=workflow_id, execution_id=execution_id, event_name=event_name, payload=payload)
        self.publish(evt)


# ── Step lifecycle events ─────────────────────────────────────────────────────


STEP_STARTED = "step.started"
STEP_COMPLETED = "step.completed"
STEP_FAILED = "step.failed"
STEP_RETRIED = "step.retried"
WORKFLOW_STARTED = "workflow.started"
WORKFLOW_COMPLETED = "workflow.completed"
WORKFLOW_FAILED = "workflow.failed"
WORKFLOW_ROLLBACK = "workflow.rollback"
WORKFLOW_STOPPED = "workflow.stopped"
