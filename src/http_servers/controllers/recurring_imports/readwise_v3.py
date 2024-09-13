# -*- coding: utf-8 -*-
from datetime import timedelta
from typing import Optional
from uuid import UUID

import fastapi
import requests

from common import env
from common.integrations import readwise_api
from common.records.recurring_imports import RecurringImport
from common.records.recurring_imports_readwise import (
    ReadwiseV3ImportSettings as ReadwiseInternalSettings,
)

from .base import (
    BaseRecurringImportsController,
    ExternalModel,
    ExternalSettingsModel,
    ExternalSettingsPatchModel,
)

_SYNC_INTERVAL = (
    timedelta(minutes=1) if env.is_local_development() else timedelta(hours=2)
)


class ReadwiseV3ExternalSettings(ExternalSettingsModel):
    """
    Readwise recurring import integration settings.

    Attributes:
        account_id (str): A client-generated identifier for this account, e.g.
            'default', 'foo@bar.baz`, etc. Cannot be modified once set.
        access_token (str): The readwise account access token.
    """

    account_id: str
    access_token: str


class ReadwiseV3ExternalSettingsPatch(ExternalSettingsPatchModel):
    access_token: Optional[str] = None


class ReadwiseV3ExternalModel(ExternalModel):
    pass


class ReadwiseV3Controller(
    BaseRecurringImportsController[
        ReadwiseInternalSettings,
        ReadwiseV3ExternalSettings,
        ReadwiseV3ExternalSettingsPatch,
        ReadwiseV3ExternalModel,
    ],
):
    def __init__(self) -> None:
        super().__init__(
            int_settings_cls=ReadwiseInternalSettings,
            ext_settings_cls=ReadwiseV3ExternalSettings,
            ext_patch_cls=ReadwiseV3ExternalSettingsPatch,
            ext_model_cls=ReadwiseV3ExternalModel,
            sync_interval=_SYNC_INTERVAL,
        )

    @staticmethod
    def source() -> RecurringImport.Source:
        return RecurringImport.Source.READWISE_V3

    def unique_identifer(
        self,
        user_id: UUID,
        settings: ReadwiseInternalSettings,
    ) -> str:
        return f"{self.source().value}:{str(user_id)}:{settings.account_id}"

    def validate_proposed_external(self, proposed: ReadwiseV3ExternalSettings) -> None:
        # Hit the readwise v3 API using the supplied access token to prevent
        # creation of a destined-to-fail recurring import.
        try:
            readwise_api.get_v3_data(
                token=proposed.access_token,
                page_limit=1,
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                raise fastapi.HTTPException(
                    status_code=400,
                    detail="Invalid access token",
                )

            detail = "Unable to validate access token"
            if not env.is_production():
                detail += f" ({e})"
            raise fastapi.HTTPException(
                status_code=500,
                detail=detail,
            )

    def validate_update_in_tx(
        self,
        current: ReadwiseInternalSettings,
        proposed: ReadwiseInternalSettings,
    ) -> None:
        if proposed.account_id != current.account_id:
            raise fastapi.HTTPException(
                status_code=400,
                # It's used to deterministically generate recurring import ID
                detail="account_id cannot be modified",
            )
