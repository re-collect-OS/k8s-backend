# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

import sqlalchemy as sa

from .records import QueryWithParams, Table
from .recurring_imports import UnstructuredImportData
from .user_records import UserRecord, UserRecordMapper, UserRecords


class Metadata(UnstructuredImportData, extra="ignore"):
    query: str | None = None


MetadataType = TypeVar("MetadataType", bound=UnstructuredImportData)
MetadataDict = dict[str, Any]


class Interaction(UserRecord):
    event_id: UUID
    artifact_id: UUID | None = None
    kind: str
    metadata: MetadataDict | None = None
    timestamp: datetime

    def typed_metadata(self, metadata_cls: type[MetadataType]) -> MetadataType:
        """
        Massage the metadata data dictionary into a validated instance of the
        given class.
        """
        return metadata_cls.model_validate(self.metadata)

    def __str__(self) -> str:
        return f"Interaction event {self.event_id}"


InteractionType = TypeVar("InteractionType", bound=Interaction)


class InteractionMapper(
    UserRecordMapper[Interaction],
):
    def __init__(self) -> None:
        super().__init__(Interaction)

    def to_row(self, record: Interaction) -> dict[str, Any]:
        data = super().to_row(record)
        # Special handling for unstructured data fields (stored as JSON in DB).
        data["metadata"] = json.dumps(record.metadata)
        return data


class InteractionRecords(
    UserRecords[Interaction],
):
    def __init__(self) -> None:
        super().__init__(Table.Interaction, InteractionMapper())

    def create_or_update(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        user_id: UUID,
        event_id: UUID,
        artifact_id: UUID | None = None,
        kind: str,
        metadata: UnstructuredImportData,
        timestamp: datetime,
    ) -> Interaction:
        now = datetime.now(timezone.utc)
        return super().upsert_by_id(
            conn,
            Interaction(
                id=id,
                user_id=user_id,
                event_id=event_id,
                artifact_id=artifact_id,
                kind=kind,
                metadata=metadata.db_safe_dict(),
                timestamp=timestamp,
                created_at=now,
            ),
        )

    def delete_all_by_event_id_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        event_id: UUID,
    ) -> int:
        (query, params) = _SQL.delete_by_event_id_by_user_id(
            table=self._table,
            user_id=user_id,
            event_id=event_id,
        )
        return conn.execute(query, params).rowcount

    def get_all_by_event_id_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        event_id: UUID,
    ) -> list[Interaction]:
        (query, params) = _SQL.select_by_event_id_by_user_id(
            table=self._table,
            user_id=user_id,
            event_id=event_id,
        )
        rows = conn.execute(query, params).mappings().fetchall()
        return [self._mapper.from_row(row) for row in rows]


class _SQL:
    @staticmethod
    def select_by_event_id_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        event_id: UUID,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            SELECT *
                FROM {table.value}
                WHERE user_id = :user_id
                    AND event_id = :event_id
            """
            ),
            {"user_id": str(user_id), "event_id": str(event_id)},
        )

    @staticmethod
    def delete_by_event_id_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        event_id: UUID,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            DELETE FROM {table.value}
                WHERE user_id = :user_id
                    AND event_id = :event_id
            """
            ),
            {"user_id": str(user_id), "event_id": str(event_id)},
        )
