# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Generic, Optional
from uuid import UUID

from .collections import Collection, CollectionObjectType


class CreateManyError(Exception):
    """
    Raised when an error occurs while creating multiple objects in a collection.
    """

    errors: list[str]

    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


class UserCollection(
    Generic[CollectionObjectType],
    ABC,
):
    """
    Shared contract for all vector database collections where objects are
    owned by a user.
    """

    @abstractmethod
    def create(
        self,
        object: CollectionObjectType,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    def create_many(
        self,
        objects: list[CollectionObjectType],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    def get_by_id(
        self,
        id: UUID,
    ) -> Optional[CollectionObjectType]:
        raise NotImplementedError()

    @abstractmethod
    def get_by_user_id(
        self,
        user_id: UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> list[CollectionObjectType]:
        raise NotImplementedError()

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError()

    @abstractmethod
    def count_by_user_id(self, user_id: UUID) -> int:
        raise NotImplementedError()

    @abstractmethod
    def delete_by_user_id(self, user_id: UUID) -> int:
        raise NotImplementedError()

    @property
    @abstractmethod
    def collection_class(self) -> Collection:
        raise NotImplementedError()
