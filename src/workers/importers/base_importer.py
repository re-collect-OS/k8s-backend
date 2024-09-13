# -*- coding: utf-8 -*-
from abc import ABC
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from common.records.recurring_imports import RecurringImport, UnstructuredImportData


class Importer(ABC):
    """Contract for a content importer for a recurring import."""

    @dataclass
    class Result:
        """
        Result of an import operation, along with any contextual information
        for the result.

        In other languages, this would be an enum with associated values; e.g.:
        https://docs.swift.org/swift-book/documentation/the-swift-programming-language/enumerations/#Associated-Values
        """

        status: RecurringImport.Status
        imported: int
        detail: Optional[str]
        updated_context: Optional[UnstructuredImportData]
        delay: Optional[timedelta]

        @staticmethod
        def success(
            imported: int,
            updated_context: Optional[UnstructuredImportData],
        ) -> "Importer.Result":
            """
            Static factory method for a successful import result.

            Args:
                imported (int):
                    The number of records imported.
                updated_context (Optional[UnstructuredImportData]):
                    Updated recurring import context to be persisted, if any.
            """
            return Importer.Result(
                status=RecurringImport.Status.SUCCESS,
                imported=imported,
                detail=None,
                updated_context=updated_context,
                delay=None,
            )

        @staticmethod
        def no_new_content() -> "Importer.Result":
            """
            Static factory method for a successful import result with no new
            content.
            """
            return Importer.Result(
                status=RecurringImport.Status.NO_NEW_DATA,
                imported=0,
                detail="No new data since last check.",
                updated_context=None,
                delay=None,
            )

        @staticmethod
        def transient_failure(
            detail: str,
            delay: Optional[timedelta] = None,
        ) -> "Importer.Result":
            """
            Static factory method for a transient failure import result that
            should be retried, either "immediately" (after a brief hiatus) or
            after a delay.

            Args:
                detail (str):
                    A human-readable description of the failure. This should be
                    human-readable and concise, suitable for display at the UI
                    level (i.e. no technical details or stack traces).
                delay (Optional[timedelta], optional):
                    Delay to introduce before retrying the import, if any.
            """
            return Importer.Result(
                status=RecurringImport.Status.TRANSIENT_FAILURE,
                imported=0,
                detail=detail,
                updated_context=None,
                delay=delay,
            )

        @staticmethod
        def permanent_failure(detail: str) -> "Importer.Result":
            """
            Static factory method for a permanent failure import result that
            should not be retried.

            Implementations should only use this for errors that are known to
            be unrecoverable, such as invalid credentials or a malformed URL.

            Args:
                detail (str):
                    A human-readable description of the failure. This should be
                    human-readable and concise, suitable for display at the UI
                    level (i.e. no technical details or stack traces).
            """
            return Importer.Result(
                status=RecurringImport.Status.PERMANENT_FAILURE,
                imported=0,
                detail=detail,
                updated_context=None,
                delay=None,
            )

    def should_skip(self, import_record: RecurringImport) -> bool:
        """Whether the import for the specified record should be skipped."""
        return False

    def import_content(self, import_record: RecurringImport) -> Result:
        """Perform one import cycle for the specified recurring import."""
        raise NotImplementedError()
