# -*- coding: utf-8 -*-
from abc import ABC
from dataclasses import dataclass
from typing import Any, Generic
from uuid import UUID

from ..collections import CollectionObjectType


@dataclass
class WeaviateDataObject:
    """
    Represents a data object in Weaviate.

    Attributes:
        id (UUID): The unique identifier of the data object.
        vector (list[float]): The vector representation of the data object.
        data (dict[str, Any]): The data associated with the object. For create
            or update operations, contents must be JSON-encodable.
    """

    id: UUID
    vector: list[float]
    data: dict[str, Any]


class WeaviateObjectMapper(
    Generic[CollectionObjectType],
    ABC,
):
    """
    Base class for a mapper between Weaviate collection data objects and
    `CollectionObject` subclasses.

    Subclasses can override `to_weaviate` and `from_weaviate` for custom
    logic but the default implementations should be sufficient for most cases.
    """

    def __init__(self, record_cls: type[CollectionObjectType]) -> None:
        self._record_cls = record_cls

    def fields(self) -> list[str]:
        """
        List of fields to request when querying Weaviate. These are supplied
        directly to the `.get()` method and will be returned under the `data`
        key of the Weaviate data object.

        Subclasses for objects that have additional fields (i.e. all useful
        cases) must override to call super and list all additional fields that
        are to be written to/read from Weaviate, e.g.:

            def fields(self) -> list[str]:
                return super().fields() + ["doc_id", "text"]

        If these fields are not supplied, create operations will not add them
        to the object and read operations will not request them.
        """

        # Improvement to consider: generate this list with reflection to obviate
        # the need for subclasses to override.
        return ["user_id"]

    def additional_fields(self) -> list[str]:
        """
        List of additional fields to request when querying Weaviate. These will
        be supplied to `.with_additional()` and will be returned under the
        `_additional` key of the Weaviate data object.

        If overrides are necessary, subclasses should override to call super and
        add more fields, e.g.:

            def additional_fields(self) -> list[str]:
                return super().additional_fields() + ["score"]
        """
        return ["vector", "id"]

    def to_weaviate(
        self,
        record: CollectionObjectType,
    ) -> WeaviateDataObject:
        """
        Convert a `CollectionObject` subclass to a Weaviate data object.

        Conversion returns a tuple of the data object and the vector, discarding
        all other additional_fields (these are expected to be generated
        server-side and thus have no meaning on insert/update).
        """

        data = record.model_dump(mode="json")
        # _additional keys are computed by weaviate and only relevant for reads;
        # pop them from the dictionary to submit.
        for add in self.additional_fields():
            data.pop(add)

        return WeaviateDataObject(
            id=record.id,
            vector=record.vector,
            data=data,
        )

    def from_weaviate(self, data: dict[str, Any]) -> CollectionObjectType:
        """Convert a Weaviate data object to a `CollectionObject` subclass."""

        # Weaviate returns values under both `data` and `_additional` keys.
        # Merge them into a single dictionary to pass to `model_validate`.
        additional = data.pop("_additional")
        for add in self.additional_fields():
            data[add] = additional.pop(add)

        return self._record_cls.model_validate(data)
