"""Error handling strategies for workflow step execution."""

from __future__ import annotations

from abc import ABC, abstractmethod

from workflow.core import RetryConfig, Step, StepErrorAction


class ErrorAction:
    STOP = "stop"
    SKIP = "skip"
    CONTINUE = "continue"
    ROLLBACK = "rollback"
    DEGRADE = "degrade"
    RETRY = "retry"


class ErrorStrategy(ABC):
    """Base class for step-level error strategies."""

    @abstractmethod
    def decide(self, step: Step, error: Exception) -> str:
        """Return the action to take. One of ErrorAction constants."""
        raise NotImplementedError


class FailFast(ErrorStrategy):
    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.STOP


class SkipOnError(ErrorStrategy):
    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.SKIP


class ContinueOnError(ErrorStrategy):
    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.CONTINUE


class RollbackOnError(ErrorStrategy):
    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.ROLLBACK


class DegradeOnError(ErrorStrategy):
    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.DEGRADE


class RetryOnError(ErrorStrategy):
    """Retry with backoff. Returns RETRY until max_attempts exhausted."""

    def __init__(self, config: RetryConfig):
        self.config = config

    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.RETRY

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.config.max_attempts

    def delay(self, attempt: int) -> float:
        if self.config.backoff == "exponential":
            return self.config.initial_delay * (self.config.backoff_base ** (attempt - 1))
        return self.config.initial_delay * attempt


class ManualOnError(ErrorStrategy):
    """Signal that human decision is required."""

    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.STOP  # Stop and wait for manual intervention via workflow_rollback tool


class SagaCompensate(ErrorStrategy):
    """Execute compensate functions in reverse order for committed steps."""

    def decide(self, step: Step, error: Exception) -> str:
        return ErrorAction.ROLLBACK


# ── Factory ──────────────────────────────────────────────────────────────────


def strategy_for(step: Step) -> ErrorStrategy:
    """Map step.on_error to an ErrorStrategy instance."""
    if step.on_error == StepErrorAction.SKIP:
        return SkipOnError()
    if step.on_error == StepErrorAction.CONTINUE:
        return ContinueOnError()
    if step.on_error == StepErrorAction.ROLLBACK:
        return RollbackOnError()
    if step.on_error == StepErrorAction.DEGRADE:
        return DegradeOnError()
    if step.on_error == StepErrorAction.RETRY:
        cfg = step.retry or RetryConfig()
        return RetryOnError(cfg)
    return FailFast()  # default + STOP
