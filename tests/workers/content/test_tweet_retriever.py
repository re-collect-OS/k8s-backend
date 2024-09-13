# -*- coding: utf-8 -*-
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import call, patch

import pytest
from hamcrest import assert_that, equal_to, has_length, is_

from common.integrations import twitter_api
from common.records.records import Record
from common.records.recurring_imports import RecurringImport, RecurringImportRecords
from common.records.recurring_imports_twitter import (
    TwitterImportContext,
    TwitterImportSettings,
)
from workers.content.tweet import retrieve_tweet

from ...test_lib.services import TestServices

# fully-qualified names of functions to patch
_GET_TWEETS_FN_FQN = "common.integrations.twitter_api.get_tweets"
_EXTEND_ACCESS_FN_FQN = "common.integrations.twitter_api.extend_access"


@pytest.mark.integration
def test_retrieve_tweet_app_auth(
    external_deps: TestServices,
) -> None:
    tweet = twitter_api.Tweet(
        id="123",
        text="tweet text",
        author=twitter_api.Profile(
            id="123",
            username="username",
            name="name",
            profile_image_url="https://example.com/profile_image.png",
        ),
        created_at=datetime.now(timezone.utc),
    )

    with (
        external_deps.sql_db_client().begin() as conn,
        patch(_GET_TWEETS_FN_FQN) as mock_get_tweets,
    ):
        mock_get_tweets.return_value = [tweet]
        result = retrieve_tweet(
            user_id="123",
            tweet_url="https://twitter.com/username/status/123",
            conn=conn,
            twitter_api_app_auth_bearer_token="app-token",
            twitter_api_user_auth_client_id="client-id",
        )
        mock_get_tweets.assert_called_once_with(
            tweet_ids=["123"],
            access_token="app-token",
        )

    _assert_content_matches(result.content, tweet)


@pytest.mark.integration
def test_retrieve_tweet_private_tweet_fallback_to_user_auth_with_credential_extension(
    external_deps: TestServices,
) -> None:
    private_tweet = twitter_api.Tweet(
        id="123",
        text="private tweet text",
        author=twitter_api.Profile(
            id="123",
            username="username",
            name="name",
            profile_image_url="https://example.com/profile_image.png",
        ),
        created_at=datetime.now(timezone.utc),
    )

    # Create a twitter recurring import with expired credentials
    with external_deps.sql_db_client().begin() as conn:
        import_record = RecurringImportRecords().create(
            conn,
            id=Record.deterministic_id("tweet_processor_1"),
            user_id=Record.deterministic_id("user_1"),
            source=RecurringImport.Source.TWITTER,
            settings=TwitterImportSettings(
                user_id="123",
                username="username",
            ),
            context=TwitterImportContext(
                oauth2_credentials=twitter_api.OAuth2Credentials(
                    token_type="refresh_token",
                    scope="scope",
                    access_token="old-access-token",
                    refresh_token="old-refresh-token",
                    expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                ),
                latest_tweet_ids_syncd=[],
            ),
            interval=timedelta(minutes=1),
        )

    with (
        external_deps.sql_db_client().begin() as conn,
        patch(_GET_TWEETS_FN_FQN) as mock_get_tweets,
        patch(_EXTEND_ACCESS_FN_FQN) as mock_extend_access,
    ):
        mock_extend_access.return_value = twitter_api.OAuth2Credentials(
            token_type="refresh_token",
            scope="scope",
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        # No result on 1st call (app auth), only 2nd call (user auth).
        # This test intentionally peers into impl details.
        mock_get_tweets.side_effect = [[], [private_tweet]]
        result = retrieve_tweet(
            user_id=str(import_record.user_id),
            tweet_url="https://twitter.com/username/status/123",
            conn=conn,
            twitter_api_app_auth_bearer_token="app-token",
            twitter_api_user_auth_client_id="client-id",
        )

        mock_extend_access.assert_called_once_with(
            current_refresh_token="old-refresh-token",
            client_id="client-id",
        )
        expected_get_tweet_calls = [
            # First call, using app auth
            call(tweet_ids=["123"], access_token="app-token"),
            # Second call, using user auth
            call(tweet_ids=["123"], access_token="new-access-token"),
        ]
        assert_that(
            mock_get_tweets.call_args_list,
            is_(equal_to(expected_get_tweet_calls)),
        )

    _assert_content_matches(result.content, private_tweet)

    # Verify that the credentials were updated.
    with external_deps.sql_db_client().begin() as conn:
        import_record = RecurringImportRecords().get_by_id(conn, id=import_record.id)
        assert import_record is not None
        context = import_record.typed_context(TwitterImportContext)
        assert context is not None
        assert_that(
            context.oauth2_credentials,
            is_(equal_to(mock_extend_access.return_value)),
        )


def _assert_content_matches(json_content: str, tweet: twitter_api.Tweet) -> None:
    elements = json.loads(json_content)
    assert_that(elements, has_length(1))
    assert_that(elements[0]["text"], is_(equal_to(tweet.text)))
    # Could definitely use a couple more robust checks.
