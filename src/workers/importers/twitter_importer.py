# -*- coding: utf-8 -*-
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy
from datadog.dogstatsd.base import DogStatsd
from loguru import logger

from common import killswitches
from common.integrations import twitter_api
from common.records.recurring_imports import RecurringImport, RecurringImportRecords
from common.records.recurring_imports_twitter import (
    TwitterImportAuthContext,
    TwitterImportContext,
    TwitterImportSettings,
)
from recollect.helpers.compose import tweet_avatar_urls
from recollect.helpers.time import datetime_to_iso_8601_str
from recollect.schemas.compositions import (
    TweetMediaGIF,
    TweetMediaPhoto,
    TweetMediaVariant,
    TweetMediaVideo,
    TweetScraped,
)
from recollect.schemas.urlcontent import UrlcontentCreate
from recollect.schemas.urlstate import PROCESSING_REQUIRED, UrlstateCreate

from .base_artifact_importer import Artifact, BaseArtifactImporter, CreateArtifacts
from .base_importer import Importer

_EXPIRES_AT_LENIENCY_SECS = 5


class TwitterImporter(
    BaseArtifactImporter[TwitterImportSettings, TwitterImportContext],
):
    def __init__(
        self,
        sql_db: sqlalchemy.Engine,
        metrics: DogStatsd,
        records: RecurringImportRecords,
        client_id: str,
    ) -> None:
        super().__init__(
            sql_db,
            metrics,
            source=RecurringImport.Source.TWITTER,
            settings_cls=TwitterImportSettings,
            context_cls=TwitterImportContext,
            readonly_killswitch=killswitches.twitter_readonly,
        )
        self._client_id = client_id
        self._records = records

    def fetch_and_convert(
        self,
        import_record: RecurringImport,
        settings: TwitterImportSettings,
        context: TwitterImportContext,
    ) -> CreateArtifacts[TwitterImportContext] | Importer.Result:
        # First things first, conditionally refresh Twitter access credentials
        # as they expire every 2 hours.
        refresh_result = self._conditionally_extend_access(import_record)

        # If the refresh failed, return the failure result.
        if isinstance(refresh_result, Importer.Result):
            return refresh_result

        # Otherwise proceed to pull bookmarks with valid oauth2_token.
        valid_oauth2_token = refresh_result

        # Only sync the past 10 tweets on recurring syncs
        count = 100 if import_record.last_run_finished_at is None else 10

        try:
            bookmarks, _ = twitter_api.get_bookmarks_page(
                oauth2_pkce_access_token=valid_oauth2_token.access_token,
                user_id=settings.user_id,
                max_results=count,
                pagination_token=None,
            )
        except twitter_api.ClientError as e:
            logger.warning(
                "Unable to fetch Twitter bookmarks for {record}: {error}",
                record=import_record,
                error=e.detailed_description(),
            )
            if e.is_credential_error:
                # Auth failure at this point is unlikely; can only happen if:
                #  a) access token expires or app access is revoked by user
                #     between _conditionally_refresh_token and get_bookmarks, or
                #  b) authorization lacks scope to fetch bookmarks
                # Either way, it's a permanent failure.
                return Importer.Result.permanent_failure(f"auth failed ({e})")
            # Non-ClientError will bubble up and be handled as transient.
            return _twitter_api_call_error(e.status_code)

        if len(bookmarks) == 0:
            return Importer.Result.no_new_content()

        now = datetime.now(timezone.utc)
        artifacts = [
            _artifact_for_tweet(
                tweet,
                source_import=import_record,
                importer_detail=self.detail,
                timestamp=now,
            )
            for tweet in bookmarks
        ]

        # Keep sync metadata as context to have a fighting chance in avoiding
        # a full sync next run. Because we can't get any metadata about when a tweet
        # was bookmarked, we have to keep track of the newest few tweet IDs we've syncd.
        latest_tweet_ids_syncd = [tweet.id for tweet in bookmarks[:10]]

        return CreateArtifacts(
            artifacts=artifacts,
            # This importer doesn't generate notes/highlights.
            annotations=[],
            updated_context=TwitterImportContext(
                oauth2_credentials=valid_oauth2_token,
                latest_tweet_ids_syncd=latest_tweet_ids_syncd,
            ),
        )

    # NB: This is very similar to code in content retriever. There, however,
    # all retrieval logic (including API calls) unfortunately runs within a
    # long-running database transaction — here we can avoid that.
    def _conditionally_extend_access(
        self,
        import_record: RecurringImport,
    ) -> twitter_api.OAuth2Credentials | Importer.Result:
        try:
            (credentials, extended) = get_or_extend_credentials(
                import_record, self._client_id
            )
        except twitter_api.ClientError as e:
            # If we're not able to extend access, that means that either
            # the app authorization has been revoked or the refresh token
            # we have is no longer valid. In any case, we're no longer able
            # to obtain access tokens for further requests.
            logger.warning(
                "Unable to extend Twitter OAuth2 access for {record}: {error}",
                record=import_record,
                error=e.detailed_description(),
            )
            if e.is_credential_error:
                return Importer.Result.permanent_failure(f"auth failed ({e})")

            # Non-ClientError will bubble up and be handled as transient.
            return _twitter_api_call_error(e.status_code)

        if not extended:
            return credentials

        # Unlike other importers that only update the context at the end of a
        # successful import, this one should immediately do it since the
        # previously stored refresh token becomes invalid after an extension.
        # Any errors in the importer flow before updating the context after a
        # successful access extension would lead to a permanent loss of the
        # ability to regenerate access tokens, which is highly undesirable as
        # it would require user intervention to re-authenticate via Twitter.
        with self._sql_db.begin() as conn:
            self._records.merge_context(
                conn,
                id=import_record.id,
                context=TwitterImportAuthContext(
                    oauth2_credentials=credentials,
                ),
            )
            logger.debug(
                "Updated context for {record} with extended Twitter OAuth2.",
                record=import_record,
            )

        return credentials


