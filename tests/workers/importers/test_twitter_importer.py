# -*- coding: utf-8 -*-
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence
from unittest.mock import Mock, call
from uuid import UUID

import pytest
import sqlalchemy
from datadog.dogstatsd.base import DogStatsd
from hamcrest import (
    assert_that,
    contains_string,
    equal_to,
    greater_than,
    has_length,
    is_,
)
from pytest_localserver.http import WSGIServer
from sqlalchemy.orm import Session

from common.integrations import twitter_api
from common.records.records import Record
from common.records.recurring_imports import RecurringImport, RecurringImportRecords
from common.records.recurring_imports_twitter import (
    TwitterImportContext,
    TwitterImportSettings,
)
from recollect import crud
from recollect.schemas.compositions import TweetScraped
from recollect.schemas.urlstate import PROCESSING_REQUIRED
from tests.test_lib import record_helpers
from workers.importers.twitter_importer import TwitterImporter

from ...test_lib.services import TestServices


@pytest.fixture
def sql_db(external_deps: TestServices) -> sqlalchemy.Engine:
    return external_deps.sql_db_client(truncate_all_tables=True)


StartResponseFunc = Callable[[str, Sequence[tuple[str, str]]], None]


@pytest.mark.integration
@pytest.mark.skip(
    reason="Flaky WSGI server; client sometimes hangs waiting for server "
    "response. Must be run manually to avoid spurious CI failures."
)
def test_twitter_importer_success_with_credentials_refresh(
    sql_db: sqlalchemy.Engine,
) -> None:
    # --- Set up fixtures ---
    def twitter_mock(
        environ: dict[str, Any],
        start_response: StartResponseFunc,
    ) -> Sequence[Any]:
        path: str = environ["PATH_INFO"]
        if path == "/oauth2/token":
            status = "200 OK"
            response_text = open(_OAUTH2_TOKEN_OK).read()
        elif path == "/users/42/bookmarks":
            status = "200 OK"
            response_text = open(_GET_BOOKMARKS_OK).read()
        else:
            status = "404 Not Found"
            response_text = "{}"

        headers = [("Content-type", "application/json")]
        start_response(status, headers)
        return [response_text.encode("utf-8")]

    server = WSGIServer(application=twitter_mock)
    twitter_api.BASE_URL = server.url
    server.start()

    try:
        awhile_back = datetime.now(timezone.utc) - timedelta(minutes=5)
        record = _create_recurring_twitter_import(
            sql_db,
            twitter_user_id="42",  # matching path above
            credentials_expiration=awhile_back,
        )

        # constraints on card table require an existing user
        with Session(sql_db) as session:
            crud.user_account.create(
                session,
                obj_in=record_helpers.user_account(user_id=record.user_id),
            )

        metrics = Mock(DogStatsd)
        recurring_imports = RecurringImportRecords()

        # --- Run test ---
        result = TwitterImporter(
            sql_db,
            metrics,
            records=recurring_imports,
            client_id="irrelevant",
        ).import_content(record)

        # --- Confirm side-effects ---
        assert_that(result.status, is_(equal_to(RecurringImport.Status.SUCCESS)))
        assert_that(result.imported, is_(equal_to(12)))
        metrics.assert_has_calls(
            [
                call.increment("twitter.items", 12),
                call.increment("twitter.result.success"),
            ]
        )

        with sql_db.begin() as conn:
            updated_record = recurring_imports.get_by_id(conn, id=record.id)
            assert updated_record is not None
            # Confirm access has been extended and stale credentials updated
            updated_context = updated_record.typed_context(TwitterImportContext)
            assert updated_context is not None
            assert_that(
                updated_context.oauth2_credentials.access_token,
                is_(equal_to("a-new-bearer-token")),
            )
            assert_that(
                updated_context.oauth2_credentials.refresh_token,
                is_(equal_to("a-new-refresh-token")),
            )
            assert_that(
                updated_context.oauth2_credentials.expires_at,
                is_(greater_than(awhile_back)),
            )

        # Spot check a few records
        _check_url_records(
            sql_db,
            record,
            url="https://twitter.com/marktenenholtz/status/1746928022995775597",
        )
        _check_url_records(
            sql_db,
            record,
            url="https://twitter.com/markbrooks/status/1741108160427901306",
        )

    finally:
        server.stop()


