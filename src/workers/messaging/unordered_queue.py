# -*- coding: utf-8 -*-
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Callable, Generic, Optional

from datadog.dogstatsd.base import DogStatsd
from loguru import logger

from .message import ContentType, Message


class UnorderedQueue(Generic[ContentType], ABC):
    """
    Contract for an unordered queue of messages.

    Implementations are expected to:
    - Deliver messages at least once
    - Make delivered messages unavailable to other consumers until acknowledged
      or timed out
    - Block until messages become available (with a timeout)

    Implementations are not expected to:
    - Provide any ordering guarantees

    Can be implemented in SQS, GCP Pub/Sub, Resque/RQ, etc.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError()

    def enqueue(
        self,
        item: ContentType,
        delay: timedelta = timedelta(0),
    ) -> None:
        self.enqueue_multiple_with_delay([(item, delay)])

    def enqueue_multiple(
        self,
        items: list[ContentType],
        delay: timedelta = timedelta(0),
    ) -> None:
        """
        Enqueue multiple items to the queue with an optional delay for the first
        delivery attempt.

        To specify a delay for each item, use `enqueue_multiple_with_delay`.

        Number of accepted items may vary with implementation
        (e.g. SQS limits to 10)

        Delay limits may vary with implementation (e.g. SQS limits to 15m).
        Exact delays are not guaranteed; implementations may deliver messages
        slightly early or late (i.e. this is not a scheduling mechanism).
        """
        self.enqueue_multiple_with_delay(
            items=[(item, delay) for item in items],
        )

    @abstractmethod
    def enqueue_multiple_with_delay(
        self,
        items: list[tuple[ContentType, timedelta]],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    def retrieve(
        self,
        timeout_secs: int,
        limit: int,
    ) -> list[Message[ContentType]]:
        """
        Wait up to `timeout_secs` for up to `limit` messages to become available
        (i.e. long-polling).

        If `timeout_secs` is 0, returns immediately with whatever is available
        (i.e. short-polling).

        Retrieved messages are not available to other consumers until they are
        acknowledged as completed or failed (see `acknowledge` method).

        Messages obtained with `retrieve` must always be acknowledged — even if
        implementations are expected to have their own timeout mechanisms, this
        should not be assumed by clients of this interface (i.e. always call
        `acknowledge`).
        """
        pass

    @abstractmethod
    def acknowledge(
        self,
        successful: list[Message[ContentType]],
        retry_now: list[Message[ContentType]],
        retry_later: list[tuple[Message[ContentType], timedelta]] = [],
    ) -> None:
        """
        Acknowledge messages that were successfully processed or failed,
        distinguishing between messages that should be immediately retried and
        those that should be retried after a delay.

        Maximum delay is implementation-dependent (e.g. 12h for SQS; see impl).
        """
        pass


@dataclass
class HandleResult:
    class Status(Enum):
        OK = "ok"
        RETRY = "retry"
        RETRY_LATER = "retry_later"

    status: Status
    delay: Optional[timedelta] = None

    @staticmethod
    def ok():
        """Message was handled and can be marked as processed."""
        return HandleResult(HandleResult.Status.OK)

    @staticmethod
    def retry_now():
        """
        There was an issue processing the message. It should be redelivered as
        soon as possible.
        """
        return HandleResult(HandleResult.Status.RETRY)

    @staticmethod
    def retry_later(delay: timedelta):
        """
        There was an issue processing the message. It should be redelivered
        after the specified delay.
        """
        return HandleResult(HandleResult.Status.RETRY_LATER, delay)


def poll_and_handle_serially(
    description: str,
    metrics: DogStatsd,
    queue: UnorderedQueue[ContentType],
    handler: Callable[[ContentType], HandleResult],
    timeout_secs: int = 20,
    limit: int = 10,
) -> bool:
    """
    Polls an unordered queue for messages and handles them serially using the
    provided handler function.

    Calls `handler` function on every message and, if no exceptions are raised,
    acknowledges the message as successfully processed. For messages that raise
    exceptions, the message is acknowledged as failed.

    Returns:
        bool: True if any messages were processed, False otherwise.
    """
    messages = queue.retrieve(timeout_secs, limit)
    if not messages:
        return False

    logger.debug(f"Processing {len(messages)} {description} messages...")

    ok: list[Message[ContentType]] = []
    retries: list[Message[ContentType]] = []
    delayed_retries: list[tuple[Message[ContentType], timedelta]] = []

    # To consider: there's high potential for parallelism here (each message
    # represents an independent unit of work).
    for message in messages:
        start = time.time()
        try:
            result = handler(message.content)
            _track_handle_duration(metrics, queue.name, start)

            if result.status == HandleResult.Status.OK:
                ok.append(message)
            elif result.status == HandleResult.Status.RETRY:
                retries.append(message)
            elif result.status == HandleResult.Status.RETRY_LATER:
                assert result.delay is not None
                delayed_retries.append((message, result.delay))
            else:
                raise ValueError(f"unexpected handle result: {result}")
        except Exception as e:
            _track_handle_duration(metrics, queue.name, start)
            logger.error(
                "Exception handling {description} message {message}: {e}",
                description=description,
                message=message,
                e=str(e),
            )
            # Unhandled exceptions are treated as retries without delay.
            retries.append(message)

    queue.acknowledge(
        successful=ok,
        retry_now=retries,
        retry_later=delayed_retries,
    )
    logger.debug(
        "Batch of {description} messages processed "
        "({ok} ok, {retries} retries, {delayed} delayed retries)",
        description=description,
        ok=len(ok),
        retries=len(retries),
        delayed=len(delayed_retries),
    )
    # Changing these requires corresponding changes in observability repo.
    metrics.increment(f"handle_message.{queue.name}.result.ok", len(ok))
    metrics.increment(f"handle_message.{queue.name}.result.retry", len(retries))
    metrics.increment(
        f"handle_message.{queue.name}.result.retrylater", len(delayed_retries)
    )
    return True


def _track_handle_duration(
    metrics: DogStatsd,
    queue_name: str,
    start: float,
) -> None:
    duration_ms = (time.time() - start) * 1000
    metrics.timing(f"handle_message.{queue_name}.duration", duration_ms)
