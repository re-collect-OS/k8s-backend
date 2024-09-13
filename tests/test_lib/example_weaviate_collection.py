# -*- coding: utf-8 -*-
import weaviate

from common.collections.collections import Collection, CollectionObject
from common.collections.weaviate.data_object import WeaviateObjectMapper
from common.collections.weaviate.user_collection import WeaviateUserCollection


class ExampleObject(CollectionObject):
    """An example CollectionObject, for testing purposes."""

    str_field: str
    int_field: int
    list_field: list[str]


class _ExampleMapper(WeaviateObjectMapper[ExampleObject]):
    """An example WeaviateObjectMapper implementation, for testing purposes."""

    def __init__(self) -> None:
        super().__init__(ExampleObject)

    def fields(self) -> list[str]:
        return super().fields() + [
            "str_field",
            "int_field",
            "list_field",
        ]


class ExampleWeaviateUserCollection(
    WeaviateUserCollection[ExampleObject],
):
    """An example WeaviateUserCollection implementation, for testing purposes."""

    def __init__(self, client: weaviate.Client) -> None:
        super().__init__(
            client=client,
            collection_class=Collection("Example"),
            mapper=_ExampleMapper(),
        )
