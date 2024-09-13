# -*- coding: utf-8 -*-
import time
from typing import Callable

from datadog.dogstatsd.base import DogStatsd
from loguru import logger


def exp_backoff_work_loop(
    description: str,
    metrics: DogStatsd,
    work_func: Callable[[], bool],
    skip_condition: Callable[[], bool],
    stop_condition: Callable[[], bool],
    work_loop_id: str = "main",
    min_delay_secs: int = 1,
    max_delay_nop_secs: int = 10,
    max_delay_err_secs: int = 30,
    enforce_min_delay: bool = False,
) -> None:
    """
    Executes a work loop with exponential backoff in case of errors or no work.

    Does not pause between work attempts if work was performed in previous loop
    unless enforce_min_delay is set to True.

    Args:
        description (str): A description of the work being done.
        work_func (Callable[[], bool]):
            A function that performs the work.
            Should return True if work was done, False otherwise.
        skip_condition (Callable[[], bool]):
            A function that returns True if the work loop should be skipped, False otherwise.
            Example: a function that returns true while system is under maintenance.
        stop_condition (Callable[[], bool]):
            A function that returns True if the work loop should stop, False otherwise.
        work_loop_id (str): A unique identifier for the work loop,
            for metrics (e.g. "main", "cleanup")
        min_delay_secs (int, optional):
            The minimum delay between work attempts, in seconds. Defaults to 1.
        max_delay_nop_secs (int, optional):
            The maximum delay between work attempts when there is no work to do, in seconds.
            Defaults to 10.
        max_delay_err_secs (int, optional):
            The maximum delay between work attempts when an error occurs, in seconds.
            Defaults to 30.
        enforce_min_delay (bool, optional):
            If set to True, pauses for min_delay_secs between work attempts when
            work was done in previous cycle.
            Defaults to False.
    """
    if min_delay_secs < 1:
        raise ValueError("min_delay_secs must be >= 1 for exponential backoff to work")
    delay = min_delay_secs

    def sleep_and_incr_delay(max_delay: int) -> None:
        nonlocal delay
        time.sleep(delay)
        delay = min(max_delay, delay * 2)

    while not stop_condition():
        if skip_condition():
            logger.debug(f"Skipping {description} work loop, retrying in {delay}s...")
            _track_result(metrics, work_loop_id, result="skip", start_time=None)
            sleep_and_incr_delay(max_delay_nop_secs)
            continue

        start = time.time()
        try:
            did_work = work_func()
            if did_work:
                _track_result(metrics, work_loop_id, result="ok", start_time=start)
                # If work was done, reset back-off and keep looping without
                # delay until there's no more work to do.
                delay = min_delay_secs
                if enforce_min_delay:
                    time.sleep(min_delay_secs)
                continue

            _track_result(metrics, work_loop_id, result="nop", start_time=start)
            logger.debug(f"No {description} work to do, retrying in {delay}s...")
            sleep_and_incr_delay(max_delay_nop_secs)

        except Exception as e:
            logger.error(
                f"Error in {description} work loop, retrying in {delay}s ({e})"
            )
            _track_result(metrics, work_loop_id, result="err", start_time=start)
            sleep_and_incr_delay(max_delay_err_secs)


def _track_result(
    metrics: DogStatsd,
    work_loop_id: str,
    result: str,
    start_time: float | None,
) -> None:
    duration_ms = 0 if start_time is None else (time.time() - start_time) * 1000
    metrics.timing(f"work_loop.{work_loop_id}.duration", duration_ms)
    metrics.increment(f"work_loop.{work_loop_id}.result.{result}")


def fixed_interval_work_loop(
    description: str,
    metrics: DogStatsd,
    work_func: Callable[[], bool],
    skip_condition: Callable[[], bool],
    stop_condition: Callable[[], bool],
    work_loop_id: str = "main",
    delay_secs: int = 60,
) -> None:
    """A work loop that executes work at a fixed interval."""
    return exp_backoff_work_loop(
        description=description,
        metrics=metrics,
        work_func=work_func,
        skip_condition=skip_condition,
        stop_condition=stop_condition,
        work_loop_id=work_loop_id,
        # Piggy-back off of exp backoff impl with fixed delays.
        min_delay_secs=delay_secs,
        max_delay_nop_secs=delay_secs,
        max_delay_err_secs=delay_secs,
        enforce_min_delay=True,
    )
