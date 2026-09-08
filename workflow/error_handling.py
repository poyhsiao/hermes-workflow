"""Error handling strategies for workflow step execution."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .core import RetryConfig, Step, StepErrorAction


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

    def should_retry(self, attempt: int) -> bool:
        """Whether to retry after a RETRY action. Base returns False."""
        return False

    def delay(self, attempt: int) -> float:
        """Seconds to wait before retry. Base returns 0."""
        return 0.0


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


def strategy_for(step: Step, default_policy: str = "fail_fast") -> ErrorStrategy:
    """Map step.on_error to an ErrorStrategy instance.

    Args:
        step: The step to get the error strategy for.
        default_policy: Workflow-level error policy to use when step.on_error is STOP (default).
    """
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
    # step.on_error == STOP (default): apply workflow-level error policy
    if default_policy == "skip":
        return SkipOnError()
    if default_policy == "continue":
        return ContinueOnError()
    if default_policy == "rollback":
        return RollbackOnError()
    if default_policy == "degrade":
        return DegradeOnError()
    if default_policy == "retry":
        cfg = step.retry or RetryConfig()
        return RetryOnError(cfg)
    return FailFast()  # default + STOP
