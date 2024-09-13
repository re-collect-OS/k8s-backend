# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar
from uuid import UUID

from .graph import GraphCollection

T = TypeVar("T")  # Type variable for nodes
U = TypeVar("U")  # Type variable for edges


class UserGraph(ABC, Generic[T, U]):
    """
    Shared contract for all graph database graphs where objects are
    owned by a user.
    """

    @abstractmethod
    def add_node(self, node: T) -> None:
        raise NotImplementedError()

    @abstractmethod
    def add_edge(self, start_node: T, end_node: T, edge: U) -> None:
        raise NotImplementedError()

    @abstractmethod
    def nodes(
        self, label: str | None = None, offset: int = 0, limit: int = 50
    ) -> list[T]:
        raise NotImplementedError()

    @abstractmethod
    def edges(self, offset: int = 0, limit: int = 50) -> list[U]:
        raise NotImplementedError()

    @abstractmethod
    def get_nodes_by_user_id(
        self, user_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[T]:
        raise NotImplementedError()

    @abstractmethod
    def get_edges_by_user_id(
        self, user_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[U]:
        raise NotImplementedError()

    @abstractmethod
    def count_nodes(self, label: str | None) -> int:
        raise NotImplementedError()

    @abstractmethod
    def count_edges(self, relationship_type: str | None) -> int:
        raise NotImplementedError()

    @abstractmethod
    def count_nodes_with_property_value(
        self, label: str, property_key: str, property_value: Any
    ) -> int:
        raise NotImplementedError()

    @abstractmethod
    def delete_nodes_with_property_value(
        self, label: str, property_key: str, property_value: Any
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    def delete_all(self) -> None:
        raise NotImplementedError()

    @property
    @abstractmethod
    def graph_collection(self) -> GraphCollection:
        raise NotImplementedError()
