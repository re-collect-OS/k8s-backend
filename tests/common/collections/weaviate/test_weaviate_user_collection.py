# -*- coding: utf-8 -*-
import pytest
from hamcrest import assert_that, contains_inanyorder, equal_to, is_

from ....test_lib.example_weaviate_collection import (
    ExampleObject,
    ExampleWeaviateUserCollection,
)
from ....test_lib.services import TestServices


@pytest.fixture
def test_collection(
    external_deps: TestServices,
) -> ExampleWeaviateUserCollection:
    return ExampleWeaviateUserCollection(
        client=external_deps.vec_db_client(truncate_all_classes=True),
    )


def test_weaviate_user_collection_read_after_create(
    test_collection: ExampleWeaviateUserCollection,
) -> None:
    # Test covers:
    # - create
    # - count
    # - read
    # - serialization + deserialization
    to_create = ExampleObject(
        id=ExampleObject.deterministic_id(3, "unique", "things"),
        user_id=ExampleObject.deterministic_id("user_id"),
        vector=[1.0, 2.0, 3.0],
        str_field="str_field",
        int_field=42,
        list_field=["list_field"],
    )
    test_collection.create(to_create)
    assert_that(test_collection.count(), is_(equal_to(1)))
    read = test_collection.get_by_id(to_create.id)

    assert_that(read, is_(equal_to(to_create)))


def test_weaviate_user_collection_get_by_user_id(
    test_collection: ExampleWeaviateUserCollection,
) -> None:
    # Test covers:
    # - batch create
    # - count_by_user_id
    # - multi-object read
    # - de/serialization
    to_create: list[ExampleObject] = []
    user_id = ExampleObject.deterministic_id("user-0")
    for i in range(10):
        to_create.append(
            ExampleObject(
                id=ExampleObject.random_id(),
                user_id=user_id,
                vector=[1.0, 2.0, 3.0],
                str_field="str_field",
                int_field=i,
                list_field=["list_field"],
            )
        )

    test_collection.create_many(to_create)
    assert_that(test_collection.count_by_user_id(user_id), is_(equal_to(10)))

    read = test_collection.get_by_user_id(user_id)
    assert_that(len(read), is_(equal_to(10)))

    assert_that(read, contains_inanyorder(*to_create))


def test_weaviate_user_collection_delete_by_user_id(
    test_collection: ExampleWeaviateUserCollection,
) -> None:
    # Test covers:
    # - batch create
    # - delete_by_user_id
    # - count_by_user_id
    to_create: list[ExampleObject] = []
    user_id = ExampleObject.deterministic_id("user-0")
    for i in range(10):
        to_create.append(
            ExampleObject(
                id=ExampleObject.random_id(),
                user_id=user_id,
                vector=[1.0, 2.0, 3.0],
                str_field="str_field",
                int_field=i,
                list_field=["list_field"],
            )
        )

    test_collection.create_many(to_create)
    assert_that(test_collection.count_by_user_id(user_id), is_(equal_to(10)))

    test_collection.delete_by_user_id(user_id)
    assert_that(test_collection.count_by_user_id(user_id), is_(equal_to(0)))
