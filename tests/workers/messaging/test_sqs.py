# -*- coding: utf-8 -*-
import json
import time
from datetime import timedelta

import pytest
from mypy_boto3_sqs.client import SQSClient
from pydantic import BaseModel

from workers.messaging.sqs import SQSQueue

from ...test_lib.services import TestServices


@pytest.fixture
def sqs(external_deps: TestServices) -> SQSClient:
    return external_deps.sqs_client()


class Data(BaseModel):
    foo: str
    bar: int


@pytest.mark.integration
def test_enqueue_and_retrieve(sqs: SQSClient):
    queue_name = "simple_test"
    create_queue_with_dlq(sqs, queue_name)

    queue = SQSQueue(sqs, queue_name, Data)
    queue.enqueue(Data(foo="baz", bar=1))
    messages = queue.retrieve()

    assert len(messages) == 1
    assert messages[0].content == Data(foo="baz", bar=1)
    queue.acknowledge(messages)
    assert queue.retrieve(timeout_secs=0) == []


@pytest.mark.integration
def test_enqueue_with_delay(sqs: SQSClient):
    queue_name = "delay_test"
    create_queue_with_dlq(sqs, queue_name)

    queue = SQSQueue(sqs, queue_name, Data)
    queue.enqueue(Data(foo="baz", bar=1), delay=timedelta(seconds=1))
    messages = queue.retrieve(timeout_secs=0)
    assert len(messages) == 0

    time.sleep(1)
    messages = queue.retrieve()
    assert len(messages) == 1
    assert messages[0].content == Data(foo="baz", bar=1)
    queue.acknowledge(messages)
    assert queue.retrieve(timeout_secs=0) == []


@pytest.mark.integration
def test_deadletter(sqs: SQSClient):
    queue_name = "redrive_test"
    create_queue_with_dlq(sqs, queue_name, max_failures=1)

    queue = SQSQueue(sqs, queue_name, Data)
    queue.enqueue(Data(foo="baz", bar=1))
    messages = queue.retrieve(timeout_secs=0, limit=1)
    queue.acknowledge(successful=[], retry_now=messages)

    # Sleep long enough to allow the message to be moved to DLQ (~1s)
    time.sleep(2)

    # Confirm no new messages available
    # NB: SQS lazily moves messages to DLQ only on next ReceiveMessage,
    # so this retrieval check is actually required to make the test work.
    # (localstack also emulates this behavior)
    messages = queue.retrieve(timeout_secs=0, limit=1)
    assert len(messages) == 0

    # Confirm message is in DLQ
    dlqueue = SQSQueue[Data](sqs, f"{queue_name}_dlq", Data)
    messages = dlqueue.retrieve(timeout_secs=0, limit=1)
    assert len(messages) == 1


@pytest.mark.integration
def test_acknowledge_with_delay(sqs: SQSClient):
    queue_name = "delay_ack_test"
    create_queue_with_dlq(sqs, queue_name)

    queue = SQSQueue(sqs, queue_name, Data)
    original_message = Data(foo="baz", bar=1)
    queue.enqueue(original_message)
    messages = queue.retrieve()
    assert len(messages) == 1
    queue.acknowledge(
        successful=[],
        retry_now=[],
        retry_later=[(messages[0], timedelta(seconds=1))],
    )
    messages = queue.retrieve(timeout_secs=0)
    assert len(messages) == 0

    time.sleep(1)
    messages = queue.retrieve()
    assert len(messages) == 1
    assert messages[0].content == original_message


# NB: functions below are generic utilities. They should be moved to a shared
# file/module if there's more code that ends up testing with SQS.


def create_queue_with_dlq(
    sqs: SQSClient,
    queue_name: str,
    max_failures: int = 3,
) -> tuple[str, str]:
    """Create and configure a main+dead-letter pair of queues."""
    # Check if the main queue already exists
    try:
        q_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        dlq_url = sqs.get_queue_url(QueueName=f"{queue_name}_dlq")["QueueUrl"]
        print(f"Queue '{queue_name}' already exists.")
        return (q_url, dlq_url)
    except sqs.exceptions.QueueDoesNotExist as e:
        # expected
        pass
    except Exception as e:
        raise e

    # Step 1: Create Dead-Letter Queue
    dlq_response = sqs.create_queue(QueueName=f"{queue_name}_dlq")
    dlq_url = dlq_response["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=["QueueArn"],
    )[
        "Attributes"
    ]["QueueArn"]

    # Step 2: Create Main Queue and associate it with Dead-Letter Queue
    redrive_policy = {
        "deadLetterTargetArn": dlq_arn,
        "visibilityTimeout": "1",
        "maxReceiveCount": f"{max_failures}",
    }
    main_queue_response = sqs.create_queue(
        QueueName=queue_name,
        Attributes={
            # If not acked within 1s, message either returns to queue
            # or is sent to DLQ (if maxReceiveCount is exceeded).
            "VisibilityTimeout": "1",
            "RedrivePolicy": json.dumps(redrive_policy),
        },
    )
    q_url = main_queue_response["QueueUrl"]
    print(f"Created main queue '{queue_name}' and associated dlq.")
    return (q_url, dlq_url)
