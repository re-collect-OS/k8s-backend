# -*- coding: utf-8 -*-
import json
import os
from types import TracebackType
from typing import Optional

import boto3
import neo4j
import sqlalchemy
import weaviate
from alembic import command
from alembic.config import Config
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient
from mypy_boto3_s3.client import S3Client
from mypy_boto3_sqs.client import SQSClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import SingletonThreadPool
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs


class TestServices:
    """
    Manage the lifecycle of mock services needed for integration tests.

    This class is intended to be used as a context manager — containers for mock
    services are started when entering the context and stopped when exiting.
    Service setup is opt-out; by default all services are started.

    Provides methods to create clients for each 3rd party API.

    Should be used once per integration test to avoid cross-test contamination.

    Uses different ports/volumes/etc. from the docker-compose local dev setup
    to avoid conflicts.

    Example:

    ```python
    # Launch only localstack (AWS mock)
    with TestContainers(localstack=True, sql_db=False, vec_db=False) as deps:
        deps.wait_until_ready()
        deps.s3_client().create_bucket(Bucket="test-bucket")

    # Running containers are stopped and removed when exiting the context.
    ```
    """

    __test__ = False  # Marker for pytest to ignore this class

    _sql_db: DockerContainer
    _vec_db: DockerContainer
    _graph_db: DockerContainer
    _aws: DockerContainer
    _cognito: DockerContainer

    def __init__(self):
        self._sql_db = (
            DockerContainer("postgres:11")
            .with_bind_ports(5432, _sql_port)
            .with_env("POSTGRES_USER", _sql_usr)
            .with_env("POSTGRES_PASSWORD", _sql_pwd)
            .with_env("POSTGRES_DB", _sql_db)
        )

        self._vec_db = (
            DockerContainer("semitechnologies/weaviate:1.22.4")
            .with_command("weaviate --scheme http --port 8080 --host 0.0.0.0")
            .with_bind_ports(8080, _vec_port)
            .with_env("AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED", "true")
            .with_env("PERSISTENCE_DATA_PATH", "/var/lib/weaviate")
        )

        self._graph_db = (
            DockerContainer("neo4j:5.15-community-bullseye")
            .with_bind_ports(7474, _graph_port_https)
            .with_bind_ports(7687, _graph_port_bolt)
            .with_env("NEO4J_AUTH", "none")
        )

        self._aws = (
            DockerContainer("localstack/localstack:3.0")
            .with_bind_ports(4566, _aws_port)
            .with_env("SERVICES", "s3,sqs,cognito")
            .with_env("EAGER_SERVICE_LOADING", "1")
        )

        self._cognito = DockerContainer(
            "jagregory/cognito-local:3-latest"
        ).with_bind_ports(9229, _cognito_port)

    def __enter__(self):
        self._sql_db.start()
        self._vec_db.start()
        self._aws.start()
        self._cognito.start()
        self._graph_db.start()

        self._wait_until_ready()

        self._setup_sql_db()
        self._setup_vec_db()

        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        self._sql_db.stop()
        self._vec_db.stop()
        self._aws.stop()
        self._cognito.stop()
        self._graph_db.stop()

        return False

    def sql_db_client(self, truncate_all_tables: bool = False) -> Engine:
        engine = create_engine(
            f"postgresql+psycopg2://{_sql_usr}:{_sql_pwd}@localhost:{_sql_port}/{_sql_db}",
            poolclass=SingletonThreadPool,
            # echo=True,  # useful in debugging
        )

        if truncate_all_tables:
            table_names = sqlalchemy.inspect(engine).get_table_names()
            with engine.connect() as conn:
                for table_name in table_names:
                    conn.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
                conn.commit()

        return engine

    def sql_db_session(self) -> Session:
        engine = self.sql_db_client()
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)()

    def vec_db_client(self, truncate_all_classes: bool = False) -> weaviate.Client:
        client = weaviate.Client(url=f"http://localhost:{_vec_port}")

        if truncate_all_classes:
            class_names: list[str] = [
                c["class"] for c in client.schema.get()["classes"]
            ]
            for class_name in class_names:
                client.schema.delete_class(class_name)

            for filename in os.listdir(_vec_schema_folder):
                if not filename.endswith(".json"):
                    continue
                with open(f"{_vec_schema_folder}/{filename}", "r") as f:
                    class_def = json.load(f)
                client.schema.create_class(class_def)

        return client

    def _delete_all_nodes_and_relationships(self, tx: neo4j.ManagedTransaction):
        # Delete all relationships
        tx.run("MATCH ()-[r]-() DELETE r")
        # Delete all nodes
        tx.run("MATCH (n) DELETE n")

    def graph_db_client(self, truncate_all_nodes_edges: bool = False) -> neo4j.Driver:
        driver = neo4j.GraphDatabase.driver(f"bolt://localhost:{_graph_port_bolt}")

        if truncate_all_nodes_edges:
            with driver.session() as session:
                session.execute_write(self._delete_all_nodes_and_relationships)

        return driver

    def s3_client(self) -> S3Client:
        return boto3.client("s3", **_aws_args)

    def sqs_client(self) -> SQSClient:
        return boto3.client("sqs", **_aws_args)

    def cognito_client(self) -> CognitoIdentityProviderClient:
        return boto3.client("cognito-idp", **_cognito_args)

    def _wait_until_ready(self, timeout: int = 10):
        """
        Wait for configured services to become ready to take requests.

        Checks the logs of each service for a "ready" message. Timeout is
        applied per service. In the worst case, this method will take N times
        the timeout to complete, where N is the number of services.
        """
        wait_for_logs(self._sql_db, "init process complete", timeout)
        wait_for_logs(self._vec_db, "Serving weaviate", timeout)
        # wait_for_logs(self._graph_db, "Started.", timeout)
        wait_for_logs(self._aws, "Ready.", timeout)
        wait_for_logs(self._cognito, "Cognito Local running on", timeout)

    def _setup_sql_db(self) -> None:
        # Load the initial_dump.sql, which is the starting point to run the
        # migrations. Ideally we'd just have to run the migrations but the
        # original DB schema was manually bootstrapped and the migrations were
        # only added later. Drop this once we've cleaned up the DB setup.
        with open(_sql_initial_dump_file, "r") as f:
            sql_dump = f.read()
        engine = self.sql_db_client()
        with engine.connect() as conn:
            conn.execute(text(sql_dump))
            conn.commit()

        # After the initial dump is loaded, run the migrations.
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", _sql_migrations_folder)

        # alembic/env.py expects these env vars to be set
        os.environ["POSTGRESQL_USER"] = str(engine.url.username)
        os.environ["POSTGRESQL_PASSWORD"] = str(engine.url.password)
        os.environ["POSTGRESQL_HOST"] = str(engine.url.host)
        os.environ["POSTGRESQL_DB"] = str(engine.url.database)
        os.environ["POSTGRESQL_PORT"] = str(engine.url.port)

        # Uncomment below and import logging to debug migrations with logs
        # logging.basicConfig()
        # logging.getLogger("alembic").setLevel(logging.INFO)
        with engine.connect() as connection:
            alembic_cfg.attributes["connection"] = connection
            command.upgrade(alembic_cfg, "head")

    def _setup_vec_db(self) -> None:
        # Load every .json in the weaviate/ folder as a class definition
        for filename in os.listdir(_vec_schema_folder):
            if not filename.endswith(".json"):
                continue
            with open(f"{_vec_schema_folder}/{filename}", "r") as f:
                class_def = json.load(f)
            self.vec_db_client().schema.create_class(class_def)


_migrations_folder = "./migrations"

_sql_usr = "postgres"
_sql_pwd = "postgres"  # pragma: allowlist secret
_sql_db = "user_data_test"
_sql_port = 15432
_sql_migrations_folder = f"{_migrations_folder}/pgsql"
_sql_initial_dump_file = f"{_sql_migrations_folder}/initial_dump.sql"

_vec_port = 18080
_vec_schema_folder = f"{_migrations_folder}/weaviate"

_graph_port_https = 17474
_graph_port_bolt = 17687

_aws_port = 14566
_aws_args = {
    "region_name": "us-east-1",
    "aws_access_key_id": "dummy",
    "aws_secret_access_key": "dummy",  # pragma: allowlist secret
    "endpoint_url": f"http://localhost:{_aws_port}",
}

_cognito_port = 19229
_cognito_args = _aws_args.copy()
_cognito_args["endpoint_url"] = f"http://localhost:{_cognito_port}"
