# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from hamcrest import assert_that, contains_string, equal_to, is_

from common.integrations import twitter_api


@pytest.mark.integration
def test_get_credentials_ok(httpserver: Any) -> None:
    twitter_api.BASE_URL = httpserver.url
    httpserver.serve_content(
        code=200,
        content=open(_OAUTH2_TOKEN_OK).read(),
        headers={"Content-Type": "application/json"},
    )

    credentials = twitter_api.get_credentials(
        code="foo",
        code_verifier="bar",
        redirect_uri="http://127.0.0.1:8000/redirect",
        client_id="baz",
    )

    assert_that(credentials.access_token, equal_to("a-new-bearer-token"))
    assert_that(credentials.refresh_token, equal_to("a-new-refresh-token"))
    now = datetime.now(timezone.utc)
    assert_that(credentials.is_expired(at_instant=now), is_(False))
    fifty_seconds_from_now = now + timedelta(seconds=50)
    assert_that(credentials.is_expired(at_instant=fifty_seconds_from_now), is_(True))


@pytest.mark.integration
def test_get_credentials_throws_credential_error(httpserver: Any) -> None:
    # Confirm that get_credentials throws the right exception when receiving a
    # 400 response from Twitter due to invalid auth parameters.
    # Correctly interpreting these errors allows signaling to calling code that
    # this is a permanent failure that should _not_ be retried (rather than a
    # transient/spurious one, where a retry might be helpful.)
    twitter_api.BASE_URL = httpserver.url
    httpserver.serve_content(
        code=400,
        content=open(_OAUTH2_TOKEN_INVALID_AUTH).read(),
        headers={"Content-Type": "application/json"},
    )

    try:
        twitter_api.get_credentials(
            code="foo",
            code_verifier="bar",
            redirect_uri="http://127.0.0.1:8000/redirect",
            client_id="baz",
        )
    except twitter_api.ClientError as e:
        assert_that(e.is_credential_error, is_(True))
    except Exception as e:
        pytest.fail(f"unexpected exception: {e}")


@pytest.mark.integration
def test_get_profile_throws_credential_error(httpserver: Any) -> None:
    # Confirm that get_self_profile throws the right exception when receiving a
    # 401 response from Twitter due to invalid auth parameters.
    # See note in test_get_credentials_throws_invalid_auth for reasoning.
    twitter_api.BASE_URL = httpserver.url
    httpserver.serve_content(
        code=401,
        content="{}",
        headers={"Content-Type": "application/json"},
    )

    try:
        twitter_api.get_self_profile(access_token="invalid")
    except twitter_api.ClientError as e:
        assert_that(e.is_credential_error, is_(True))
    except Exception as e:
        pytest.fail(f"unexpected exception: {e}")


# Further coverage that'd be nice but not critical:
# - bookmarks with fixture

_FIXTURES_PATH = "tests/fixture_data/twitter"
_OAUTH2_TOKEN_OK = f"{_FIXTURES_PATH}/oauth2_token_ok.json"
_OAUTH2_TOKEN_INVALID_AUTH = f"{_FIXTURES_PATH}/oauth2_token_invalid_auth.json"
