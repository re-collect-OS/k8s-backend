# -*- coding: utf-8 -*-
from datetime import timedelta
from uuid import UUID

from common import env
from common.records.recurring_imports import RecurringImport

from ...middleware.auth import CognitoUser
from .base import (
    BaseRecurringImportsController,
    ExternalModel,
    ExternalSettingsModel,
    ExternalSettingsPatchModel,
    UnstructuredImportData,
)

# Note: The Apple Notes recurring import is executed by the Mac app
# This controller is a stub so we can have a way to register the integration
# in the system and give customers a way to pause and disable the import or
# their Apple Notes.

# Native clients will run on their own schedule so we want to avoid scheduling sync jobs
_SYNC_INTERVAL = timedelta(days=36500)


class AppleNotesInternalSettings(UnstructuredImportData):
    pass


class AppleNotesExternalSettings(ExternalSettingsModel):
    pass


class AppleNotesExternalSettingsPatch(ExternalSettingsPatchModel):
    pass


class AppleNotesExternalModel(ExternalModel):
    pass


class AppleNotesController(
    BaseRecurringImportsController[
        AppleNotesInternalSettings,
        AppleNotesExternalSettings,
        AppleNotesExternalSettingsPatch,
        AppleNotesExternalModel,
    ],
):
    def __init__(self) -> None:
        super().__init__(
            int_settings_cls=AppleNotesInternalSettings,
            ext_settings_cls=AppleNotesExternalSettings,
            ext_patch_cls=AppleNotesExternalSettingsPatch,
            ext_model_cls=AppleNotesExternalModel,
            sync_interval=_SYNC_INTERVAL,
        )

    @staticmethod
    def source() -> RecurringImport.Source:
        return RecurringImport.Source.APPLE_NOTES

    def unique_identifer(
        self,
        user_id: UUID,
        settings: AppleNotesInternalSettings,
    ) -> str:
        return f"{self.source().value}:{str(user_id)}"

    def run_now(
        self,
        auth_user: CognitoUser,
        import_id: UUID,
    ) -> None:
        pass
