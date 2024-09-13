# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

ContentType = TypeVar("ContentType", bound=BaseModel)


@dataclass
class Message(Generic[ContentType]):
    """
    A message from the queue.

    Attributes:
        content (T): The contents of the message.
        failures (int): The number of times the message has failed to process.
        context (any): Implementation-specific context for the message.
            For example, for SQS it would be the tuple (id, receipt_handle);
            for Kafka it would be the tuple (id, offset); for a mock
            implementation it could be just a string for the ID.
    """

    content: ContentType
    failures: int = 0
    context: Any = None
