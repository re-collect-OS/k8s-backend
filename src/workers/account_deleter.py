# -*- coding: utf-8 -*-
from typing import Any
from uuid import UUID

from datadog.dogstatsd.base import DogStatsd
from loguru import logger
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient
from mypy_boto3_s3.client import S3Client
from neo4j import Driver
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import SingletonThreadPool

from common import env, killswitches
from common.aws import cognito_client_from_env, s3_client_from_env, sqs_client_from_env
from common.collections import weaviate
from common.collections.user_collection import UserCollection
from common.collections.weaviate.paragraph_v1 import WeaviateParagraphV1Collection
from common.collections.weaviate.paragraph_v2 import WeaviateParagraphV2Collection
from common.neo4j import neo4j_client_from_env
from common.records.records import Table
from common.records.user_records import GenericUserRecords
from common.sqldb import connection_url_from_env
from common.text import NotImplementedCrossEncodeFunc, NotImplementedEmbedFunc
from recollect.helpers.log import LOG_CONFIG

from .messaging.sqs import SQSQueue
from .messaging.unordered_queue import HandleResult, poll_and_handle_serially
from .work.signals import OSSignalHandler
from .work.work_loop import exp_backoff_work_loop

logger.configure(**LOG_CONFIG)


class AccountDeletion(BaseModel):
    id: UUID
    # Email isn't strictly necessary, but since all records will cease to exist
    # during the deletion operation, it's more useful for debugging/logging.
    # It's also convenient to avoid an extra call to Cognito to look up the
    # username by ID (cognito does not support deleting by ID).
    email: str


class AccountDeleter:
    def __init__(
        self,
        cognito: CognitoIdentityProviderClient,
        s3: S3Client,
        neo4j: Driver,
        collections: list[UserCollection[Any]],
        sql_db: Engine,
        user_records: list[GenericUserRecords],
        cognito_user_pool_id: str,
        s3_user_files_bucket: str,
        metrics: DogStatsd,
    ):
        self._cognito = cognito
        self._s3 = s3
        self._neo4j = neo4j
        self._collections = collections
        self._sql_db = sql_db
        self._user_records = user_records
        self._user_pool_id = cognito_user_pool_id
        self._user_files_bucket = s3_user_files_bucket
        self._metrics = metrics

    def delete_account(self, user: AccountDeletion) -> HandleResult:
        # NB: This logic intentionally does not check for the existence or
        # state of user account, since the whole operation is meant to be
        # idempotent (it may fail at any point and be retried).
        logger.info("Deleting account for {user}...", user=user.email)

        # First delete cognito account so user can't log in again.
        self._delete_cognito_account(user)

        # Then delete all resources, from most to least valuable to free up.
        for collection in self._collections:
            total = collection.delete_by_user_id(user.id)
            logger.info(
                "Deleted {count} {collection} data objects for {user}",
                count=total,
                collection=collection.collection_class,
                user=user.email,
            )
        self._batch_delete_all_s3_objects(user)
        self._delete_all_sql_records(user)
        self._delete_graph_nodes_edges(user)

        logger.info(f"Deleted all data and account for {user.email}.")
        return HandleResult.ok()

    def _delete_graph_nodes_edges(self, user: AccountDeletion) -> None:
        with self._neo4j.session(database="neo4j") as session:
            query = """
            // Identify nodes with the specific user_id and collect the relationships
            MATCH (n)
            WHERE n['user_id'] = $user_id
            OPTIONAL MATCH (n)-[r]-()
            WITH n, count(r) AS totalRelationships
            // Collect nodes and sum of all relationships before deletion
            WITH collect(n) AS nodesToDelete, sum(totalRelationships) AS deletedRelationshipsCount, count(n) AS deletedNodesCount
            // Now delete the nodes and their relationships
            UNWIND nodesToDelete AS nodeToDelete
            DETACH DELETE nodeToDelete
            RETURN deletedNodesCount, deletedRelationshipsCount
            """
            results = session.run(query, user_id=str(user.id)).single()
            # TODO the unwind gives us n identical results, take first
            result = results[0] if results else None
            if result is not None:
                logger.info(
                    f"Deleted {result['deletedNodesCount']} graph nodes and "
                    f"{int(result['deletedRelationshipsCount']/2)} edges for {user.email}."
                )
            else:
                logger.info(f"Deleted 0 graph nodes and 0 edges for {user.email}.")

    def _delete_cognito_account(self, user: AccountDeletion) -> None:
        try:
            self._cognito.admin_delete_user(
                UserPoolId=self._user_pool_id,
                Username=user.email,
            )
            logger.info("Deleted cognito account for {user}.", user=user.email)
        except self._cognito.exceptions.UserNotFoundException:
            logger.info(
                "Cognito account for {user} not found or already deleted.",
                user=user.email,
            )

    def _batch_delete_all_s3_objects(self, user: AccountDeletion) -> None:
        total = 0
        while True:
            # List up to 1k objects that match the prefix for batch deletion
            # (batch deletion has a limit of 1k objects)
            response = self._s3.list_objects_v2(
                Bucket=self._user_files_bucket,
                Prefix=f"{user.id}/",
                MaxKeys=1000,
            )

            objects_to_delete = [
                {"Key": obj["Key"]} for obj in response.get("Contents", [])
            ]

            if len(objects_to_delete) == 0:
                break

            self._s3.delete_objects(
                Bucket=self._user_files_bucket,
                Delete={"Objects": objects_to_delete},
            )
            total += len(objects_to_delete)

        logger.info(
            "Deleted {total} S3 objects for {user}.",
            total=total,
            user=user.email,
        )

    def _delete_all_sql_records(self, user: AccountDeletion) -> None:
        total = 0

        with self._sql_db.begin() as connection:
            for records in self._user_records:
                deleted = records.delete_by_user_id(connection, user_id=user.id)
                logger.info(
                    "Deleted {count} {table} records for {user}.",
                    count=deleted,
                    table=records.table.name,
                    user=user.email,
                )
                total += deleted

        logger.info(
            "Deleted a total of {total} SQL records for {user}.",
            total=total,
            user=user.email,
        )


