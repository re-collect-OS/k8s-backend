# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from typing import TypeVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from .records import QueryWithParams, Table
from .user_records import UserRecord, UserRecordMapper, UserRecords


class BaseTrackingSession(BaseModel):
    id: UUID
    url: str
    started_at: datetime
    finished_at: datetime
    time_in_tab: timedelta
    max_scroll_depth: int
    click_count: int
    highlight_count: int


class TrackingSession(UserRecord, BaseTrackingSession):
    pass


TrackingSessionType = TypeVar("TrackingSessionType", bound=TrackingSession)


class TrackingSessionMapper(
    UserRecordMapper[TrackingSession],
):
    def __init__(self) -> None:
        super().__init__(TrackingSession)


class TrackingSessionRecords(
    UserRecords[TrackingSession],
):
    def __init__(self) -> None:
        super().__init__(Table.TrackingSessions, TrackingSessionMapper())

    def create(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        user_id: UUID,
        url: str,
        started_at: datetime,
        finished_at: datetime,
        time_in_tab: timedelta,
        max_scroll_depth: int,
        click_count: int,
        highlight_count: int,
    ) -> TrackingSession:
        now = datetime.now(timezone.utc)
        return super().insert(
            conn,
            TrackingSession(
                id=id,
                created_at=now,
                user_id=user_id,
                url=url,
                started_at=started_at,
                finished_at=finished_at,
                time_in_tab=time_in_tab,
                max_scroll_depth=max_scroll_depth,
                click_count=click_count,
                highlight_count=highlight_count,
            ),
        )

    def delete_all_by_url_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        url: str,
    ) -> int:
        (query, params) = _SQL.delete_by_url_by_user_id(
            table=self._table,
            user_id=user_id,
            url=url,
        )
        return conn.execute(query, params).rowcount

    def get_all_by_url_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        url: str,
    ) -> list[TrackingSession]:
        (query, params) = _SQL.select_by_url_by_user_id(
            table=self._table,
            user_id=user_id,
            url=url,
        )
        rows = conn.execute(query, params).mappings().fetchall()
        return [self._mapper.from_row(row) for row in rows]


# CREATE TABLE tracking_session (
#     user_id TEXT NOT NULL REFERENCES user_account (id),
#     created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT current_timestamp(),
#     id VARCHAR(64) NOT NULL PRIMARY KEY,
#     started_at TIMESTAMP WITH TIME ZONE NOT NULL,
#     finished_at TIMESTAMP WITH TIME ZONE NOT NULL,
#     doc_id TEXT NOT NULL REFERENCES sentence_source (doc_id),
#     time_in_tab INTERVAL NOT NULL,
#     max_scroll_depth SMALLINT NOT NULL,
#     click_count SMALLINT NOT NULL,
#     highlight_count SMALLINT NOT NULL,
#     INDEX idx_doc_user_id (doc_id, user_id),


class _SQL:
    @staticmethod
    def select_by_url_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        url: str,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            SELECT *
                FROM {table.value}
                WHERE user_id = :user_id
                    AND url = :url
            """
            ),
            {"user_id": str(user_id), "url": url},
        )

    @staticmethod
    def delete_by_url_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        url: str,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            DELETE FROM {table.value}
                WHERE user_id = :user_id
                    AND url = :url
            """
            ),
            {"user_id": str(user_id), "url": url},
        )
