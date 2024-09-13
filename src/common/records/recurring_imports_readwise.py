# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from .recurring_imports import UnstructuredImportData


class ReadwiseV2ImportSettings(UnstructuredImportData):
    account_id: str
    access_token: str


class ReadwiseV2ImportContext(UnstructuredImportData):
    last_updated_at: Optional[datetime] = None


class ReadwiseV3ImportSettings(UnstructuredImportData):
    """
    Settings for a Readwise recurring import.

    Attributes:
        account_id (str): A client-generated identifier for this account, e.g.
            'default', or an email. Should be unique per user (to support
            multiple accounts).
        access_token (str): The readwise account access token.
    """

    account_id: str
    access_token: str


class ReadwiseV3ImportContext(UnstructuredImportData):
    """Import context for Readwise v3 (Reader) recurring imports."""

    # No context info is currently required between imports.
    pass
