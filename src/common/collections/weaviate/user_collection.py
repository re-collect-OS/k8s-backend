# -*- coding: utf-8 -*-
from typing import Any, Callable, Generic, Optional
from uuid import UUID

import weaviate  # type: ignore (no stubs)
from loguru import logger
from weaviate.batch import Batch  # type: ignore (no stubs)

from common import env
from recollect.helpers.log import LOG_CONFIG

from ..collections import Collection, CollectionObjectType
from ..user_collection import CreateManyError, UserCollection
from .data_object import WeaviateObjectMapper

logger.configure(**LOG_CONFIG)


_batch_delete_limit = 10_000


def id_eq(id: UUID) -> dict[str, Any]:
    return {
        "path": ["id"],
        "operator": "Equal",
        "valueText": str(id),
    }


def user_id_eq(user_id: UUID) -> dict[str, Any]:
    return {
        "path": ["user_id"],
        "operator": "Equal",
        "valueText": str(user_id),
    }


# FIXME some legacy doc_ids are not valid UUIDs
def user_id_doc_id_eq(user_id: UUID, doc_id: str) -> dict[str, Any]:
    return {
        "operator": "And",
        "operands": [
            {
                "path": ["user_id"],
                "operator": "Equal",
                "valueText": str(user_id),
            },
            {
                "path": ["doc_id"],
                "operator": "Equal",
                "valueText": str(doc_id),
            },
        ],
    }


