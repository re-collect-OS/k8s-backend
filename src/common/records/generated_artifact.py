# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

import sqlalchemy as sa

from .records import Table
from .recurring_imports import UnstructuredImportData
from .user_records import UserRecord, UserRecordMapper, UserRecords


class Metadata(UnstructuredImportData, extra="ignore"):
    queries: list[str] | None = None
    stack_ids: list[UUID] | None = None
    artifact_ids: list[UUID] | None = None
    model_parameters: dict[str, Any] | None = None


MetadataType = TypeVar("MetadataType", bound=UnstructuredImportData)
MetadataDict = dict[str, Any]


class GeneratedArtifact(UserRecord):
    kind: str
    indexable_text: str
    mime_type: str
    metadata: MetadataDict | None = None
    generated_at: datetime
    created_at: datetime
    accessed_at: datetime | None = None

    def typed_metadata(self, metadata_cls: type[MetadataType]) -> MetadataType:
        """
        Massage the metadata data dictionary into a validated instance of the
        given class.
        """
        return metadata_cls.model_validate(self.metadata)

    def __str__(self) -> str:
        return f"GeneratedArtifact {self.id}"


GeneratedArtifactType = TypeVar("GeneratedArtifactType", bound=GeneratedArtifact)


class GeneratedArtifactMapper(
    UserRecordMapper[GeneratedArtifact],
):
    def __init__(self) -> None:
        super().__init__(GeneratedArtifact)

    def to_row(self, record: GeneratedArtifact) -> dict[str, Any]:
        data = super().to_row(record)
        # Special handling for unstructured data fields (stored as JSON in DB).
        data["metadata"] = json.dumps(record.metadata)
        return data


class GeneratedArtifactRecords(
    UserRecords[GeneratedArtifact],
):
    def __init__(self) -> None:
        super().__init__(Table.GeneratedArtifact, GeneratedArtifactMapper())

    def create_or_update(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        user_id: UUID,
        kind: str,
        indexable_text: str,
        mime_type: str,
        metadata: UnstructuredImportData,
        generated_at: datetime,
    ) -> GeneratedArtifact:
        now = datetime.now(timezone.utc)
        return super().upsert_by_id(
            conn,
            GeneratedArtifact(
                id=id,
                user_id=user_id,
                kind=kind,
                indexable_text=indexable_text,
                mime_type=mime_type,
                metadata=metadata.db_safe_dict(),
                generated_at=generated_at,
                created_at=now,
                accessed_at=None,
            ),
        )
