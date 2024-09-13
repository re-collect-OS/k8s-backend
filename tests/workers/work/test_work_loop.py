# -*- coding: utf-8 -*-
from unittest.mock import ANY, Mock, call

from datadog.dogstatsd.base import DogStatsd
from pytest import MonkeyPatch

from workers.work.work_loop import exp_backoff_work_loop, fixed_interval_work_loop


def test_exp_backoff_no_sleep_when_work_done(monkeypatch: MonkeyPatch):
    mock_sleep = Mock()
    monkeypatch.setattr("time.sleep", mock_sleep)

    metrics = Mock(DogStatsd)
    # Set up work function mock to always return True (i.e. work done)
    work_func = Mock(return_value=True)
    # Set up the stop condition to stop on the 4th work loop.
    stop_condition = Mock(side_effect=[False, False, False, True])

    exp_backoff_work_loop(
        "test-work",
        metrics=metrics,
        work_func=work_func,
        skip_condition=lambda: False,
        stop_condition=stop_condition,
    )

    # Confirm that sleep was never called (i.e. tight loop when work done)
    mock_sleep.assert_not_called()
    metrics.assert_has_calls(
        [
            call.timing("work_loop.main.duration", ANY),
            call.increment("work_loop.main.result.ok"),
            call.timing("work_loop.main.duration", ANY),
            call.increment("work_loop.main.result.ok"),
            call.timing("work_loop.main.duration", ANY),
            call.increment("work_loop.main.result.ok"),
        ]
    )


def test_exp_backoff_sleep_when_work_done(monkeypatch: MonkeyPatch):
    mock_sleep = Mock()
    monkeypatch.setattr("time.sleep", mock_sleep)

    metrics = Mock(DogStatsd)
    # Set up work function mock to always return True (i.e. work done)
    work_func = Mock(return_value=True)
    # Set up the stop condition to stop on the 4th work loop.
    stop_condition = Mock(side_effect=[False, False, False, True])

    exp_backoff_work_loop(
        "test-work",
        metrics=metrics,
        work_func=work_func,
        skip_condition=lambda: False,
        stop_condition=stop_condition,
        min_delay_secs=1,
        max_delay_nop_secs=2,
        max_delay_err_secs=4,
        enforce_min_delay=True,  # Testing this param is respected
    )

    # Confirm that sleep is called even when work was done.
    mock_sleep.assert_has_calls(
        [
            call(1),
            call(1),
            call(1),
        ]
    )


def test_exp_backoff_no_work(monkeypatch: MonkeyPatch):
    mock_sleep = Mock()
    monkeypatch.setattr("time.sleep", mock_sleep)

    metrics = Mock(DogStatsd)
    # Set up work function mock to always return False (i.e. no work done)
    work_func = Mock(return_value=False)
    # Set up the stop condition to stop on the 5th work loop.
    stop_condition = Mock(side_effect=[False, False, False, False, True])

    exp_backoff_work_loop(
        "test-nop-backoff",
        metrics=metrics,
        work_func=work_func,
        skip_condition=lambda: False,
        stop_condition=stop_condition,
        work_loop_id="foo",
        min_delay_secs=1,
        max_delay_nop_secs=2,  # Testing this param is respected
        max_delay_err_secs=4,
    )

    # Confirm that sleep is called with the expected delays,
    # validating that max_delay_nop_secs is respected.
    mock_sleep.assert_has_calls(
        [
            call(1),
            call(2),
            call(2),
            call(2),
        ]
    )
    metrics.assert_has_calls(
        [
            call.timing("work_loop.foo.duration", ANY),
            call.increment("work_loop.foo.result.nop"),
            call.timing("work_loop.foo.duration", ANY),
            call.increment("work_loop.foo.result.nop"),
            call.timing("work_loop.foo.duration", ANY),
            call.increment("work_loop.foo.result.nop"),
            call.timing("work_loop.foo.duration", ANY),
            call.increment("work_loop.foo.result.nop"),
        ]
    )


