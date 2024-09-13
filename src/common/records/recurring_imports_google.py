# -*- coding: utf-8 -*-
from ..integrations.google_api import OAuth2Credentials
from .recurring_imports import UnstructuredImportData


class GoogleImportSettings(UnstructuredImportData):
    """
    Google recurring import settings.
    """

    id: str
    email: str


class GoogleImportContext(UnstructuredImportData):
    """
    Import context for Google recurring imports.
    """

    oauth2_credentials: OAuth2Credentials