def _twitter_api_call_error(status_code: int) -> Importer.Result:
    """
    Return a transient failure result for a Twitter API call error with the
    given status code. If the status code is 429, the result will include a
    delay to wait before retrying.
    """
    delay: Optional[timedelta] = None
    if status_code == 429:
        # https://developer.twitter.com/en/docs/twitter-api/rate-limits#v2-limits-basic
        # Twitter API rate limits requests in 15m windows; wait 20m.
        delay = timedelta(minutes=20)

    return Importer.Result.transient_failure(
        detail="request error",
        delay=delay,
    )


def _artifact_for_tweet(
    tweet: twitter_api.Tweet,
    source_import: RecurringImport,
    importer_detail: str,
    timestamp: datetime,
) -> Artifact:
    ts_str = datetime_to_iso_8601_str(timestamp)
    urlstate = UrlstateCreate(
        user_id=str(source_import.user_id),
        # No need to normalize tweet URLs, they're already in canonical form.
        url=tweet.url,
        timestamp=ts_str,
        initial_timestamp=ts_str,
        detail=importer_detail,
        source=source_import.source.value,
        state=PROCESSING_REQUIRED,
        retrieval_timestamp=ts_str,
        retrieval_detail=importer_detail,
        recurring_import_id=str(source_import.id),
    )

    urlcontent = UrlcontentCreate(
        user_id=str(source_import.user_id),
        url=tweet.url,
        content=to_urlcontent_content([tweet]),
        retriever=importer_detail,
        timestamp=ts_str,
        doc_id=str(uuid.uuid4()),
        metadata=None,
    )

    return Artifact(
        state=urlstate,
        content=urlcontent,
    )


# Shared logic with tweet retriever


def get_or_extend_credentials(
    import_record: RecurringImport,
    client_id: str,
) -> tuple[twitter_api.OAuth2Credentials, bool]:
    """
    Check the current credentials from the import record's context. If they're
    still valid, return them. Otherwise, extend them and return the result.

    Result should be immediately persisted since extending credentials
    invalidates the current refresh token — losing the result of an extension
    means losing the ability generate new access tokens for the user.
    """

    context = import_record.typed_context(TwitterImportContext)
    # Overzealous sanity check; twitter recurring imports are always created
    # with a context that contains the credentials (validated on creation).
    assert context is not None

    credentials = context.oauth2_credentials

    # When refreshing, add a few seconds to account for clock drift and/or
    # delay in firing request.
    a_few_seconds_from_now = datetime.now(timezone.utc) + timedelta(
        seconds=_EXPIRES_AT_LENIENCY_SECS
    )

    # Easy case, credentials still valid until a_few_seconds_from_now.
    if not credentials.is_expired(at_instant=a_few_seconds_from_now):
        logger.debug(
            "Twitter OAuth2 access still valid for {record}.",
            record=import_record,
        )
        return credentials, False

    logger.debug(
        "Twitter OAuth2 access for {record} expired; extending...",
        record=import_record,
    )
    return (
        twitter_api.extend_access(
            current_refresh_token=credentials.refresh_token,
            client_id=client_id,
        ),
        True,
    )


def to_urlcontent_content(tweets: list[twitter_api.Tweet]) -> str:
    """
    Convert a list of tweets (possibly related, e.g. a thread) to a JSON string
    to be stored in the content field of a Urlcontent record.
    """
    tweets.sort(key=lambda t: t.created_at)
    content_pieces = [
        _to_content_processor_model(tweet, expand_quotes=True) for tweet in tweets
    ]
    content_pieces = [p.model_dump(mode="json") for p in content_pieces]

    return json.dumps(content_pieces)


def _to_content_processor_model(
    tweet: twitter_api.Tweet, expand_quotes: bool
) -> TweetScraped:
    """
    Convert a tweet to a TweetScraped model for use by the content processor.
    """
    return TweetScraped(
        url=tweet.url,
        user_name=tweet.author.username,
        display_name=tweet.author.name,
        text=tweet.text,
        avatar_urls=tweet_avatar_urls(tweet.author.profile_image_url),
        media=[_convert_media(m) for m in tweet.media],
        quotes_tweet=(
            _to_content_processor_model(tweet.quoted_tweet, expand_quotes=False)
            if tweet.quoted_tweet is not None and expand_quotes is True
            else None
        ),
        sentences=[],  # Populated by content processor.
    )


def _convert_media(
    media: twitter_api.Photo | twitter_api.Video | twitter_api.GIF,
) -> TweetMediaPhoto | TweetMediaVideo | TweetMediaGIF:
    if type(media) is twitter_api.Photo:
        return TweetMediaPhoto(
            type="photo",
            previewUrl=media.url,
            fullUrl=media.url,
        )

    if type(media) is twitter_api.Video:
        return TweetMediaVideo(
            type="video",
            thumbnailUrl=media.thumbnail_url,
            duration=0.0,  # Not present in API response.
            variants=[_convert_variant(v) for v in media.variants],
        )

    if type(media) is twitter_api.GIF:
        return TweetMediaGIF(
            type="gif",
            thumbnailUrl=media.thumbnail_url,
            variants=[_convert_variant(v) for v in media.variants],
        )
    raise ValueError(f"unexpected media type: {type(media)}")


def _convert_variant(variant: twitter_api.Variant) -> TweetMediaVariant:
    return TweetMediaVariant(
        contentType=variant.content_type,
        url=variant.url,
        bitrate=variant.bit_rate,
    )
