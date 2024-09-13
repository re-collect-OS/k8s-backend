# -*- coding: utf-8 -*-
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from pydantic import BaseModel


@dataclass
class GraphCollection:
    name: str


class GraphCollections:
    Graph_v20231222 = GraphCollection("Graph_v20231222")


class Node(BaseModel):
    """
    Base class for all node objects.

    Attributes:
        id (UUID): Unique identifier.
        user_id (UUID): The ID of the user who owns the object.
    """

    id: UUID
    user_id: UUID
    node_type: str
    node_name: str
    node_embedding: list[float] | None = None

    artifact_id: UUID | None = None
    summary: str | None = None
    doc_type: str | None = None
    doc_subtype: str | None = None
    domain: str | None = None  # can be null in sentence_source
    title: str | None = None  # can be null in sentence_source
    byline: str | None = None  # can be null in sentence_source
    labels: list[str] | None = None  # e.g. email
    last_visited: datetime
    modified_at: datetime | None = None
    total_time_in_tab_in_seconds: int | None = None
    max_scroll_depth_reached_pct: int | None = None

    @staticmethod
    def deterministic_id(*data: Any) -> UUID:
        """
        Generate a deterministic unique ID based on the input data.

        All input elements are converted to string and concatenated with a
        colon before being hashed into a UUID v5.

        Example:
            >>> GraphObject.deterministic_id("foo", 1, {"bar": "baz"})
            UUID('2ffc716c-cc6a-5469-afb0-ef0f5f299e18')
        """

        if not data:
            raise ValueError("cannot generate UUID without data")

        return uuid5(NAMESPACE_DNS, ":".join([str(i) for i in data]))

    @staticmethod
    def random_id() -> UUID:
        """Generate a random unique ID."""
        return uuid4()


class Edge(BaseModel):
    """
    Base class for all edge objects.

    Attributes:
        id (UUID): Unique identifier.
        user_id (UUID): The ID of the user who owns the object.
    """

    id: UUID
    user_id: UUID
    start_node_id: UUID
    end_node_id: UUID
    directed: bool = True
    relationship_type: str

    semantic_similarity: float | None = None

    @staticmethod
    def deterministic_id(*data: Any) -> UUID:
        if not data:
            raise ValueError("cannot generate UUID without data")

        return uuid5(NAMESPACE_DNS, ":".join([str(i) for i in data]))

    @staticmethod
    def random_id() -> UUID:
        """Generate a random unique ID."""
        return uuid4()


NodeType = TypeVar("NodeType", bound=Node)
EdgeType = TypeVar("EdgeType", bound=Edge)
