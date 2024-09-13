# -*- coding: utf-8 -*-
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional, TypeVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from .records import Table
from .user_records import QueryWithParams, UserRecord, UserRecordMapper, UserRecords


class UnstructuredImportData(BaseModel):
    """
    Base class for unstructured recurring import data.

    Used to store integration-specific settings and context data.
    """

    def db_safe_dict(self) -> dict[str, Any]:
        # NB: This is just so we have a single source of truth if we want to
        # change how we serialize to DB.
        return self.model_dump(exclude_none=True, mode="json")

    def db_safe_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


SettingsType = TypeVar("SettingsType", bound=UnstructuredImportData)
ContextType = TypeVar("ContextType", bound=UnstructuredImportData)

# NB: Non-JSONable values will raise when trying to save to DB.
SettingsDict = dict[str, Any]
ContextDict = dict[str, Any]


class RecurringImport(UserRecord):
    """
    Representation for a recurring import record.

    The `settings` and `context` fields are JSON-serialized dictionaries that
    hold unstructured, integration-specific data. The `typed_settings` and
    `typed_context` methods can be used to massage them into validated instances
    of concrete settings/context classes.

    Attributes:
        source (RecurringImport.Source): The source of the import data.
        settings (dict[str, Any]): Integration-specific settings for the import.
        context (dict[str, Any]): Integration-specific context for the import.
        enabled (bool): Whether the import is enabled. Disabled imports will not
            be picked up by the recurring import dispatcher.
        interval (timedelta): Tnterval between runs of the import.
        next_run_at (datetime): Timestamp of the next scheduled run.
        last_run_finished_at (datetime): Timestamp of last run's completion,
            if any.
        last_run_status (RecurringImport.Status): Status of last run, if any.
        last_run_detail (str): Human-readable details of last run, if any.
    """

    class Source(Enum):
        # Consider relaxing this into just a string. This record should be
        # agnostic to implementation details and this is an extra step in
        # supporting a new integration (but it's nice to have a list of all
        # supported integrations in one place.)
        APPLE_NOTES = "apple-notes"
        RSS_FEED = "rss"
        READWISE_V2 = "readwise-v2"
        READWISE_V3 = "readwise-v3"
        TWITTER = "twitter"
        GOOGLE_DRIVE = "google-drive"

    class Status(Enum):
        SUCCESS = "success"
        NO_NEW_DATA = "no_new_data"
        PERMANENT_FAILURE = "permanent_failure"
        TRANSIENT_FAILURE = "transient_failure"

    source: Source
    settings: SettingsDict
    context: Optional[ContextDict]
    enabled: bool
    interval: timedelta
    next_run_at: datetime
    last_run_finished_at: Optional[datetime]
    last_run_status: Optional[Status]
    last_run_detail: Optional[str]

    def typed_settings(self, settings_cls: type[SettingsType]) -> SettingsType:
        """
        Massage the settings data dictionary into a validated instance of the
        given class.
        """
        return settings_cls.model_validate(self.settings)

    def typed_context(self, context_cls: type[ContextType]) -> Optional[ContextType]:
        """
        Massage the context data dictionary into a validated instance of the
        given class.
        """
        if self.context is None:
            return None
        return context_cls.model_validate(self.context)

    def __str__(self) -> str:
        return f"{self.source.value} import {self.id}"


class RecurringImportMapper(
    UserRecordMapper[RecurringImport],
):
    def __init__(self) -> None:
        super().__init__(RecurringImport)

    def to_row(self, record: RecurringImport) -> dict[str, Any]:
        data = super().to_row(record)
        # Special handling for unstructured data fields (stored as JSON in DB).
        data["settings"] = json.dumps(record.settings)
        data["context"] = json.dumps(record.context)
        return data


class RecurringImportRecords(
    UserRecords[RecurringImport],
):
    def __init__(self) -> None:
        super().__init__(Table.RecurringImports, RecurringImportMapper())

    def create(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        user_id: UUID,
        source: RecurringImport.Source,
        settings: UnstructuredImportData,
        context: Optional[UnstructuredImportData] = None,
        interval: timedelta,
        enabled: bool = True,
        first_run_at: Optional[datetime] = None,
    ) -> RecurringImport:
        """Create a new recurring import."""
        now = datetime.now(timezone.utc)
        return super().insert(
            conn,
            RecurringImport(
                id=id,
                created_at=now,
                user_id=user_id,
                source=source,
                settings=settings.db_safe_dict(),
                context=context.db_safe_dict() if context else None,
                enabled=enabled,
                interval=interval,
                next_run_at=first_run_at or now,
                last_run_finished_at=None,
                last_run_status=None,
                last_run_detail=None,
            ),
        )

    def get_all_by_source_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        source: RecurringImport.Source,
    ) -> list[RecurringImport]:
        """Retrieve all recurring imports matching source for the given user."""
        (query, params) = _SQL.select_by_source_by_user_id(
            table=self._table,
            user_id=user_id,
            source=source,
        )
        rows = conn.execute(query, params).mappings().fetchall()
        return [self._mapper.from_row(row) for row in rows]

    def update_enabled(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        enabled: bool = True,
    ) -> bool:
        """Enable or disable the recurring import matching given ID."""
        (query, params) = _SQL.update_set_enabled(id=id, enabled=enabled)
        return conn.execute(query, params).rowcount > 0

    def update_settings(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        settings: UnstructuredImportData,
    ) -> bool:
        """Update the settings for the recurring import matching given ID."""
        (query, params) = _SQL.update_by_id_set_settings(id=id, settings=settings)
        return conn.execute(query, params).rowcount > 0

    def update_context(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        context: UnstructuredImportData,
    ) -> bool:
        """Update the context for the recurring import matching given ID."""
        (query, params) = _SQL.update_by_id_set_context(id=id, context=context)
        return conn.execute(query, params).rowcount > 0

    def merge_context(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        context: UnstructuredImportData,
    ) -> bool:
        """Update the context for the recurring import matching given ID."""
        (query, params) = _SQL.update_by_id_merge_context(id=id, context=context)
        return conn.execute(query, params).rowcount > 0

    def update_next_run_at(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        instant: datetime,
    ) -> bool:
        """Update the next run timestamp for the recurring import matching given ID."""
        (query, params) = _SQL.update_by_id_set_next_run_at(
            id=id,
            next_run_at=instant,
        )
        return conn.execute(query, params).rowcount > 0

    def update_last_run_status(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        finished_at: datetime,
        status: RecurringImport.Status,
        detail: Optional[str] = None,
    ) -> bool:
        """
        Update the last run timestamp and status for the recurring import
        matching given ID.
        """
        (query, params) = _SQL.update_by_id_set_last_run_fields(
            id=id,
            finished_at=finished_at,
            status=status,
            detail=detail,
        )
        return conn.execute(query, params).rowcount > 0

    def reschedule_due(
        self,
        conn: sa.Connection,
        *,
        instant: datetime,
        limit: int = 10,
    ) -> list[RecurringImport]:
        """
        Bump the `next_run_at` timestamp (now + frequency) for up to `limit`
        recurring imports that are due to run at-or-before the given `instant`,
        returning the updated records.
        """
        (query, params) = _SQL.reschedule_due_returning(
            instant=instant,
            limit=limit,
        )
        rows = conn.execute(query, params).mappings().fetchall()
        return [self._mapper.from_row(row) for row in rows]