if __name__ == "__main__":
    worker_name = "account_deleter"

    env_cognito_user_pool_id = env.require_str("COGNITO_USERPOOL_ID")
    env_s3_user_files_bucket = env.require_str("S3_BUCKET_USERFILES")

    queue = SQSQueue(
        sqs_client=sqs_client_from_env(),
        queue_name="account_deletions",
        message_cls=AccountDeletion,
    )

    # List of tables to delete; order matters (foreign key constraints),
    # so delete UserAccount last.
    tables_to_delete = Table.user_id_tables()
    tables_to_delete.remove(Table.UserAccount)
    tables_to_delete = list(tables_to_delete)
    tables_to_delete.append(Table.UserAccount)

    weaviate_client = weaviate.self_hosted_client()

    metrics = DogStatsd()
    account_deleter = AccountDeleter(
        cognito=cognito_client_from_env(),
        s3=s3_client_from_env(),
        neo4j=neo4j_client_from_env(),
        collections=[
            WeaviateParagraphV1Collection(
                client=weaviate_client,
                embed_func=NotImplementedEmbedFunc,
                cross_encode_func=NotImplementedCrossEncodeFunc,
            ),
            WeaviateParagraphV2Collection(
                client=weaviate_client,
                embed_func=NotImplementedEmbedFunc,
                cross_encode_func=NotImplementedCrossEncodeFunc,
            ),
        ],
        # DB pool with single connection to DB (all work is serial)
        sql_db=create_engine(
            url=connection_url_from_env(),
            poolclass=SingletonThreadPool,
        ),
        user_records=[GenericUserRecords(table) for table in tables_to_delete],
        cognito_user_pool_id=env_cognito_user_pool_id,
        s3_user_files_bucket=env_s3_user_files_bucket,
        metrics=metrics,
    )

    logger.info("Worker {name} starting...", name=worker_name)
    sig_handler = OSSignalHandler()
    exp_backoff_work_loop(
        description=worker_name,
        metrics=metrics,
        # There's some potential for optimization by deleting records in batches
        # but account deletion is expected to be so infrequenty that it's not
        # worth the added complexity.
        work_func=lambda: poll_and_handle_serially(
            # This queue will rarely have data so this is a good candidate to be
            # combined with other low-activity queues under a single worker.
            description=worker_name,
            metrics=metrics,
            queue=queue,
            handler=account_deleter.delete_account,
            # There's no rush to delete accounts; pull as many messages as
            # possible and handle them serially. Higher throughput can be
            # achieved by scaling up the number of account deletion workers.
            limit=10,
        ),
        skip_condition=lambda: killswitches.maintenance.is_enabled(),
        stop_condition=lambda: sig_handler.term_received,
    )

    logger.info("Worker {name} stopped.", worker_name)
    metrics.close_socket()
