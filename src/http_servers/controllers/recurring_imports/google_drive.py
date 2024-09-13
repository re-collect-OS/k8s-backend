# -*- coding: utf-8 -*-
from datetime import timedelta
from typing import Optional
from uuid import UUID

import fastapi
import sqlalchemy
from loguru import logger
from pydantic import BaseModel

from common import env
from common.integrations import google_api
from common.integrations.google_api import GoogleDriveAuthRedirect
from common.records.recurring_imports import RecurringImport
from common.records.recurring_imports_google import (
    GoogleImportContext as GoogleDriveInternalContext,
)
from common.records.recurring_imports_google import GoogleImportSettings
from common.records.recurring_imports_google import (
    GoogleImportSettings as GoogleDriveInternalSettings,
)
from http_servers.middleware.auth import CognitoUser

from .base import (
    BaseRecurringImportsController,
    ExternalModel,
    ExternalSettingsModel,
    ExternalSettingsPatchModel,
)

_CLIENT_ID = env.require_str("GOOGLE_OAUTH_CLIENT_ID")
_CLIENT_SECRET = env.require_str("GOOGLE_OAUTH_CLIENT_SECRET")

_SYNC_INTERVAL = (
    timedelta(minutes=1) if env.is_local_development() else timedelta(minutes=31)
)
_GOOGLE_DRIVE_SCOPES = env.require_str("GOOGLE_DRIVE_SCOPES")


class GoogleDriveExternalSettings(ExternalSettingsModel):
    """
    Google recurring import integration settings.

    Attributes:
        oauth2_params: OAuth2 parameters to complete Google's OAuth2 PKCE flow
            and obtain credentials. Required on submission, never returned.
        id: The Google account id. Should not be supplied on submission
            (value is discovered on successful OAuth2 flow); always returned in
            responses.
        email: The email associated with the Google account. Should not be supplied on submission
            (value is discovered on successful OAuth2 flow); always returned in
            responses.
    """

    # step 2, get token:
    # after getting code in response to successful auth, we
    # can request the token - Google will verify the code_verifier
    # using the previously sent code and hash method
    class OAuth2Params(BaseModel):
        code: str
        code_verifier: str
        redirect_uri: str

    # Inputs, never returned.
    oauth2_params: Optional[OAuth2Params] = None
    # Outputs, always returned.
    id: Optional[str] = None
    email: Optional[str] = None


class GoogleDriveExternalSettingsPatch(ExternalSettingsPatchModel):
    oauth2_params: Optional[GoogleDriveExternalSettings.OAuth2Params] = None


class GoogleDriveExternalModel(ExternalModel):
    pass


class GoogleDriveController(
    BaseRecurringImportsController[
        GoogleDriveInternalSettings,
        GoogleDriveExternalSettings,
        GoogleDriveExternalSettingsPatch,
        GoogleDriveExternalModel,
    ],
):
    def __init__(self) -> None:
        super().__init__(
            int_settings_cls=GoogleDriveInternalSettings,
            ext_settings_cls=GoogleDriveExternalSettings,
            ext_patch_cls=GoogleDriveExternalSettingsPatch,
            ext_model_cls=GoogleDriveExternalModel,
            sync_interval=_SYNC_INTERVAL,
        )
        self.scope = _GOOGLE_DRIVE_SCOPES

    @staticmethod
    def source() -> RecurringImport.Source:
        return RecurringImport.Source.GOOGLE_DRIVE

    def unique_identifer(
        self,
        user_id: UUID,
        settings: GoogleDriveInternalSettings,
    ) -> str:
        return f"{self.source().value}:{str(user_id)}:{settings.id}"

    def validate_proposed_external(self, proposed: GoogleDriveExternalSettings) -> None:
        # Hit the Google Drive API using the supplied access token to prevent
        # creation of a destined-to-fail recurring import.
        if proposed.oauth2_params is None:
            raise fastapi.HTTPException(
                status_code=400,
                detail="oauth2_params are required",
            )

        if proposed.id is not None or proposed.email is not None:
            raise fastapi.HTTPException(
                status_code=400,
                detail="google account id and/or email cannot be set",
            )

    def validate_update_in_tx(
        self,
        current: GoogleImportSettings,
        proposed: GoogleImportSettings,
    ) -> None:
        if proposed.id != current.id:
            raise fastapi.HTTPException(
                status_code=400,
                detail="Supplied OAuth2 credentials are for a different user",
            )

    def create(
        self,
        auth_user: CognitoUser,
        config: GoogleDriveExternalSettings,
    ) -> GoogleDriveExternalModel:
        self._check_readonly_killswitch()

        self.validate_proposed_external(config)
        assert config.oauth2_params is not None
        (oauth2_credentials, profile) = self._get_oauth2_credentials_and_profile(
            oauth2_params=config.oauth2_params,
        )

        settings = GoogleDriveInternalSettings(
            id=profile.id,
            email=profile.email,
        )
        context = GoogleDriveInternalContext(oauth2_credentials=oauth2_credentials)
        with self._db.begin() as conn:
            try:
                unique_id = self.unique_identifer(auth_user.id, settings)
                unique_id = RecurringImport.deterministic_id(unique_id)

                import_record = self._records.create(
                    conn,
                    id=unique_id,
                    user_id=auth_user.id,
                    source=self.source(),
                    settings=settings,
                    context=context,
                    interval=self._sync_interval,
                    enabled=config.enabled,
                )
            except sqlalchemy.exc.IntegrityError as e:
                if "duplicate key value violates unique constraint" in str(e):
                    raise fastapi.HTTPException(
                        status_code=409,
                        detail="A matching recurring sync already exists.",
                    )
                else:
                    raise e

        logger.success(
            f"Created new recurring {import_record} for user {auth_user.id}."
        )
        return self._to_external(import_record)

    def update_settings(
        self,
        auth_user: CognitoUser,
        import_id: UUID,
        ext_proposed: GoogleDriveExternalSettings,
    ) -> GoogleDriveExternalModel:
        self._check_readonly_killswitch()

        self.validate_proposed_external(ext_proposed)
        assert ext_proposed.oauth2_params is not None
        (oauth2_token, profile) = self._get_oauth2_credentials_and_profile(
            oauth2_params=ext_proposed.oauth2_params,
        )
        # See create() for details on why we need to update context here.
        int_proposed = GoogleDriveInternalSettings(
            id=profile.id,
            email=profile.email,
        )
        updated_context = GoogleDriveInternalContext(oauth2_credentials=oauth2_token)
        with self._db.begin() as conn:
            import_record = self._get_if_owner(conn, import_id, auth_user)
            current = self._int_settings_cls.model_validate(import_record.settings)
            self.validate_update_in_tx(current=current, proposed=int_proposed)

            updated_record = self._update_settings(
                conn,
                import_id=import_id,
                settings=int_proposed,
                enabled=ext_proposed.enabled,
            )
            # Differs from other implementations in that we also update context.
            self._records.update_context(
                conn,
                id=import_id,
                context=updated_context,
            )

        logger.success(f"Updated settings for {import_record} for user {auth_user.id}.")
        return self._to_external(updated_record)

    def patch_settings(
        self,
        auth_user: CognitoUser,
        import_id: UUID,
        patch: GoogleDriveExternalSettingsPatch,
    ) -> GoogleDriveExternalModel:
        self._check_readonly_killswitch()

        settings: Optional[GoogleDriveInternalSettings] = None
        updated_context: Optional[GoogleDriveInternalContext] = None

        if patch.oauth2_params is not None:
            (oauth2_token, profile) = self._get_oauth2_credentials_and_profile(
                oauth2_params=patch.oauth2_params,
            )
            settings = GoogleDriveInternalSettings(
                id=profile.id,
                email=profile.email,
            )
            updated_context = GoogleDriveInternalContext(
                oauth2_credentials=oauth2_token
            )

        with self._db.begin() as conn:
            import_record = self._get_if_owner(conn, import_id, auth_user)
            current_settings = self._int_settings_cls.model_validate(
                import_record.settings
            )

            if settings is not None:
                self.validate_update_in_tx(
                    current=current_settings,
                    proposed=settings,
                )

            enabled = import_record.enabled
            if patch.enabled is not None:
                enabled = patch.enabled
            updated_record = self._update_settings(
                conn,
                import_id=import_id,
                settings=settings or current_settings,
                enabled=enabled,
            )
            if updated_context is not None:
                # Update context if OAuth2 credentials changed.
                self._records.update_context(
                    conn,
                    id=import_id,
                    context=updated_context,
                )

        logger.success(f"Patched settings for {import_record} for user {auth_user.id}.")
        return self._to_external(record=updated_record)

    def get_authorization_redirect(
        self,
        redirect_uri: str,
    ) -> GoogleDriveAuthRedirect:
        redirect_data = google_api.get_authorization_redirect(
            client_id=_CLIENT_ID,
            scope=self.scope,
            redirect_uri=redirect_uri,
        )
        return redirect_data

    def _get_oauth2_credentials_and_profile(
        self,
        oauth2_params: GoogleDriveExternalSettings.OAuth2Params,
    ) -> tuple[google_api.OAuth2Credentials, google_api.Profile]:
        try:
            oauth2_credentials = google_api.get_credentials(
                code=oauth2_params.code,
                code_verifier=oauth2_params.code_verifier,
                redirect_uri=oauth2_params.redirect_uri,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
            )
            profile = google_api.get_self_profile(oauth2_credentials.access_token)
        except Exception as e:
            logger.opt(exception=e).warning(f"credential verify exception: {e}")
            raise fastapi.HTTPException(
                status_code=500,
                detail=f"Unable to verify credentials: {str(e)}",
            )

        return oauth2_credentials, profile
