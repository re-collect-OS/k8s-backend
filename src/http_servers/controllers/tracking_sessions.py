# -*- coding: utf-8 -*-
from uuid import UUID

import sqlalchemy
import sqlalchemy.exc
from loguru import logger
from pydantic import BaseModel

from common.records.tracking_session import BaseTrackingSession, TrackingSessionRecords
from common.sqldb import sqldb_from_env
from recollect.helpers.url import normalize_url

from ..middleware.auth import CognitoUser


class ExternalModel(BaseModel):
    sessions: list[BaseTrackingSession]


class IgnoredChange(BaseModel):
    id: str
    reason: str


class LogResponse(BaseModel):
    ignored_changes: list[IgnoredChange] = list()


class TrackingSessionsController:
    def __init__(
        self,
        ext_model_cls: type[ExternalModel] = ExternalModel,
    ) -> None:
        self._db = sqldb_from_env()
        self.ext_model_cls = ext_model_cls
        self._records = TrackingSessionRecords()

    def log_sessions(
        self,
        auth_user: CognitoUser,
        sessions: list[BaseTrackingSession],
    ) -> LogResponse:
        with self._db.begin() as conn:
            failed = self._log_sessions(
                conn,
                user_id=auth_user.id,
                sessions=sessions,
            )

        logger.success(
            "Logged {count} tracking sessions (of which {failed} were ignored) for user {user}.",
            count=len(sessions),
            failed=len(failed),
            user=auth_user.id,
        )
        return LogResponse(ignored_changes=failed)

    def _log_sessions(
        self,
        conn: sqlalchemy.Connection,
        user_id: UUID,
        sessions: list[BaseTrackingSession],
    ) -> list[IgnoredChange]:
        failed_session_ids: list[IgnoredChange] = []
        for session in sessions:
            try:
                self._records.create(
                    conn,
                    user_id=user_id,
                    id=session.id,
                    url=normalize_url(session.url),
                    started_at=session.started_at,
                    finished_at=session.finished_at,
                    time_in_tab=session.time_in_tab,
                    max_scroll_depth=session.max_scroll_depth,
                    click_count=session.click_count,
                    highlight_count=session.highlight_count,
                )
            except sqlalchemy.exc.IntegrityError as e:
                failed_session_ids.append(
                    IgnoredChange(id=str(session.id), reason=str(e))
                )
        return failed_session_ids
