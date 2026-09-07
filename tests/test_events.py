"""Tests for EventBus."""

import pytest

from workflow.events import (
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_STARTED,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_STARTED,
    WORKFLOW_STOPPED,
    EventBus,
    WorkflowEvent,
)


class TestWorkflowEvent:
    def test_workflow_event_defaults(self):
        evt = WorkflowEvent(workflow_id="w1", execution_id="e1", event_name="test")
        assert evt.workflow_id == "w1"
        assert evt.execution_id == "e1"
        assert evt.event_name == "test"
        assert evt.payload is None
        assert evt.timestamp  # auto-set

    def test_workflow_event_with_payload(self):
        evt = WorkflowEvent(workflow_id="w1", execution_id="e1", event_name="test", payload={"key": "val"})
        assert evt.payload == {"key": "val"}


class TestEventBus:
    def test_singleton(self):
        bus1 = EventBus.get_instance()
        bus2 = EventBus.get_instance()
        assert bus1 is bus2

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []

        def handler(evt: WorkflowEvent):
            received.append(evt)

        bus.subscribe("my_event", handler)
        bus.emit("w1", "e1", "my_event", {"data": 42})
        assert len(received) == 1
        assert received[0].payload == {"data": 42}

    def test_unsubscribe(self):
        bus = EventBus()
        received = []

        def handler(evt: WorkflowEvent):
            received.append(evt)

        bus.subscribe("ev1", handler)
        bus.unsubscribe("ev1", handler)
        bus.emit("w1", "e1", "ev1")
        assert len(received) == 0

    def test_multiple_subscribers(self):
        bus = EventBus()
        count = []

        def handler1(evt: WorkflowEvent):
            count.append(1)

        def handler2(evt: WorkflowEvent):
            count.append(2)

        bus.subscribe("ev", handler1)
        bus.subscribe("ev", handler2)
        bus.emit("w1", "e1", "ev")
        assert len(count) == 2

    def test_publish_ignores_subscriber_errors(self):
        bus = EventBus()

        def bad_handler(evt: WorkflowEvent):
            raise RuntimeError("subscriber error")

        bus.subscribe("ev", bad_handler)
        # Should not raise
        bus.emit("w1", "e1", "ev")

    def test_emit_creates_correct_event(self):
        bus = EventBus()
        received = []

        def handler(evt: WorkflowEvent):
            received.append(evt)

        bus.subscribe("step.started", handler)
        bus.emit("w1", "e1", "step.started", {"step": "a"})
        assert len(received) == 1
        assert received[0].workflow_id == "w1"
        assert received[0].execution_id == "e1"
        assert received[0].event_name == "step.started"
        assert received[0].payload == {"step": "a"}

    def test_subscribe_unsubscribe_idempotent(self):
        bus = EventBus()

        def handler(evt: WorkflowEvent):
            pass

        # Multiple unsubscribe should not raise
        bus.unsubscribe("ev", handler)
        bus.unsubscribe("ev", handler)

    def test_publish_to_unknown_event(self):
        bus = EventBus()
        bus.emit("w1", "e1", "unknown.event")  # should not raise


class TestEventConstants:
    def test_step_events_exist(self):
        assert STEP_STARTED == "step.started"
        assert STEP_COMPLETED == "step.completed"
        assert STEP_FAILED == "step.failed"

    def test_workflow_events_exist(self):
        assert WORKFLOW_STARTED == "workflow.started"
        assert WORKFLOW_COMPLETED == "workflow.completed"
        assert WORKFLOW_FAILED == "workflow.failed"
        assert WORKFLOW_STOPPED == "workflow.stopped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
