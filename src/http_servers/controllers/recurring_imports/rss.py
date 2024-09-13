# -*- coding: utf-8 -*-
from datetime import timedelta
from typing import Optional
from uuid import UUID

import fastapi

from common import env
from common.records.recurring_imports import RecurringImport
from common.records.recurring_imports_rss import (
    RSSImportSettings as RSSInternalSettings,
)

from .base import (
    BaseRecurringImportsController,
    ExternalModel,
    ExternalSettingsModel,
    ExternalSettingsPatchModel,
)

_SYNC_INTERVAL = (
    timedelta(minutes=1) if env.is_local_development() else timedelta(days=1)
)


class RSSExternalSettings(ExternalSettingsModel):
    """
    RSS Feed recurring import integration settings.

    Attributes:
        url (str): The RSS feed URL.
        import_content_links (bool): Whether to import content links.
            When enabled, links in feed entry contents will also be imported.
    """

    url: str
    import_content_links: bool


class RSSExternalSettingsPatch(ExternalSettingsPatchModel):
    import_content_links: Optional[bool] = None


class RSSExternalModel(ExternalModel):
    pass


class RSSController(
    BaseRecurringImportsController[
        RSSInternalSettings,
        RSSExternalSettings,
        RSSExternalSettingsPatch,
        RSSExternalModel,
    ],
):
    def __init__(self) -> None:
        super().__init__(
            int_settings_cls=RSSInternalSettings,
            ext_settings_cls=RSSExternalSettings,
            ext_patch_cls=RSSExternalSettingsPatch,
            ext_model_cls=RSSExternalModel,
            sync_interval=_SYNC_INTERVAL,
        )

    @staticmethod
    def source() -> RecurringImport.Source:
        return RecurringImport.Source.RSS_FEED

    def unique_identifer(
        self,
        user_id: UUID,
        settings: RSSInternalSettings,
    ) -> str:
        return f"{self.source().value}:{str(user_id)}:{settings.url}"

    def validate_update_in_tx(
        self,
        current: RSSInternalSettings,
        proposed: RSSInternalSettings,
    ) -> None:
        if proposed.url != current.url:
            raise fastapi.HTTPException(
                status_code=400,
                detail="url cannot be modified",
            )
