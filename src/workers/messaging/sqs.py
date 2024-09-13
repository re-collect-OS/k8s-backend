# -*- coding: utf-8 -*-
from datetime import timedelta

from mypy_boto3_sqs.client import SQSClient

from .unordered_queue import ContentType, Message, UnorderedQueue


class SQSQueue(UnorderedQueue[ContentType]):
    """Implementation of `UnorderedQueue` backed by Amazon Simple Queue Service (SQS).

    Args:
        sqs_client (SQSClient): An instance of the boto3 SQS client.
        queue_name (str): The name of the SQS queue to use.
        model_type (Type[T]): The type of the model objects to be enqueued and retrieved.
    """

    def __init__(
        self,
        sqs_client: SQSClient,
        queue_name: str,
        message_cls: type[ContentType],
    ):
        self._message_cls = message_cls
        self._sqs = sqs_client
        response = self._sqs.get_queue_url(QueueName=queue_name)
        self._queue_name = queue_name
        self._queue_url = response["QueueUrl"]

    @property
    def name(self) -> str:
        return self._queue_name

    def enqueue_multiple_with_delay(
        self,
        items: list[tuple[ContentType, timedelta]],
    ) -> None:
        # https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_SendMessageBatch.html
        if len(items) > 10:
            raise ValueError("SQS impl only supports up to 10 items per batch")

        self._sqs.send_message_batch(
            QueueUrl=self._queue_url,
            Entries=[
                {
                    "Id": str(index),
                    "MessageBody": content.model_dump_json(),
                    "DelaySeconds": delay.seconds,
                }
                for index, (content, delay) in enumerate(items)
            ],
        )

    def retrieve(
        self,
        timeout_secs: int = 20,
        limit: int = 10,
    ) -> list[Message[ContentType]]:
        messages = self._sqs.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=limit,
            WaitTimeSeconds=timeout_secs,
        ).get("Messages", [])

        return [
            Message(
                content=self._message_cls.model_validate_json(msg["Body"]),
                context={
                    "id": msg["MessageId"],
                    "receipt_handle": msg["ReceiptHandle"],
                },
            )
            for msg in messages
        ]

    def acknowledge(
        self,
        successful: list[Message[ContentType]],
        retry_now: list[Message[ContentType]] = [],
        retry_later: list[tuple[Message[ContentType], timedelta]] = [],
    ) -> None:
        # NB: SQS doesn't support delaying retries but this can be emulated by
        # setting the message's visibility timeout to the desired delay (and
        # not deleting it). The maximum delay is 12h.
        if len(retry_later) > 0:
            self._sqs.change_message_visibility_batch(
                QueueUrl=self._queue_url,
                Entries=[
                    {
                        "Id": str(i),
                        "ReceiptHandle": msg.context["receipt_handle"],
                        "VisibilityTimeout": delay.seconds,
                    }
                    for i, (msg, delay) in enumerate(retry_later)
                ],
            )

        if len(successful) > 0:
            self._sqs.delete_message_batch(
                QueueUrl=self._queue_url,
                Entries=[
                    {
                        "Id": str(i),
                        "ReceiptHandle": msg.context["receipt_handle"],
                    }
                    for i, msg in enumerate(successful)
                ],
            )

        # Failed messages (retry_now) are implicitly retried if visibility
        # window expires before receiving an ack. They may be moved to DLQ if
        # they exceed the configured number of max retries for the queue in SQS.
