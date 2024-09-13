# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, Optional, TypeVar
from uuid import UUID

import fastapi
import sqlalchemy
import sqlalchemy.exc
from loguru import logger
from pydantic import BaseModel, SerializeAsAny, ValidationError
from sqlalchemy.orm import Session

from common import env
from common.features.features import Killswitch
from common.records.recurring_imports import (
    RecurringImport,
    RecurringImportRecords,
    UnstructuredImportData,
)
from common.sqldb import sqldb_from_env
from recollect.crud import crud_card, crud_urlstate
from recollect.schemas.urlstate import REMOVING

from ...middleware.auth import CognitoUser

InternalSettingsType = TypeVar(
    "InternalSettingsType",
    bound=UnstructuredImportData,
)


class AuthRedirect(BaseModel):
    pass


class ExternalSettingsModel(BaseModel):
    """
    Base class for external API representation of recurring import settings,
    containing shared settings for all recurring import types.

    Subclasses should add other integration-specific attributes as needed.

    Attributes:
        enabled (bool): Whether the recurring import is enabled.
    """

    enabled: bool

    def unpack(
        self,
        internal_cls: type[InternalSettingsType],
    ) -> tuple[bool, InternalSettingsType]:
        dump_sans_enabled = self.model_dump(exclude={"enabled"})
        return self.enabled, internal_cls.model_validate(dump_sans_enabled)


class ExternalSettingsPatchModel(BaseModel):
    """
    Base class for external API representation of recurring import settings
    selective updates (patches), containing shared settings for all recurring
    import types.

    Subclasses should add other integration-specific attributes as needed.

    Attributes:
        enabled (bool): Whether the recurring import is enabled.
    """

    enabled: Optional[bool] = None


class Status(BaseModel):
    """
    External API representation of a recurring import's status.

    Attributes:
        last_run (datetime): Timestamp of the last run.
        last_run_status (str): Status of the last run.
        last_run_detail (str): Human-friendly details of the last run.
    """

    last_run: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_detail: Optional[str] = None


class ExternalModel(BaseModel):
    """
    Base class for external API representation of a recurring import.

    Subclasses typically won't need to add any extra attributes.

    Attributes:
        id (str): A unique identifier for this recurring import.
        config (ExternalSettingsModel*): The recurring import's settings.
        status (Status): The recurring import's status.
    """

    id: UUID
    # NB: SerializeAsAny required here to properly serialize subclasses.
    # See:
    # - https://github.com/pydantic/pydantic/issues/8162
    # - https://docs.pydantic.dev/latest/concepts/serialization/#serializing-with-duck-typing
    settings: SerializeAsAny[ExternalSettingsModel]
    status: Optional[Status] = None


ExternalSettingsType = TypeVar(
    "ExternalSettingsType",
    bound=ExternalSettingsModel,
)
ExternalSettingsPatchType = TypeVar(
    "ExternalSettingsPatchType",
    bound=ExternalSettingsPatchModel,
)
ExternalModelType = TypeVar(
    "ExternalModelType",
    bound=ExternalModel,
)


class ExternalModelList(
    BaseModel,
    Generic[ExternalModelType],
):
    count: int
    items: list[ExternalModelType]

    def __init__(self, items: list[ExternalModelType]) -> None:
        super().__init__(
            count=len(items),
            items=items,
        )