# Schema snapshot, for reference (2023-11-27):
#
# CREATE TABLE recurring_imports (
#     id VARCHAR(64) NOT NULL PRIMARY KEY,
#     created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT current_timestamp(),
#     user_id TEXT NOT NULL REFERENCES user_account (id),
#     source VARCHAR(50) NOT NULL,
#     settings JSON NOT NULL,
#     context JSON,
#     enabled BOOLEAN NOT NULL DEFAULT true,
#     interval INTERVAL NOT NULL,
#     next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
#     last_run_finished_at TIMESTAMP WITH TIME ZONE,
#     last_run_status VARCHAR(50),
#     last_run_detail VARCHAR(100),
#
#     INDEX idx_user_id (user_id),
#     INDEX idx_next_run_enabled (next_run_at, enabled)
# )
class _SQL:
    @staticmethod
    def select_by_source_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        source: RecurringImport.Source,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            SELECT *
                FROM {table.value}
                WHERE user_id = :user_id
                    AND source = :source
            """
            ),
            {"user_id": str(user_id), "source": source.value},
        )

    @staticmethod
    def update_set_enabled(
        *,
        id: UUID,
        enabled: bool,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            UPDATE {Table.RecurringImports.value}
                SET enabled = :enabled
                WHERE id = :id
            """
            ),
            {"id": str(id), "enabled": enabled},
        )

    @staticmethod
    def update_by_id_set_settings(
        *,
        id: UUID,
        settings: UnstructuredImportData,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            UPDATE {Table.RecurringImports.value}
                SET settings = :settings
                WHERE id = :id
            """
            ),
            {"id": str(id), "settings": settings.db_safe_json()},
        )

    @staticmethod
    def update_by_id_set_context(
        *,
        id: UUID,
        context: UnstructuredImportData,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            UPDATE {Table.RecurringImports.value}
                SET context = :context
                WHERE id = :id
            """
            ),
            {"id": str(id), "context": context.db_safe_json()},
        )

    @staticmethod
    def update_by_id_merge_context(
        *,
        id: UUID,
        context: UnstructuredImportData,
    ) -> QueryWithParams:
        # Merge partial context into existing context
        return (
            sa.text(
                f"""
            UPDATE {Table.RecurringImports.value}
                SET context = COALESCE(context, '{{}}'::json)::jsonb || '{context.db_safe_json()}'::jsonb
                WHERE id = :id
            """
            ),
            {"id": str(id)},
        )

    @staticmethod
    def update_by_id_set_next_run_at(
        *,
        id: UUID,
        next_run_at: datetime,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            UPDATE {Table.RecurringImports.value}
                SET next_run_at = :next_run_at
                WHERE id = :id
            """
            ),
            {"id": str(id), "next_run_at": next_run_at},
        )

    @staticmethod
    def update_by_id_set_last_run_fields(
        *,
        id: UUID,
        finished_at: datetime,
        status: RecurringImport.Status,
        detail: Optional[str],
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            UPDATE {Table.RecurringImports.value}
                SET last_run_finished_at = :finished_at,
                    last_run_status = :status,
                    last_run_detail = :detail
                WHERE id = :id
            """
            ),
            {
                "id": str(id),
                "finished_at": finished_at,
                "status": status.value,
                "detail": detail,
            },
        )

    @staticmethod
    def reschedule_due_returning(
        *,
        instant: datetime,
        limit: int,
    ) -> QueryWithParams:
        table = Table.RecurringImports.value
        return (
            sa.text(
                f"""
            WITH slice AS (
                SELECT id
                    FROM {table}
                    WHERE next_run_at <= :instant
                        AND enabled = true
                    ORDER BY next_run_at
                    LIMIT :limit
            )
            UPDATE {table}
                SET next_run_at = {sa.func.current_timestamp()} + interval
                FROM slice
                WHERE {table}.id = slice.id
                RETURNING {table}.*;
            """
            ),
            {"instant": instant, "limit": limit},
        )
