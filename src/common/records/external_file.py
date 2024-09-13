# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import HttpUrl

from .records import QueryWithParams, Table
from .recurring_imports import UnstructuredImportData
from .user_records import UserRecord, UserRecordMapper, UserRecords


class Metadata(UnstructuredImportData, extra="ignore"):
    """
    Google Drive metadata derived fields for now.
    """

    kind: str | None = None
    shared: bool | None = None  # shared with others, not by others
    description: str | None = None
    exportLinks: dict[str, str] = dict()  # Google Drive


MetadataType = TypeVar("MetadataType", bound=UnstructuredImportData)
MetadataDict = dict[str, Any]


class ScreenshotProcessingParameters(UnstructuredImportData, extra="ignore"):
    prompt: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    preprocessing: bool | None = None


ScreenshotProcessingParametersType = TypeVar(
    "ScreenshotProcessingParametersType", bound=UnstructuredImportData
)
ScreenshotProcessingParametersDict = dict[str, Any]


class ExternalFile(UserRecord):
    recurring_import_id: UUID
    provider: str
    external_id: str
    version: str
    is_shared_by_other: bool | None = None
    filename: str
    extension: str | None = None
    starred: bool | None = None
    original_filename: str | None = None
    mime_type: str
    is_screenshot: bool | None = None
    screenshot_processing_parameters: ScreenshotProcessingParametersDict | None = (
        None  # TODO allow None also in Mapper
    )
    s3_path: str | None = None  # for thumbnail if screenshot
    indexable_text: str | None = None
    indexable_text_mime_type: str | None = None
    size_bytes: int
    size_x: int | None = None
    size_y: int | None = None
    view_link: HttpUrl | None = None
    download_link: HttpUrl | None = None
    resource_created_at: datetime | None = None
    resource_modified_at: datetime | None = None
    modified_at: datetime | None = None
    accessed_at: datetime | None = None
    metadata: MetadataDict | None = None

    def typed_screenshot_processing_parameters(
        self, metadata_cls: type[ScreenshotProcessingParametersType]
    ) -> ScreenshotProcessingParametersType:
        """
        Massage the screenshot_processing_parameters data dictionary into a validated instance of the
        given class.
        """
        return metadata_cls.model_validate(self.screenshot_processing_parameters)

    def typed_metadata(self, metadata_cls: type[MetadataType]) -> MetadataType:
        """
        Massage the metadata data dictionary into a validated instance of the
        given class.
        """
        return metadata_cls.model_validate(self.metadata)

    def __str__(self) -> str:
        return f"{self.provider} import {self.recurring_import_id}"


ExternalFileType = TypeVar("ExternalFileType", bound=ExternalFile)


class ExternalFileMapper(
    UserRecordMapper[ExternalFile],
):
    def __init__(self) -> None:
        super().__init__(ExternalFile)

    def to_row(self, record: ExternalFile) -> dict[str, Any]:
        data = super().to_row(record)
        # Special handling for unstructured data fields (stored as JSON in DB).
        data["metadata"] = json.dumps(record.metadata)
        data["screenshot_processing_parameters"] = json.dumps(
            record.screenshot_processing_parameters
        )
        return data


class ExternalFileRecords(
    UserRecords[ExternalFile],
):
    def __init__(self) -> None:
        super().__init__(Table.ExternalFile, ExternalFileMapper())

    def create_or_update(
        self,
        conn: sa.Connection,
        *,
        id: UUID,
        user_id: UUID,
        recurring_import_id: UUID,
        provider: str,
        external_id: str,
        version: str,
        is_shared_by_other: bool | None = None,
        starred: bool | None = None,
        filename: str,
        extension: str,
        original_filename: str,
        mime_type: str,
        indexable_text: str | None = None,
        indexable_text_mime_type: str | None = None,
        is_screenshot: bool,
        screenshot_processing_parameters: UnstructuredImportData,
        s3_path: str | None = None,
        size_bytes: int,
        size_x: int | None = None,
        size_y: int | None = None,
        view_link: HttpUrl | None = None,
        download_link: HttpUrl | None = None,
        metadata: UnstructuredImportData,
        resource_created_at: datetime | None = None,
        resource_modified_at: datetime | None = None,
    ) -> ExternalFile:
        now = datetime.now(timezone.utc)
        return super().upsert_by_id(
            conn,
            ExternalFile(
                id=id,
                created_at=now,
                recurring_import_id=recurring_import_id,
                user_id=user_id,
                provider=provider,
                external_id=external_id,
                version=version,
                is_shared_by_other=is_shared_by_other,
                starred=starred,
                filename=filename,
                extension=extension,
                original_filename=original_filename,
                mime_type=mime_type,
                indexable_text=indexable_text,
                indexable_text_mime_type=indexable_text_mime_type,
                is_screenshot=is_screenshot,
                screenshot_processing_parameters=screenshot_processing_parameters.db_safe_dict(),
                s3_path=s3_path,
                size_bytes=size_bytes,
                size_x=size_x,
                size_y=size_y,
                view_link=view_link,
                download_link=download_link,
                metadata=metadata.db_safe_dict(),
                resource_created_at=resource_created_at,
                resource_modified_at=resource_modified_at,
            ),
        )

    def delete_all_by_mime_type_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        mime_type: str,
    ) -> int:
        (query, params) = _SQL.delete_by_mime_type_by_user_id(
            table=self._table,
            user_id=user_id,
            mime_type=mime_type,
        )
        return conn.execute(query, params).rowcount

    def get_all_by_mime_type_by_user_id(
        self,
        conn: sa.Connection,
        *,
        user_id: UUID,
        mime_type: str,
    ) -> list[ExternalFile]:
        (query, params) = _SQL.select_by_mime_type_by_user_id(
            table=self._table,
            user_id=user_id,
            mime_type=mime_type,
        )
        rows = conn.execute(query, params).mappings().fetchall()
        return [self._mapper.from_row(row) for row in rows]


class _SQL:
    @staticmethod
    def select_by_mime_type_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        mime_type: str,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            SELECT *
                FROM {table.value}
                WHERE user_id = :user_id
                    AND mime_type = :mime_type
            """
            ),
            {"user_id": str(user_id), "mime_type": mime_type},
        )

    @staticmethod
    def delete_by_mime_type_by_user_id(
        *,
        table: Table,
        user_id: UUID,
        mime_type: str,
    ) -> QueryWithParams:
        return (
            sa.text(
                f"""
            DELETE FROM {table.value}
                WHERE user_id = :user_id
                    AND mime_type = :mime_type
            """
            ),
            {"user_id": str(user_id), "mime_type": mime_type},
        )
