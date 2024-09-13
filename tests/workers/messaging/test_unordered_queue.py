# -*- coding: utf-8 -*-
from unittest.mock import ANY, Mock, call

from datadog.dogstatsd.base import DogStatsd
from pydantic import BaseModel

from workers.messaging.unordered_queue import (
    HandleResult,
    Message,
    UnorderedQueue,
    poll_and_handle_serially,
)


class _Content(BaseModel):
    data: str


def test_poll_and_handle_serially():
    mock_queue = Mock(UnorderedQueue[_Content])
    mock_queue.name = "test_q"
    mock_metrics = Mock(DogStatsd)
    mock_message_ok = Mock(Message[_Content])
    mock_message_ok.content = _Content(data="ok_content")
    mock_message_retry = Mock(Message[_Content])
    mock_message_retry.content = _Content(data="retry_content")
    mock_message_err = Mock(Message[_Content])
    mock_message_err.content = _Content(data="err_content")

    mock_queue.retrieve.return_value = [  # type: ignore
        mock_message_ok,
        mock_message_retry,
        mock_message_err,
    ]

    def mock_handler(content: _Content) -> HandleResult:
        if content.data == "ok_content":
            return HandleResult.ok()
        elif content.data == "retry_content":
            return HandleResult.retry_now()
        else:
            raise Exception("kaboom!")

    result = poll_and_handle_serially(
        "description",
        mock_metrics,
        mock_queue,
        mock_handler,
    )

    assert result == True
    # acknowledge(ok, retry_now)
    mock_queue.acknowledge.assert_called_once_with(
        successful=[mock_message_ok],
        retry_now=[mock_message_retry, mock_message_err],
        retry_later=[],
    )
    mock_metrics.assert_has_calls(
        [
            call.timing("handle_message.test_q.duration", ANY),
            call.timing("handle_message.test_q.duration", ANY),
            call.timing("handle_message.test_q.duration", ANY),
            call.increment("handle_message.test_q.result.ok", 1),
            call.increment("handle_message.test_q.result.retry", 2),
            call.increment("handle_message.test_q.result.retrylater", 0),
        ]
    )
