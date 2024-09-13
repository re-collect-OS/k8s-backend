# -*- coding: utf-8 -*-
from typing import Optional

from ..integrations.twitter_api import OAuth2Credentials
from .recurring_imports import UnstructuredImportData


class TwitterImportSettings(UnstructuredImportData):
    """
    Twitter recurring import settings.

    Attributes:
        user_id: The Twitter account user_id.
        username: The Twitter account username.
    """

    user_id: str
    username: str


class TwitterImportContext(UnstructuredImportData):
    """
    Import context for Twitter recurring imports.

    Attributes:
        oauth2_credentials: The OAuth2 credentials for the Twitter account.
            Unlike other integrations, Twitter's context must be populated when
            the recurring import is created.
        latest_tweet_ids_syncd: The last 10 tweet IDs syncd
    """

    oauth2_credentials: OAuth2Credentials
    latest_tweet_ids_syncd: Optional[list[str]] = None


class TwitterImportAuthContext(UnstructuredImportData):
    oauth2_credentials: OAuth2Credentials


class TwitterImportSyncContext(UnstructuredImportData):
    latest_tweet_ids_syncd: list[str]
