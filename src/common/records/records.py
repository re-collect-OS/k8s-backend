# -*- coding: utf-8 -*-
from abc import ABC
from datetime import datetime
from enum import Enum
from typing import Any, Generic, Optional, TypeVar
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import sqlalchemy
from pydantic import BaseModel

QueryWithParams = tuple[sqlalchemy.TextClause, Optional[dict[str, Any]]]


class Table(Enum):
    GeneratedArtifact = "generated_artifact"
    RecurringImports = "recurring_imports"
    TrackingSessions = "tracking_sessions"
    ExternalFile = "external_file"
    Interaction = "interaction"

    # legacy tables with user_id column
    RssSubscription = "rss_subscription"
    IndexState = "index_state"
    Urlcontent = "urlcontent"
    SentenceSource = "sentence_source"
    Sentence = "sentence"
    IncomingUrlvisit = "incoming_urlvisit"
    Urlstate = "urlstate"
    Card = "card"
    Idea = "idea"
    GoList = "go_list"
    NoGoList = "no_go_list"
    Eventlog = "eventlog"
    Stack = "stack"
    Feedback_event = "feedback_event"
    UserAccount = "user_account"

    @classmethod
    def user_id_tables(cls) -> set["Table"]:
        # All tables currently have a user_id column.
        # If this changes, filter non-user_id tables here.
        return {table for table in Table}


class Record(BaseModel):
    """
    Base class for all SQL records.

    Attributes:
        id (UUID): A unique identifier for this recurring import.
        created_at (datetime): Timestamp of record creation.
    """

    id: UUID
    created_at: datetime

    @staticmethod
    def deterministic_id(*data: Any) -> UUID:
        """
        Generate a deterministic unique ID based on the input data.

        All input elements are converted to string and concatenated with a
        colon before being hashed into a UUID v5.

        Example:
            >>> Record.deterministic_id("foo", 1, {"bar": "baz"})
            UUID('2ffc716c-cc6a-5469-afb0-ef0f5f299e18')
        """

        if not data:
            raise ValueError("cannot generate UUID without data")

        return uuid5(NAMESPACE_DNS, ":".join([str(i) for i in data]))

    @staticmethod
    def random_id() -> UUID:
        """Generate a random unique identifier."""
        return uuid4()


RecordType = TypeVar("RecordType", bound=Record)


class RecordMapper(
    Generic[RecordType],
    ABC,
):
    """
    Base class for a mapper between SQL rows and Record models.

    Subclasses can override `to_row` and `from_row` for custom logic,
    but the default implementations should be sufficient for most use cases.
    """

    def __init__(self, record_cls: type[RecordType]) -> None:
        self._record_cls = record_cls

    def to_row(self, record: RecordType) -> dict[str, Any]:
        # subclasses can override for custom logic
        return record.model_dump(mode="json")

    def from_row(self, row: sqlalchemy.RowMapping) -> RecordType:
        return self._record_cls.model_validate(row)


class Records(
    Generic[RecordType],
):
    """
    Abstract class for a SQL table.

    All rows in are assumed to have an `id` (unique, primary key).
    """

    def __init__(
        self,
        table: Table,
        mapper: RecordMapper[RecordType],
    ) -> None:
        self._table = table
        self._mapper = mapper

    def insert(
        self,
        conn: sqlalchemy.Connection,
        record: RecordType,
    ) -> RecordType:
        """Insert a new record."""
        insert_data = self._mapper.to_row(record)
        # Pop created_at to let the database set it (better source of truth).
        insert_data.pop("created_at")

        (query, params) = BaseSQL.insert_returning(self._table, insert_data)
        inserted_row = conn.execute(query, params).mappings().fetchone()
        if inserted_row is None:
            raise RuntimeError("INSERT did not return row")

        return self._mapper.from_row(inserted_row)

    def upsert_by_id(
        self,
        conn: sqlalchemy.Connection,
        record: RecordType,
    ) -> RecordType:
        """Insert a new record, or update if record with same id exists."""
        insert_data = self._mapper.to_row(record)
        # Pop created_at to let the database set it (better source of truth).
        insert_data.pop("created_at")

        (query, params) = BaseSQL.upsert_by_id_returning(self._table, insert_data)
        inserted_row = conn.execute(query, params).mappings().fetchone()
        if inserted_row is None:
            raise RuntimeError("INSERT did not return row")

        return self._mapper.from_row(inserted_row)

    def get_by_id(
        self,
        conn: sqlalchemy.Connection,
        *,
        id: UUID,
    ) -> Optional[RecordType]:
        """Retrieve the record matching given ID, if it exists."""
        (query, params) = BaseSQL.select_by_id(self._table, id=id)
        row = conn.execute(query, params).mappings().fetchone()
        if row is None:
            return None

        return self._mapper.from_row(row)

    def delete_by_id(
        self,
        conn: sqlalchemy.Connection,
        *,
        id: UUID,
    ) -> bool:
        """Delete the record matching given ID."""
        (query, params) = BaseSQL.delete_by_id(self._table, id=id)
        return conn.execute(query, params).rowcount > 0

    @property
    def table(self) -> Table:
        return self._table


class BaseSQL:
    """Common SQL queries for all tables."""

    # NB: At the time of writing, many legacy tables don't have an id column.
    # The plan is to eventually replace the whole schema.

    @staticmethod
    def insert_returning(table: Table, data: dict[str, Any]) -> QueryWithParams:
        """
        Generate an INSERT query with RETURNING clause, using the keys in the
        data dict as columns and the values as values.
        """
        keys = ", ".join(data.keys())
        values = ", ".join(f":{key}" for key in data.keys())
        return (
            sqlalchemy.text(
                f"INSERT INTO {table.value} ({keys}) VALUES ({values}) RETURNING *"
            ),
            data,
        )

    @staticmethod
    def upsert_by_id_returning(table: Table, data: dict[str, Any]) -> QueryWithParams:
        """
        Generate an INSERT query with ON CONFLICT and RETURNING clauses,
        updating the row whenever there is a unique violation fot the primary key "id".
        """
        keys = ", ".join(data.keys())
        values = ", ".join(f":{key}" for key in data.keys())

        to_update = list(data.keys())
        to_update.remove("id")
        update_cols_string = ", ".join([f"{key} = EXCLUDED.{key}" for key in to_update])
        set_string = f"SET {update_cols_string}"

        return (
            sqlalchemy.text(
                f"""
            INSERT INTO {table.value} ({keys}) VALUES ({values})
                ON CONFLICT (id) DO UPDATE {set_string}
                RETURNING *
            """
            ),
            data,
        )

    @staticmethod
    def select_by_id(table: Table, *, id: UUID) -> QueryWithParams:
        """
        Generate a SELECT query for all the columns in the given table,
        filtering by `id` column.
        """
        return (
            sqlalchemy.text(f"SELECT * FROM {table.value} WHERE id = :id"),
            {"id": str(id)},
        )

    @staticmethod
    def delete_by_id(table: Table, *, id: UUID) -> QueryWithParams:
        """
        Generate a DELETE query for rows in the given table, filtering by `id`
        column.
        """
        return (
            sqlalchemy.text(f"DELETE FROM {table.value} WHERE id = :id"),
            {"id": str(id)},
        )
