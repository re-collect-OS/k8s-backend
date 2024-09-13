# -*- coding: utf-8 -*-
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from pydantic import BaseModel


@dataclass
class Collection:
    name: str


class Collections:
    # In self-hosted weaviate
    Paragraph_v20230517 = Collection("Paragraph_v20230517")

    # In weaviate cloud services
    Paragraph_v20231120 = Collection("Paragraph_v20231120")


class CollectionObject(BaseModel):
    """
    Base class for all collection objects.

    Attributes:
        id (UUID): Unique identifier.
        user_id (UUID): The ID of the user who owns the object.
        vector (list[float]): Embedding vector.
    """

    # fields
    user_id: UUID
    # _additional
    id: UUID
    vector: list[float]

    @staticmethod
    def deterministic_id(*data: Any) -> UUID:
        """
        Generate a deterministic unique ID based on the input data.

        All input elements are converted to string and concatenated with a
        colon before being hashed into a UUID v5.

        Example:
            >>> CollectionObject.deterministic_id("foo", 1, {"bar": "baz"})
            UUID('2ffc716c-cc6a-5469-afb0-ef0f5f299e18')
        """

        if not data:
            raise ValueError("cannot generate UUID without data")

        return uuid5(NAMESPACE_DNS, ":".join([str(i) for i in data]))

    @staticmethod
    def random_id() -> UUID:
        """Generate a random unique ID."""
        return uuid4()


CollectionObjectType = TypeVar("CollectionObjectType", bound=CollectionObject)


class CreateManyError(Exception):
    """
    Raised when an error occurs while creating multiple objects in a collection.
    """

    errors: list[str]

    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors
