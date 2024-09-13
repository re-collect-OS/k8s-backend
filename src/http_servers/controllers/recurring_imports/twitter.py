# -*- coding: utf-8 -*-
from datetime import timedelta
from typing import Optional
from uuid import UUID

import fastapi
import sqlalchemy
from loguru import logger
from pydantic import BaseModel

from common import env
from common.integrations import twitter_api
from common.records.recurring_imports import RecurringImport
from common.records.recurring_imports_twitter import (
    TwitterImportAuthContext as TwitterInternalAuthContext,
)
from common.records.recurring_imports_twitter import TwitterImportSettings
from common.records.recurring_imports_twitter import (
    TwitterImportSettings as TwitterInternalSettings,
)
from http_servers.middleware.auth import CognitoUser

from .base import (
    BaseRecurringImportsController,
    ExternalModel,
    ExternalSettingsModel,
    ExternalSettingsPatchModel,
)

_CLIENT_ID = env.require_str("TWITTER_USER_AUTH_CLIENT_ID")
_SYNC_INTERVAL = (
    timedelta(minutes=1) if env.is_local_development() else timedelta(hours=24)
)


class TwitterExternalSettings(ExternalSettingsModel):
    """
    Twitter recurring import integration settings.

    Attributes:
        oauth2_params: OAuth2 parameters to complete Twitter's OAuth2 PKCE flow
            and obtain credentials. Required on submission, never returned.
        user_id: The Twitter user_id. Should not be supplied on submission
            (value is discovered on successful OAuth2 flow); always returned in
            responses.
        username: The Twitter username. Should not be supplied on submission
            (value is discovered on successful OAuth2 flow); always returned in
            responses.
    """

    class OAuth2Params(BaseModel):
        code: str
        code_verifier: str
        redirect_uri: str

    # Inputs, never returned.
    oauth2_params: Optional[OAuth2Params] = None
    # Outputs, always returned.
    user_id: Optional[str] = None
    username: Optional[str] = None


class TwitterExternalSettingsPatch(ExternalSettingsPatchModel):
    oauth2_params: Optional[TwitterExternalSettings.OAuth2Params] = None


class TwitterExternalModel(ExternalModel):
    pass


class TwitterController(
    BaseRecurringImportsController[
        TwitterInternalSettings,
        TwitterExternalSettings,
        TwitterExternalSettingsPatch,
        TwitterExternalModel,
    ],
):
    def __init__(self) -> None:
        super().__init__(
            int_settings_cls=TwitterInternalSettings,
            ext_settings_cls=TwitterExternalSettings,
            ext_patch_cls=TwitterExternalSettingsPatch,
            ext_model_cls=TwitterExternalModel,
            sync_interval=_SYNC_INTERVAL,
        )

    @staticmethod
    def source() -> RecurringImport.Source:
        return RecurringImport.Source.TWITTER

    def unique_identifer(
        self,
        user_id: UUID,
        settings: TwitterInternalSettings,
    ) -> str:
        return f"{self.source().value}:{str(user_id)}:{settings.user_id}"

    def validate_proposed_external(
        self,
        proposed: TwitterExternalSettings,
    ) -> None:
        if proposed.oauth2_params is None:
            raise fastapi.HTTPException(
                status_code=400,
                detail="oauth2_params are required",
            )

        if proposed.user_id is not None or proposed.username is not None:
            raise fastapi.HTTPException(
                status_code=400,
                detail="user_id and/or username cannot be set",
            )

    def validate_update_in_tx(
        self,
        current: TwitterImportSettings,
        proposed: TwitterImportSettings,
    ) -> None:
        if proposed.user_id != current.user_id:
            raise fastapi.HTTPException(
                status_code=400,
                detail="Supplied OAuth2 credentials are for a different user",
            )

    def create(
        self,
        auth_user: CognitoUser,
        config: TwitterExternalSettings,
    ) -> TwitterExternalModel:
        self._check_readonly_killswitch()

        self.validate_proposed_external(config)
        assert config.oauth2_params is not None
        (oauth2_credentials, profile) = self._get_oauth2_credentials_and_profile(
            oauth2_params=config.oauth2_params,
        )

        # Unlike other integrations, there's no simple 1:1 mapping between the
        # external settings model and the internal settings model due to how
        # Twitter's OAuth2 PKCE flow works. We accept OAuth2 data to complete
        # the PKCE flow which returns:
        #  - an expiring access token (used to make API requests on behalf of
        #    the user)
        #  - a single-use refresh token (which can be used to obtain a new
        #    expiring access token + another single-use refresh token)
        #  - the TTL of the access token
        #
        # Given the ephemeral nature of these inputs and outputs (it's all
        # single-use as access constantly needs to be refreshed, i.e. internal
        # state), these are best kept in the _context_ of the RecurringImport
        # record — which must be present at time of first import.
        # Being internal state that is constantly changing without user action,
        # it doesn't make sense to return these credentials as settings to the
        # client.
        #
        # Instead (and in order to have _some_ useful information to return to
        # the clients), we use the newly minted credentials to read the user's
        # profile and store the user_id and username in the settings model.
        # Since the user_id/username "settings" properties are derived rather
        # than supplied, the API enforces read-only access.
        #
        # For details in the conditional extension of access required to perform
        # Twitter API calls, see `workers/importers/twitter_importer.py`.
        settings = TwitterInternalSettings(
            user_id=profile.id,
            username=profile.username,
        )
        context = TwitterInternalAuthContext(oauth2_credentials=oauth2_credentials)
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
        ext_proposed: TwitterExternalSettings,
    ) -> TwitterExternalModel:
        self._check_readonly_killswitch()

        self.validate_proposed_external(ext_proposed)
        assert ext_proposed.oauth2_params is not None
        (oauth2_token, profile) = self._get_oauth2_credentials_and_profile(
            oauth2_params=ext_proposed.oauth2_params,
        )
        # See create() for details on why we need to update context here.
        int_proposed = TwitterInternalSettings(
            user_id=profile.id,
            username=profile.username,
        )
        updated_context = TwitterInternalAuthContext(oauth2_credentials=oauth2_token)
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
            self._records.merge_context(
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
        patch: TwitterExternalSettingsPatch,
    ) -> TwitterExternalModel:
        self._check_readonly_killswitch()

        settings: Optional[TwitterInternalSettings] = None
        updated_context: Optional[TwitterInternalAuthContext] = None

        if patch.oauth2_params is not None:
            (oauth2_token, profile) = self._get_oauth2_credentials_and_profile(
                oauth2_params=patch.oauth2_params,
            )
            settings = TwitterInternalSettings(
                user_id=profile.id,
                username=profile.username,
            )
            updated_context = TwitterInternalAuthContext(
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
                self._records.merge_context(
                    conn,
                    id=import_id,
                    context=updated_context,
                )

        logger.success(f"Patched settings for {import_record} for user {auth_user.id}.")
        return self._to_external(record=updated_record)

    def _get_oauth2_credentials_and_profile(
        self,
        oauth2_params: TwitterExternalSettings.OAuth2Params,
    ) -> tuple[twitter_api.OAuth2Credentials, twitter_api.Profile]:
        try:
            oauth2_credentials = twitter_api.get_credentials(
                code=oauth2_params.code,
                code_verifier=oauth2_params.code_verifier,
                redirect_uri=oauth2_params.redirect_uri,
                client_id=_CLIENT_ID,
            )
            profile = twitter_api.get_self_profile(oauth2_credentials.access_token)
        except twitter_api.ClientError as e:
            if e.is_credential_error:
                raise fastapi.HTTPException(
                    status_code=400,
                    detail=f"Invalid Twitter credentials: {e.detailed_description()}",
                )

            raise fastapi.HTTPException(
                status_code=500,
                detail=f"Unable to verify credentials: {e.detailed_description()}",
            )

        return oauth2_credentials, profile