@pytest.mark.integration
@pytest.mark.skip(
    reason="Flaky WSGI server; client sometimes hangs waiting for server "
    "response. Must be run manually to avoid spurious CI failures."
)
def test_twitter_importer_credential_refresh_failure(
    sql_db: sqlalchemy.Engine,
) -> None:
    # --- Set up fixtures ---
    def twitter_mock(
        environ: dict[str, Any],
        start_response: StartResponseFunc,
    ) -> Sequence[Any]:
        path: str = environ["PATH_INFO"]
        if path == "/oauth2/token":
            status = "400 Bad Request"
            response_text = open(_OAUTH2_TOKEN_INVALID_AUTH).read()
        else:
            status = "404 Not Found"
            response_text = "{}"

        headers = [("Content-type", "application/json")]
        start_response(status, headers)
        return [response_text.encode("utf-8")]

    server = WSGIServer(application=twitter_mock)
    twitter_api.BASE_URL = server.url
    server.start()

    try:
        awhile_back = datetime.now(timezone.utc) - timedelta(minutes=5)
        record = _create_recurring_twitter_import(
            sql_db,
            twitter_user_id="irrelevant",
            credentials_expiration=awhile_back,
        )

        # constraints on card table require an existing user
        with Session(sql_db) as session:
            crud.user_account.create(
                session,
                obj_in=record_helpers.user_account(user_id=record.user_id),
            )

        metrics = Mock(DogStatsd)
        recurring_imports = RecurringImportRecords()

        # --- Run test ---
        result = TwitterImporter(
            sql_db,
            metrics,
            records=recurring_imports,
            client_id="irrelevant",
        ).import_content(record)

        # --- Confirm side-effects ---
        assert_that(
            result.status,
            is_(equal_to(RecurringImport.Status.PERMANENT_FAILURE)),
        )
        assert result.detail is not None
        assert_that(result.detail, contains_string("Oh noes!"))
    finally:
        server.stop()


def _check_url_records(
    sql_db: sqlalchemy.Engine,
    recurring_import: RecurringImport,
    url: str,
) -> None:
    with Session(sql_db) as sess:
        urlstate = crud.urlstate.get_by_user_and_url(
            sess,
            user_id=str(recurring_import.user_id),
            url=url,
        )
        assert urlstate
        assert_that(
            urlstate.source,
            is_(equal_to(RecurringImport.Source.TWITTER.value)),
        )
        assert_that(
            str(urlstate.detail),
            contains_string(RecurringImport.Source.TWITTER.value),
        )
        assert_that(
            str(urlstate.recurring_import_id),
            is_(equal_to(str(recurring_import.id))),
        )

        assert_that(urlstate.state, is_(equal_to(PROCESSING_REQUIRED)))
        assert_that(
            urlstate.retrieval_detail,
            is_(equal_to(urlstate.detail)),
        )
        assert_that(
            urlstate.retrieval_timestamp,
            is_(equal_to(urlstate.initial_timestamp)),
        )

        urlcontent = crud.urlcontent.get_by_user_by_url(
            sess,
            user_id=str(recurring_import.user_id),
            url=url,
        )
        assert urlcontent
        assert_that(urlcontent.retriever, is_(equal_to(urlstate.detail)))

        # Basic checks: content can be deserialized as JSON and is a list with
        # 1 TweetScraped model (the content processor model).
        tweet_dicts = json.loads(str(urlcontent.content))
        assert_that(tweet_dicts, has_length(1))
        TweetScraped.model_validate(tweet_dicts[0])


_FIXTURES_PATH = "tests/fixture_data/twitter"
_OAUTH2_TOKEN_OK = f"{_FIXTURES_PATH}/oauth2_token_ok.json"
_OAUTH2_TOKEN_INVALID_AUTH = f"{_FIXTURES_PATH}/oauth2_token_invalid_auth.json"
_GET_BOOKMARKS_OK = f"{_FIXTURES_PATH}/get_bookmarks_ok.json"


def _deterministic_import_id(
    twitter_user_id: str,
    user_id: UUID = Record.deterministic_id("user_1"),
) -> UUID:
    return Record.deterministic_id(f"twitter:{user_id}:{twitter_user_id}")


def _create_recurring_twitter_import(
    sql_db: sqlalchemy.Engine,
    twitter_user_id: str,
    credentials_expiration: datetime = datetime.now(timezone.utc),
    user_id: UUID = Record.deterministic_id("user_1"),
) -> RecurringImport:
    with sql_db.begin() as conn:
        record = RecurringImportRecords().create(
            conn,
            id=_deterministic_import_id(twitter_user_id, user_id),
            user_id=user_id,
            source=RecurringImport.Source.TWITTER,
            settings=TwitterImportSettings(
                user_id="42",
                username="foobar",
            ),
            context=TwitterImportContext(
                oauth2_credentials=twitter_api.OAuth2Credentials(
                    token_type="refresh_token",
                    access_token="old-access-token",
                    refresh_token="old-refresh-token",
                    scope="irrelevant",
                    expires_at=credentials_expiration,
                ),
            ),
            interval=timedelta(minutes=1),
            enabled=True,
        )

    return record
