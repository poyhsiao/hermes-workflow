"""Tests for error handling strategies."""

import pytest

from workflow.core import RetryConfig, Step, StepErrorAction, StepType
from workflow.error_handling import (
    ContinueOnError,
    DegradeOnError,
    ErrorAction,
    FailFast,
    ManualOnError,
    RetryOnError,
    RollbackOnError,
    SagaCompensate,
    SkipOnError,
    strategy_for,
)


def make_step(on_error: StepErrorAction = StepErrorAction.STOP, retry=None) -> Step:
    return Step(
        name="test_step",
        step_type=StepType.TOOL,
        args={},
        on_error=on_error,
        retry=retry,
    )


class TestErrorAction:
    def test_error_action_constants(self):
        assert ErrorAction.STOP == "stop"
        assert ErrorAction.SKIP == "skip"
        assert ErrorAction.CONTINUE == "continue"
        assert ErrorAction.ROLLBACK == "rollback"
        assert ErrorAction.DEGRADE == "degrade"
        assert ErrorAction.RETRY == "retry"


class TestFailFast:
    def test_returns_stop(self):
        s = FailFast()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.STOP
        assert not s.should_retry(1)
        assert s.delay(1) == 0.0


class TestSkipOnError:
    def test_returns_skip(self):
        s = SkipOnError()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.SKIP


class TestContinueOnError:
    def test_returns_continue(self):
        s = ContinueOnError()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.CONTINUE


class TestRollbackOnError:
    def test_returns_rollback(self):
        s = RollbackOnError()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.ROLLBACK


class TestDegradeOnError:
    def test_returns_degrade(self):
        s = DegradeOnError()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.DEGRADE


class TestRetryOnError:
    def test_returns_retry(self):
        cfg = RetryConfig()
        s = RetryOnError(cfg)
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.RETRY

    def test_should_retry_respects_attempts(self):
        cfg = RetryConfig(max_attempts=3)
        s = RetryOnError(cfg)
        assert s.should_retry(1) is True
        assert s.should_retry(2) is True
        assert s.should_retry(3) is False
        assert s.should_retry(4) is False

    def test_delay_exponential(self):
        cfg = RetryConfig(initial_delay=1.0, backoff="exponential")
        s = RetryOnError(cfg)
        assert s.delay(1) == 1.0
        assert s.delay(2) == 2.0  # 1 * 2^1
        assert s.delay(3) == 4.0  # 1 * 2^2

    def test_delay_linear(self):
        cfg = RetryConfig(initial_delay=2.0, backoff="linear")
        s = RetryOnError(cfg)
        assert s.delay(1) == 2.0
        assert s.delay(2) == 4.0
        assert s.delay(3) == 6.0


class TestManualOnError:
    def test_returns_stop(self):
        s = ManualOnError()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.STOP


class TestSagaCompensate:
    def test_returns_rollback(self):
        s = SagaCompensate()
        step = make_step()
        assert s.decide(step, ValueError("err")) == ErrorAction.ROLLBACK


class TestStrategyFor:
    def test_skip(self):
        step = make_step(on_error=StepErrorAction.SKIP)
        s = strategy_for(step)
        assert isinstance(s, SkipOnError)

    def test_continue(self):
        step = make_step(on_error=StepErrorAction.CONTINUE)
        s = strategy_for(step)
        assert isinstance(s, ContinueOnError)

    def test_rollback(self):
        step = make_step(on_error=StepErrorAction.ROLLBACK)
        s = strategy_for(step)
        assert isinstance(s, RollbackOnError)

    def test_degrade(self):
        step = make_step(on_error=StepErrorAction.DEGRADE)
        s = strategy_for(step)
        assert isinstance(s, DegradeOnError)

    def test_retry(self):
        step = make_step(on_error=StepErrorAction.RETRY)
        s = strategy_for(step)
        assert isinstance(s, RetryOnError)

    def test_default_is_fail_fast(self):
        step = make_step(on_error=StepErrorAction.STOP)
        s = strategy_for(step)
        assert isinstance(s, FailFast)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
