# -*- coding: utf-8 -*-
from abc import ABC
from typing import Generic, TypeVar
from uuid import UUID

import sqlalchemy

from .records import QueryWithParams, Record, RecordMapper, Records, Table


class UserRecord(Record):
    """Base class for all SQL records owned by a given user."""

    user_id: UUID


UserRecordType = TypeVar("UserRecordType", bound=UserRecord)


class UserRecordMapper(
    RecordMapper[UserRecordType],
):
    def __init__(self, record_cls: type[UserRecordType]) -> None:
        super().__init__(record_cls)


class UserRecords(
    Records[UserRecordType],
    Generic[UserRecordType],
    ABC,
):
    """
    Abstract class for a table managing records owned by a given user.

    All rows in the table are assumed to have both an `id` (unique, primary key)
    and a `user_id` column. For improved performance, `user_id` should be
    indexed (or an fkey reference).
    """

    def __init__(
        self,
        table: Table,
        record_mapper: UserRecordMapper[UserRecordType],
    ) -> None:
        super().__init__(table, record_mapper)

    def get_all_by_user_id(
        self,
        conn: sqlalchemy.Connection,
        *,
        user_id: UUID,
    ) -> list[UserRecordType]:
        """Retrieve all records for the given user."""

        # TODO(bruno): pagination
        (query, params) = UserSQL.select_by_user_id(self._table, user_id=user_id)
        rows = conn.execute(query, params).mappings().fetchall()
        return [self._mapper.from_row(row) for row in rows]

    def count_by_user_id(
        self,
        conn: sqlalchemy.Connection,
        *,
        user_id: UUID,
    ) -> int:
        """Count all records for the given user."""
        (query, params) = UserSQL.count_by_user_id(self._table, user_id=user_id)
        return conn.execute(query, params).scalar() or 0

    def delete_by_user_id(
        self,
        conn: sqlalchemy.Connection,
        *,
        user_id: UUID,
    ) -> int:
        """
        Delete all records for the given user, returning the number deleted rows.
        """
        (query, params) = UserSQL.delete_by_user_id(self._table, user_id=user_id)
        return conn.execute(query, params).rowcount


class UserSQL:
    @staticmethod
    def select_by_user_id(table: Table, *, user_id: UUID) -> QueryWithParams:
        return (
            sqlalchemy.text(f"SELECT * FROM {table.value} WHERE user_id = :user_id"),
            {"user_id": str(user_id)},
        )

    @staticmethod
    def count_by_user_id(table: Table, *, user_id: UUID) -> QueryWithParams:
        return (
            sqlalchemy.text(
                f"SELECT COUNT(*) FROM {table.value} WHERE user_id = :user_id"
            ),
            {"user_id": str(user_id)},
        )

    @staticmethod
    def delete_by_user_id(table: Table, *, user_id: UUID) -> QueryWithParams:
        return (
            sqlalchemy.text(f"DELETE FROM {table.value} WHERE user_id = :user_id"),
            {"user_id": str(user_id)},
        )


class GenericUserRecords(UserRecords[UserRecord]):
    """
    Alias for UserRecords[UserRecord]. Makes it easier to declare types for
    parameters when interacting with tables that have a user_id column in a
    generic way e.g. deleting all records in a given table by user id, when
    the actual type of record doesn't matter (see: account_deleter).
    """

    # ⬆ See account_deleter daemon for example use case.

    def __init__(self, table: Table) -> None:
        super().__init__(table, UserRecordMapper(UserRecord))

    def insert(
        self,
        conn: sqlalchemy.Connection,
        record: UserRecord,
    ) -> UserRecord:
        # Calling this is likely a programming mistake; fail loudly.
        # Highly unlikely to succeed if attempted since mapper declared in
        # constructor will only work with id, created_at, and user_id fields.
        raise NotImplementedError(
            "GenericUserRecords does not support insert(), "
            "use the record-specific subclass of UserRecords instead"
        )