class WeaviateUserCollection(
    UserCollection[CollectionObjectType],
    Generic[CollectionObjectType],
):
    def __init__(
        self,
        client: weaviate.Client,
        collection_class: Collection,
        mapper: WeaviateObjectMapper[CollectionObjectType],
    ) -> None:
        self._client = client
        self._collection_class = collection_class
        self._mapper = mapper

    def create(self, object: CollectionObjectType) -> None:
        collection_name = self._collection_class.name
        data_object = self._mapper.to_weaviate(object)

        self._client.data_object.create(
            class_name=collection_name,
            uuid=data_object.id,
            vector=data_object.vector,
            data_object=data_object.data,
        )

    def create_many(self, objects: list[CollectionObjectType]) -> None:
        if len(objects) == 0:
            raise ValueError("empty list of objects supplied")

        collection_name = self._collection_class.name
        batch_callback = _BatchCallback()

        self._client.batch.configure(
            # `batch_size` takes an `int` value to enable auto-batching
            # (`None` is used for manual batching)
            batch_size=20,
            # dynamically update the `batch_size` based on import speed
            dynamic=True,
            timeout_retries=3,
            connection_error_retries=3,
            weaviate_error_retries=weaviate.WeaviateErrorRetryConf(number_retries=3),
            callback=batch_callback,
        )

        try:
            with self._client.batch as batch:
                for obj in objects:
                    data_object = self._mapper.to_weaviate(obj)
                    batch.add_data_object(
                        class_name=collection_name,
                        uuid=data_object.id,
                        vector=data_object.vector,
                        data_object=data_object.data,
                    )
        except Exception as e:
            # If the batch flush fails, it never resets its internal list of
            # data objects to push to the server, which means that subsequent
            # batch writes will include the objects that _may have_ triggered
            # the failure, which can put the client in a permanent failure
            # state. To workaround this bug until it's fixed, we manually reset
            # the internal batch state on exceptions.
            self._client.batch.shutdown()
            self._client.batch = Batch(self._client._connection)
            logger.opt(exception=e).warning(
                f"[PR #349] weaviate client batch manual reset, {str(e)}"
            )

        # Batch operations can partially fail, which is why it's imperative that
        # IDs are stable (so as to not create duplicate records on retries).
        failures = len(batch_callback.errors)
        if failures == len(objects):
            raise CreateManyError(
                message=f"complete batch-add failure ({failures} failed)",
                errors=batch_callback.errors,
            )

        if failures > 0:
            ok = len(objects) - len(batch_callback.errors)
            raise CreateManyError(
                message=(
                    f"partial batch add failure ({ok} created, "
                    f"{len(batch_callback.errors)} failed)"
                ),
                errors=batch_callback.errors,
            )
        # else all records were successfully created

    def get_by_id(self, id: UUID) -> Optional[CollectionObjectType]:
        collection_name = self._collection_class.name
        result: dict[str, Any] = (
            self._client.query.get(collection_name, self._mapper.fields())
            .with_where(id_eq(id))
            .with_additional(self._mapper.additional_fields())
            .do()
        )

        data_objects = get_data_objects(
            result,
            collection_name,
            description=lambda: f"read {collection_name} with id={id}",
        )

        if len(data_objects) == 0:
            return None

        # This should not be possible but fail loudly in non-prod envs #justincase
        if len(data_objects) > 1 and not env.is_production():
            raise Exception(f"multiple {collection_name} found with id={id}")

        return self._mapper.from_weaviate(data_objects[0])

    # FIXME this will fail if offset+limit > QUERY_MAXIMUM_RESULTS of weaviate,
    # which is currently defaulting to 100_000
    def get_by_user_id(
        self,
        user_id: UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> list[CollectionObjectType]:
        collection_name = self._collection_class.name
        result: dict[str, Any] = (
            self._client.query.get(collection_name, self._mapper.fields())
            .with_where(user_id_eq(user_id))
            .with_additional(self._mapper.additional_fields())
            .with_offset(offset)
            .with_limit(limit)
            .do()
        )

        data_objects = get_data_objects(
            result,
            collection_name,
            description=lambda: (
                f"read {collection_name} objects (offset={offset}, "
                f"limit={limit}) for user_id={user_id}"
            ),
        )

        return [self._mapper.from_weaviate(do) for do in data_objects]

    def get_by_user_id_doc_id(
        self,
        user_id: UUID,
        doc_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[CollectionObjectType]:
        collection_name = self._collection_class.name
        result: dict[str, Any] = (
            self._client.query.get(collection_name, self._mapper.fields())
            .with_where(user_id_doc_id_eq(user_id, doc_id))
            .with_additional(self._mapper.additional_fields())
            .with_offset(offset)
            .with_limit(limit)
            .do()
        )

        data_objects = get_data_objects(
            result,
            collection_name,
            description=lambda: (
                f"read {collection_name} objects (offset={offset}, "
                f"limit={limit}) for user_id={user_id}"
            ),
        )

        return [self._mapper.from_weaviate(do) for do in data_objects]

    def count(self) -> int:
        collection_name = self._collection_class.name
        result: dict[str, Any] = (
            self._client.query.aggregate(collection_name).with_meta_count().do()
        )

        aggregate_data = get_aggregate_data(
            result,
            collection_name,
            description=lambda: f"count {collection_name}",
        )

        return int(aggregate_data[0]["meta"]["count"])

    def count_by_user_id(self, user_id: UUID) -> int:
        collection_name = self._collection_class.name
        result: dict[str, Any] = (
            self._client.query.aggregate(collection_name)
            .with_where(user_id_eq(user_id))
            .with_meta_count()
            .do()
        )

        aggregate_data = get_aggregate_data(
            result,
            collection_name,
            description=lambda: f"count {collection_name} for user_id={user_id}",
        )

        return int(aggregate_data[0]["meta"]["count"])

    def delete_by_user_id(self, user_id: UUID) -> int:
        collection_name = self._collection_class.name
        total = 0
        while True:
            result: dict[str, Any] = self._client.batch.delete_objects(
                class_name=collection_name,
                where=user_id_eq(user_id),
            )
            ok = int(result["results"]["successful"])
            err = int(result["results"]["failed"])
            if err > 0:
                raise Exception(
                    f"failed to delete {err} {collection_name} objects "
                    f"for user_id={user_id}"
                )

            total += ok
            # Weaviate has an upper limit of 10k objects per batch delete. If
            # fewer were matched, all vectors were deleted.
            if ok < _batch_delete_limit:
                break

        return total

    @property
    def collection_class(self) -> Collection:
        return self._collection_class


class _BatchCallback:
    errors: list[str] = []

    def __call__(self, results: list[dict[str, Any]] | None):
        if results is not None:
            for result in results:
                if "result" in result and "errors" in result["result"]:
                    if "error" in result["result"]["errors"]:
                        error_msg = str(result["result"]["errors"]["error"])
                        self.errors.append(error_msg)


def get_data_objects(
    result: dict[str, Any],
    collection_name: str,
    description: Callable[[], str],
) -> list[dict[str, Any]]:
    raise_if_error(result, description)

    return result["data"]["Get"][collection_name]


def get_aggregate_data(
    result: dict[str, Any],
    collection_name: str,
    description: Callable[[], str],
) -> list[dict[str, Any]]:
    raise_if_error(result, description)

    return result["data"]["Aggregate"][collection_name]


def raise_if_error(
    result: dict[str, Any],
    description: Callable[[], str],
) -> None:
    if "errors" in result:
        raise Exception(
            f"failed to {description()}: {result['errors'][0]['message']}",
        )
