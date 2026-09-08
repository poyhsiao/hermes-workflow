"""OpenTelemetry tracing for workflow executions."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

# ── OpenTelemetry integration ──────────────────────────────────────────────────
# Try to use real OTel if available, otherwise no-op stub

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Status, StatusCode

    _provider = TracerProvider(resource=Resource.create({"service.name": "hermes-dynamic-workflow"}))
    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer(__name__)

    def trace_workflow(execution_id: str, workflow_name: str) -> Any:  # type: ignore[misc]
        return _tracer.start_as_current_span(f"workflow/{workflow_name}", attributes={"execution_id": execution_id})

    def trace_step(step_name: str, step_type: str, execution_id: str) -> Any:  # type: ignore[misc]
        return _tracer.start_as_current_span(
            f"step/{step_name}", attributes={"step_type": step_type, "execution_id": execution_id}
        )

    @contextmanager
    def span(name: str, attrs: dict | None = None):
        with _tracer.start_as_current_span(name, attributes=attrs or {}) as s:
            try:
                yield s
                s.set_status(Status(StatusCode.OK))
            except Exception as e:
                s.set_status(Status(StatusCode.ERROR, str(e)))
                raise

except ImportError:
    # OTel not available — no-op stubs
    from contextlib import contextmanager

    @contextmanager
    def trace_workflow(execution_id: str, workflow_name: str) -> Any:  # type: ignore[misc]
        yield

    @contextmanager
    def trace_step(step_name: str, step_type: str, execution_id: str) -> Any:  # type: ignore[misc]
        yield

    @contextmanager
    def span(name: str, attrs: dict | None = None):
        yield