def test_exp_backoff_on_error(monkeypatch: MonkeyPatch):
    mock_sleep = Mock()
    monkeypatch.setattr("time.sleep", mock_sleep)

    metrics = Mock(DogStatsd)
    # Set up work function mock to always fail
    work_func = Mock(side_effect=Exception("kaboom!"))
    # Set up the stop condition to stop on the 5th work loop.
    stop_condition = Mock(side_effect=[False, False, False, False, True])

    exp_backoff_work_loop(
        "test-err-backoff",
        metrics=metrics,
        work_func=work_func,
        skip_condition=lambda: False,
        stop_condition=stop_condition,
        work_loop_id="bar",
        min_delay_secs=2,
        max_delay_nop_secs=4,
        max_delay_err_secs=8,  # Testing this param is respected
    )

    # Confirm that sleep is called with the expected delays,
    # validating that max_delay_err_secs is respected.
    mock_sleep.assert_has_calls(
        [
            call(2),
            call(4),
            call(8),
            call(8),
        ]
    )
    metrics.assert_has_calls(
        [
            call.timing("work_loop.bar.duration", ANY),
            call.increment("work_loop.bar.result.err"),
            call.timing("work_loop.bar.duration", ANY),
            call.increment("work_loop.bar.result.err"),
            call.timing("work_loop.bar.duration", ANY),
            call.increment("work_loop.bar.result.err"),
            call.timing("work_loop.bar.duration", ANY),
            call.increment("work_loop.bar.result.err"),
        ]
    )


def test_exp_backoff_skip(monkeypatch: MonkeyPatch):
    mock_sleep = Mock()
    monkeypatch.setattr("time.sleep", mock_sleep)

    metrics = Mock(DogStatsd)
    # Set up work function mock to always fail (though it'll never be called)
    work_func = Mock(side_effect=Exception("kaboom!"))
    # Set up the stop condition to stop on the 5th work loop.
    stop_condition = Mock(side_effect=[False, False, False, False, True])

    exp_backoff_work_loop(
        "test-err-backoff",
        metrics=metrics,
        work_func=work_func,
        skip_condition=lambda: True,  # Testing skip condition
        stop_condition=stop_condition,
        work_loop_id="baz",
        min_delay_secs=2,
        max_delay_nop_secs=4,
        max_delay_err_secs=8,
    )

    # Confirm that sleep is called with the expected delays,
    # validating that max_delay_nop_secs is respected.
    mock_sleep.assert_has_calls(
        [
            call(2),
            call(4),
            call(4),
            call(4),
        ]
    )
    metrics.assert_has_calls(
        [
            call.timing("work_loop.baz.duration", 0),
            call.increment("work_loop.baz.result.skip"),
            call.timing("work_loop.baz.duration", 0),
            call.increment("work_loop.baz.result.skip"),
            call.timing("work_loop.baz.duration", 0),
            call.increment("work_loop.baz.result.skip"),
            call.timing("work_loop.baz.duration", 0),
            call.increment("work_loop.baz.result.skip"),
        ]
    )


def test_fixed_interval_always_sleeps_fixed_amount(monkeypatch: MonkeyPatch):
    mock_sleep = Mock()
    monkeypatch.setattr("time.sleep", mock_sleep)

    metrics = Mock(DogStatsd)
    # Test work, no-work, and exception cases.
    work_func = Mock(side_effect=[True, False, Exception("kaboom!")])
    # Stop on the 4th iteration.
    stop_condition = Mock(side_effect=[False, False, False, True])

    fixed_interval_work_loop(
        "fixed-interval-work",
        metrics=metrics,
        work_func=work_func,
        skip_condition=lambda: False,
        stop_condition=stop_condition,
        work_loop_id="fixed",
        delay_secs=5,
    )
    # Confirm that sleep is always called with the expected delay.
    mock_sleep.assert_has_calls(
        [
            call(5),
            call(5),
            call(5),
        ]
    )
    # Assert metrics reflect expected work loop outcomes.
    metrics.assert_has_calls(
        [
            call.timing("work_loop.fixed.duration", ANY),
            call.increment("work_loop.fixed.result.ok"),
            call.timing("work_loop.fixed.duration", ANY),
            call.increment("work_loop.fixed.result.nop"),
            call.timing("work_loop.fixed.duration", ANY),
            call.increment("work_loop.fixed.result.err"),
        ]
    )
