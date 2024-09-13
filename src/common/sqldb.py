# -*- coding: utf-8 -*-
import os

from sqlalchemy import Engine, create_engine

from . import env


def connection_url_from_env() -> str:
    _user = env.require_str("POSTGRESQL_USER")
    _pw = env.require_str("POSTGRESQL_PASSWORD")
    _host = env.require_str("POSTGRESQL_HOST")
    _port = os.getenv("POSTGRESQL_PORT", "5432")
    _db = env.require_str("POSTGRESQL_DB")

    return f"postgresql+psycopg2://{_user}:{_pw}@{_host}:{_port}/{_db}"


def sqldb_from_env(
    pool_size: int = 5,
    pool_acquire_timeout_secs: int = 5,
    pool_connection_recycle_secs: int = 3600,
) -> Engine:
    """
    Create a SQLAlchemy backed by a connection pool based on env vars.

    Args:
        pool_size (int): The maximum number of connections to keep in the pool
            (default: 5).
        pool_acquire_timeout_secs (int): The maximum number of seconds to wait
            for a connection from the pool (default: 5). Raises an exception if
            no connection is available within this timeout (fail-fast).
        pool_connection_recycle_secs (int): The number of seconds after which a
            connection is automatically recycled (default: 3600).
    """
    return create_engine(
        url=connection_url_from_env(),
        pool_size=pool_size,
        pool_timeout=pool_acquire_timeout_secs,
        pool_recycle=pool_connection_recycle_secs,
    )