class BaseRecurringImportsController(
    Generic[
        InternalSettingsType,
        ExternalSettingsType,
        ExternalSettingsPatchType,
        ExternalModelType,
    ],
    ABC,
):
    """
    Base class for recurring import controllers.

    Default implementation assumes a simple case where every external settings
    optional field maps (by name & type) to an internal settings type field.

    Subclasses must implement the following methods:
        - `source`
        - `generate_new_record_id`

    Subclasses may perform optionally override the following pre-create/update/patch
    methods:
        - `validate_proposed_external`
        - `validate_update_in_tx`
    """

    def __init__(
        self,
        int_settings_cls: type[InternalSettingsType],
        ext_settings_cls: type[ExternalSettingsType],
        ext_patch_cls: type[ExternalSettingsPatchType],
        ext_model_cls: type[ExternalModelType],
        sync_interval: timedelta,
        killswitch: Optional[Killswitch] = None,
    ) -> None:
        self._int_settings_cls = int_settings_cls
        self.ext_settings_cls = ext_settings_cls
        self.ext_patch_cls = ext_patch_cls
        self._ext_model_cls = ext_model_cls
        self._sync_interval = sync_interval
        self._readonly_killswitch = killswitch

        self._db = sqldb_from_env()
        self._records = RecurringImportRecords()

    # --- For subclasses to implement/override

    @staticmethod
    @abstractmethod
    def source() -> RecurringImport.Source:
        raise NotImplementedError()

    @abstractmethod
    def unique_identifer(
        self,
        user_id: UUID,
        settings: InternalSettingsType,
    ) -> str:
        """
        Generate a deterministic new unique identifier for the recurring
        import record.

        Examples:
            f"rss:{str(user_id)}:{settings.feed_url}"
            f"readwise_v2:{str(user_id)}:{settings.account_id}"
        """
        raise NotImplementedError()

    def validate_proposed_external(
        self,
        proposed: ExternalSettingsType,
    ) -> None:
        """
        Pre-create/update external model validation hook for subclasses.

        Subclasses should raise 4xx HTTPException if validation fails.

        Should be used to discard obviously invalid external settings proposals
        for create/update operations.
        """
        pass

    def validate_update_in_tx(
        self,
        current: InternalSettingsType,
        proposed: InternalSettingsType,
    ) -> None:
        """
        Pre-persistence validation hook for update and patch operations.

        Subclasses should raise 4xx HTTPException if validation fails.

        Allows subclasses to perform further, current-state-aware validation
        logic after the external settings representation model has been
        converted into an internal one, prior to persisting the update.
        An example would be rejecting an update to a field that should not be
        modified.

        Note: Avoid running expensive/blocking logic here, as this method is
        called within a database transaction. If such logic is a requirement,
        consider a full override of `update_settings` or `patch_settings` to
        run such logic outside a transaction.
        """
        pass

    # --- Interface

    def create(
        self,
        auth_user: CognitoUser,
        config: ExternalSettingsType,
    ) -> ExternalModelType:
        """Validate and create a new recurring import."""
        self._check_readonly_killswitch()

        self.validate_proposed_external(config)
        (enabled, settings) = self._to_internal(config)
        with self._db.begin() as conn:
            import_record = self._create(
                conn,
                user_id=auth_user.id,
                settings=settings,
                enabled=enabled,
            )

        logger.success(
            "Created new recurring {record} for user {user}.",
            record=import_record,
            user=auth_user.id,
        )
        return self._to_external(import_record)

    def list(self, auth_user: CognitoUser) -> ExternalModelList[ExternalModelType]:
        """List all recurring imports for the given user."""
        with self._db.begin() as conn:
            records = self._records.get_all_by_source_by_user_id(
                conn,
                user_id=auth_user.id,
                source=self.source(),
            )

        return ExternalModelList(
            items=[self._to_external(r) for r in records],
        )

    def read(self, auth_user: CognitoUser, import_id: UUID) -> ExternalModelType:
        """Get a single recurring import by ID."""
        with self._db.begin() as conn:
            record = self._get_if_owner(conn, import_id, auth_user)

        return self._to_external(record)

    def update_settings(
        self,
        auth_user: CognitoUser,
        import_id: UUID,
        ext_proposed: ExternalSettingsType,
    ) -> ExternalModelType:
        """Validate and update settings for an existing recurring import."""
        self._check_readonly_killswitch()

        self.validate_proposed_external(ext_proposed)
        (enabled, int_proposed) = self._to_internal(ext_proposed)
        with self._db.begin() as conn:
            import_record = self._get_if_owner(conn, import_id, auth_user)
            current = self._int_settings_cls.model_validate(import_record.settings)
            self.validate_update_in_tx(current=current, proposed=int_proposed)

            updated_record = self._update_settings(
                conn,
                import_id=import_id,
                settings=int_proposed,
                enabled=enabled,
            )

        logger.success(
            "Updated settings for {record} for user {user}.",
            record=import_record,
            user=auth_user.id,
        )
        return self._to_external(updated_record)

    def patch_settings(
        self,
        auth_user: CognitoUser,
        import_id: UUID,
        patch: ExternalSettingsPatchType,
    ) -> ExternalModelType:
        """Validate and patch settings for an existing recurring import."""
        self._check_readonly_killswitch()

        with self._db.begin() as conn:
            import_record = self._get_if_owner(conn, import_id, auth_user)
            current_settings = self._int_settings_cls.model_validate(
                import_record.settings
            )

            patched = self._apply_patch(current_settings, patch)
            self.validate_update_in_tx(current=current_settings, proposed=patched)

            enabled = import_record.enabled
            if patch.enabled is not None:
                enabled = patch.enabled
            updated_record = self._update_settings(
                conn,
                import_id=import_id,
                settings=patched,
                enabled=enabled,
            )

        logger.success(
            "Patched settings for {record} for user {user}.",
            record=import_record,
            user=auth_user.id,
        )
        return self._to_external(record=updated_record)

    def run_now(
        self,
        auth_user: CognitoUser,
        import_id: UUID,
    ) -> None:
        """Trigger an early run for a recurring import."""
        self._check_readonly_killswitch()

        instant = datetime.now(timezone.utc)
        with self._db.begin() as conn:
            # Possible optimization: set by import_id+user_id to avoid having to
            # fetch the record just to validate ownership.
            import_record = self._get_if_owner(conn, import_id, auth_user)
            if not self._records.update_next_run_at(
                conn,
                id=import_id,
                instant=instant,
            ):
                raise fastapi.HTTPException(
                    status_code=500,
                    detail="Update failed; record no longer exists.",
                )

        logger.success(
            "Scheduled next run of {record} for user {user} at {instant}.",
            record=import_record,
            user=auth_user.id,
            instant=instant,
        )

    def delete(self, auth_user: CognitoUser, import_id: UUID) -> None:
        """Delete a recurring import by ID."""
        self._check_readonly_killswitch()

        with self._db.begin() as conn:
            # Possible optimization: delete by import_id+user_id
            # to avoid having to fetch the record first.
            import_record = self._get_if_owner(conn, import_id, auth_user)

            self._records.delete_by_id(conn, id=import_id)
            (artifacts, cards) = self._delete_imported_content(conn, import_id)

        logger.success(
            "Deleted {record} ({artifacts} artifacts, {cards} cards) for {user}.",
            record=import_record,
            artifacts=artifacts,
            cards=cards,
            user=auth_user.id,
        )

    def delete_all(self, auth_user: CognitoUser) -> None:
        """
        Delete all recurring imports of this controller's managed type for the
        given user, along with their generated content (artifacts, cards).
        """
        self._check_readonly_killswitch()

        with self._db.begin() as conn:
            imports = self._records.get_all_by_source_by_user_id(
                conn,
                user_id=auth_user.id,
                source=self.source(),
            )
            total_artifacts = 0
            total_cards = 0
            for import_record in imports:
                self._records.delete_by_id(conn, id=import_record.id)
                (artifacts, cards) = self._delete_imported_content(
                    transaction=conn,
                    import_id=import_record.id,
                )
                total_artifacts += artifacts
                total_cards += cards
            total_imports = len(imports)

        logger.success(
            "Deleted {count} {source} recurring imports "
            "({total_artifacts} artifacts, {total_cards} cards) for user {user}.",
            count=total_imports,
            source=self.source().value,
            total_artifacts=total_artifacts,
            total_cards=total_cards,
            user=auth_user.id,
        )

    def _create(
        self,
        conn: sqlalchemy.Connection,
        user_id: UUID,
        settings: InternalSettingsType,
        enabled: bool,
    ) -> RecurringImport:
        """
        Record creation helper, massages duplicate key collision failures into
        more informative 409 errors (instead of 500).
        """
        try:
            unique_id = self.unique_identifer(user_id, settings)
            unique_id = RecurringImport.deterministic_id(unique_id)

            return self._records.create(
                conn,
                id=unique_id,
                user_id=user_id,
                source=self.source(),
                settings=settings,
                interval=self._sync_interval,
                enabled=enabled,
            )
        except sqlalchemy.exc.IntegrityError as e:
            if "duplicate key value violates unique constraint" in str(e):
                raise fastapi.HTTPException(
                    status_code=409,
                    detail="A matching recurring sync already exists.",
                )
            else:
                raise e

    def _get_if_owner(
        self,
        conn: sqlalchemy.Connection,
        import_id: UUID,
        authenticated_user: CognitoUser,
    ) -> RecurringImport:
        """
        Get recurring import by ID, raising 404 if record does not exist and
        403 if owner does not match authenticated user.
        """
        record = self._records.get_by_id(conn, id=import_id)
        if record is None:
            raise fastapi.HTTPException(
                status_code=404,
                detail="Record does not exist.",
            )

        if record.user_id != authenticated_user.id:
            raise fastapi.HTTPException(
                status_code=403,
                detail="User does not own record.",
            )

        return record

    def _apply_patch(
        self,
        current: InternalSettingsType,
        patch: ExternalSettingsPatchType,
    ) -> InternalSettingsType:
        """
        Apply patch to current settings, returning the patched settings.

        Only applies to patch operations. Returned result will be validated with
        `validate_update_in_tx` before being persisted.

        Assumes simple case where every patch type optional field maps
        (by name & type) to an internal settings type field.
        """
        # Default implementation; assumes simple case where every patch optional
        # field maps (by name and type) to an internal settings field.
        # For custom mappings, override in subclass.
        return current.model_copy(
            update={k: v for k, v in patch.model_dump(exclude_none=True).items()}
        )

    def _update_settings(
        self,
        conn: sqlalchemy.Connection,
        import_id: UUID,
        settings: InternalSettingsType,
        enabled: bool,
    ) -> RecurringImport:
        """Update the settings for a recurring import."""
        # To consider: a single update query to update both fields and return
        # the updated record (vs 3 queries). These 3 always happen in tandem.
        self._records.update_enabled(conn, id=import_id, enabled=enabled)
        self._records.update_settings(conn, id=import_id, settings=settings)
        updated = self._records.get_by_id(conn, id=import_id)
        if updated is None:
            # Should never happen assuming proper transactional isolation.
            raise fastapi.HTTPException(
                status_code=500,
                detail="Update failed; record no longer exists.",
            )

        return updated

    def _delete_imported_content(
        self,
        transaction: sqlalchemy.Connection,
        import_id: UUID,
    ) -> tuple[int, int]:
        """
        Delete all imported content originated by the target recurring import.

        Returns a tuple of (number of deleted artifacts, number of deleted cards).
        """
        with Session(transaction) as sess:
            artifacts = crud_urlstate.urlstate.update_state_where_recurring_import_id(
                sess,
                recurring_import_id=import_id,
                state=REMOVING,
                commit=False,
            )
            cards = crud_card.card.delete_by_recurring_import_id(
                sess,
                recurring_import_id=import_id,
                commit=False,
            )

        return (artifacts, cards)

    def _to_internal(
        self,
        proposed: ExternalSettingsType,
    ) -> tuple[bool, InternalSettingsType]:
        """
        Convert a external settings representation to its internal version.

        Assumes simple case where every external settings optional field maps
        (by name & type) to an internal settings type field.
        """
        try:
            return proposed.unpack(self._int_settings_cls)
        except ValidationError as e:
            raise fastapi.HTTPException(
                status_code=400,
                detail=f"invalid settings: {e}",
            )

    def _to_external(self, record: RecurringImport) -> ExternalModelType:
        """
        Convert a recurring import record to its external API representation.

        Assumes simple case where every internal settings optional field maps
        (by name & type) to an external settings type field.
        """
        status: Optional[Status] = None
        if record.last_run_finished_at is not None:
            status = Status(
                last_run=record.last_run_finished_at,
                last_run_status=(
                    record.last_run_status.value if record.last_run_status else None
                ),
                last_run_detail=record.last_run_detail,
            )

        return self._ext_model_cls(
            id=record.id,
            settings=self.ext_settings_cls(
                enabled=record.enabled,
                **record.settings,
            ),
            status=status,
        )

    def _check_readonly_killswitch(self) -> None:
        if (
            self._readonly_killswitch is not None
            and self._readonly_killswitch.is_enabled()
        ):
            raise fastapi.HTTPException(
                status_code=503,
                detail=(
                    f"Read-only killswitch {self._readonly_killswitch.key} enabled."
                    if not env.is_production()
                    else "Service temporarily unavailable."
                ),
            )


AnyController = BaseRecurringImportsController[
    Any,
    ExternalSettingsModel,
    ExternalSettingsPatchModel,
    ExternalModel,
]
