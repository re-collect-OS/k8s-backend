# -*- coding: utf-8 -*-
import json
from typing import Optional
from uuid import UUID

import sqlalchemy
from loguru import logger
from pydantic import BaseModel

from common.integrations import twitter_api
from common.integrations.twitter_api import OAuth2Credentials
from common.records.recurring_imports import RecurringImport, RecurringImportRecords
from common.records.recurring_imports_twitter import (
    TwitterImportAuthContext,
    TwitterImportContext,
)
from recollect.helpers.text import text_to_sentences
from recollect.schemas.compositions import TweetScraped
from recollect.schemas.sentence import SentenceTweet

from ..importers.twitter_importer import (
    get_or_extend_credentials,
    to_urlcontent_content,
)
from .common import RetrieveResult

_RETRIEVER_VERSION = "tweet_retriever_1.0"
_PROCESSOR_VERSION = "tweet_processor_1.0"


def tweet_id_from_url(url: str) -> str:
    # Copied from old tweet retriever; could use some validations.
    return url.split("/")[5]


def retrieve_tweet(
    user_id: str,
    tweet_url: str,
    conn: sqlalchemy.Connection,
    twitter_api_app_auth_bearer_token: str,
    twitter_api_user_auth_client_id: str,
) -> RetrieveResult:
    """
    Retrieve a tweet and possibly its surrounding conversation.

    Falls back to client auth if the tweet is private and the user has a
    configured Twitter recurring import.
    """
    tweet_id = tweet_id_from_url(tweet_url)
    tweet_thread = _get_conversation_for_tweet(
        user_id=user_id,
        tweet_id=tweet_id,
        conn=conn,
        app_auth_bearer_token=twitter_api_app_auth_bearer_token,
        user_auth_client_id=twitter_api_user_auth_client_id,
    )

    return RetrieveResult(
        content=to_urlcontent_content(tweet_thread),
        detail=_RETRIEVER_VERSION,
    )


def _get_conversation_for_tweet(
    user_id: str,
    tweet_id: str,
    conn: sqlalchemy.Connection,
    app_auth_bearer_token: str,
    user_auth_client_id: str,
) -> list[twitter_api.Tweet]:
    """
    Return relevant tweets in conversation for a given tweet.

    NB: Retrieving tweets by conversation ID with the basic plan ($100/mo) only
    works for the last 7 days. For full archive access, we'd have to be on the
    Pro plan or above (min $5,000/mo). For the time being, load just the tweet.

    TL;DR: this function will return a list with just the target tweet.
    """

    tweets = twitter_api.get_tweets(
        tweet_ids=[tweet_id],
        access_token=app_auth_bearer_token,
    )
    # An empty response means the tweet is private (other reasons such as bad
    # tweet ID would raise exception).
    #
    # Intuition: if a user's re:collect account has this tweet in their
    # processing pipeline, it's likely the user could view the tweet (i.e.
    # follow the author). In that case, check if the user also has configured
    # the Twitter recurring imports integration, and use its credentials to
    # retrieve the tweet using client auth (OAuth2 PKCE).
    #
    # Alternatively, we could always start with client auth and fall back to
    # app auth when user doesn't have twitter recurring imports configured,
    # though that would mean we'd always incur extra DB lookup and a potential
    # credential refresh flow (if the user does have integration configured but
    # credentials are stale). Assuming a) most tweets are public, b) not all
    # users will have twitter integration configured, and c) processing URLs is
    # in the product's (really) hot path, appauth->userauth seems more sensible.
    if len(tweets) == 0:
        logger.debug("Tweet {id} is private; trying client auth...", id=tweet_id)
        return _get_conversation_for_tweet_with_client_auth(
            user_id,
            tweet_id,
            conn,
            user_auth_client_id,
        )

    return tweets


def _get_conversation_for_tweet_with_client_auth(
    user_id: str,
    tweet_id: str,
    conn: sqlalchemy.Connection,
    user_auth_client_id: str,
) -> list[twitter_api.Tweet]:
    """
    Return relevant tweets in conversation for a given tweet using client auth.

    Returns an empty list if no Twitter recurring import is configured for the
    user, or if the import is disabled.
    """
    records = RecurringImportRecords()
    imports = records.get_all_by_source_by_user_id(
        conn=conn,
        source=RecurringImport.Source.TWITTER,
        user_id=UUID(user_id),
    )

    # Typically there'll only be one import configured (at the time of writing,
    # webapp only allows configuring 1 Twitter integration per re:collect user
    # account). Filter out disabled imports (e.g. auto-off after permanent
    # failure); if there are no enabled imports, return early. If there are
    # multiple enabled imports, naively use the first one.
    enabled = [i for i in imports if i.enabled]
    if len(enabled) == 0:
        logger.debug(
            "No enabled Twitter recurring imports configured for user {id}; "
            "unable to fetch tweet {tweet_id} using client auth.",
            id=user_id,
            tweet_id=tweet_id,
        )
        # No recurring import configured for user.
        return []

    import_record = enabled[0]
    credentials = _conditionally_extend_twitter_access(
        records,
        conn,
        import_record,
        user_auth_client_id,
    )

    try:
        logger.debug(
            "Fetching tweet {tweet} for user {user} "
            "using client auth from {record}.",
            tweet=tweet_id,
            user=user_id,
            record=import_record,
        )
        return twitter_api.get_tweets(
            tweet_ids=[tweet_id],
            access_token=credentials.access_token,
        )
    except twitter_api.ClientError as e:
        logger.warning(
            "Unable to fetch tweet {tweet} for {user} "
            "using client auth from {record}: {error}",
            tweet=tweet_id,
            record=import_record,
            error=e.detailed_description(),
        )
        return []


# NB: Similar to code in twitter_importer; primary differences are:
# 1. no disabling the import here if credential extension fails — recurring
#   import record management should take place exclusively within recurring
#   import mechanisms.
# 2. all this code (including Twitter API call) runs within a DB tx, which is a
#   terrible idea but would required a lot of refactoring to content processor
#   to fix.
#
# There's potential for a race condition where both the recurring importer and
# the tweet retriever are trying to extend the same credentials at the same
# time, but that's unlikely to happen in practice — and if it does, whichever
# process finishes first will update the credentials in the DB.
def _conditionally_extend_twitter_access(
    records: RecurringImportRecords,
    conn: sqlalchemy.Connection,
    import_record: RecurringImport,
    user_auth_client_id: str,
) -> OAuth2Credentials:
    try:
        (credentials, extended) = get_or_extend_credentials(
            import_record,
            client_id=user_auth_client_id,
        )
    except Exception as e:
        logger.warning(
            "Unable to extend Twitter OAuth2 access for {record}: {error}",
            record=import_record,
            error=str(e),
        )
        context = import_record.typed_context(TwitterImportContext)
        assert context is not None
        return context.oauth2_credentials

    if not extended:
        return credentials

    records.merge_context(
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
    # An early commit here is really not ideal, but since a) the whole retriever
    # code runs within a DB transaction and b) storing the new credentials is
    # imperative to not lose Twitter access (as the old refresh token stored in
    # the DB just became invalid), it's the safest option.
    conn.commit()

    return credentials


class ProcessTweetResult(BaseModel):
    sentences: list[SentenceTweet]
    title: str
    byline: str
    doc_type: str
    doc_subtype: Optional[str]
    processor_version: str


def process_tweet(content: str) -> ProcessTweetResult:
    # NB(bruno): Logic and idiosyncrasies copied from old tweet processor, with
    # some minor adjustments for legibility and type-safety (where possible).
    # Unless otherwise stated, all comments below are from the original code.
    paragraph_number, tweet_number = 0, 0
    sentences: list[SentenceTweet] = []

    tweet_thread = [TweetScraped.model_validate(tweet) for tweet in json.loads(content)]
    first_tweet = tweet_thread[0]

    for tweet in tweet_thread:
        # process a quoted tweet first, if it exists
        # note: tweets are in chronological order, we only need to keep
        # track of whether current tweet has a tweet quoted to adjust
        # both paragraph and tweet number in parent tweet moved one position
        if isinstance(tweet.tweet_quotes, TweetScraped):
            quoted_tweet_sentences = text_to_sentences(tweet.tweet_quotes.text)
            if len(quoted_tweet_sentences) > 0:
                paragraph_length = sum(len(s) for s in quoted_tweet_sentences)
                for sentence in quoted_tweet_sentences:
                    tweet_sentence = _to_tweet_sentence(
                        source=tweet,
                        sentence=sentence,
                        tweet_number=tweet_number,
                        paragraph_number=paragraph_number,
                        paragraph_length=paragraph_length,
                        quoted_tweet_index=None,
                    )
                    sentences.append(tweet_sentence)
                paragraph_number += 1
                tweet_number += 1

        tweet_sentences = text_to_sentences(tweet.text)

        if len(tweet_sentences) > 0:
            paragraph_length = sum(len(s) for s in tweet_sentences)
            for sentence in tweet_sentences:
                tweet_sentence = _to_tweet_sentence(
                    source=tweet,
                    sentence=sentence,
                    tweet_number=tweet_number,
                    paragraph_number=paragraph_number,
                    paragraph_length=paragraph_length,
                    quoted_tweet_index=tweet_number - 1 if tweet.tweet_quotes else None,
                )
                sentences.append(tweet_sentence)
            paragraph_number += 1
            tweet_number += 1

    return ProcessTweetResult(
        sentences=sentences,
        title=f"{first_tweet.tweet_display_name} @{first_tweet.tweet_user_name}",
        byline=first_tweet.tweet_user_name,
        doc_type="twitter",
        doc_subtype="tweet_thread",
        processor_version=_PROCESSOR_VERSION,
    )


def _to_tweet_sentence(
    source: TweetScraped,
    sentence: str,
    tweet_number: int,
    paragraph_number: int,
    paragraph_length: int,
    quoted_tweet_index: Optional[int],
) -> SentenceTweet:
    tweet_media_json: Optional[str] = None
    if isinstance(source.tweet_media, str):
        tweet_media_json = source.tweet_media
    else:
        tweet_media_json = json.dumps(
            [m.model_dump(mode="json") for m in source.tweet_media]
        )

    return SentenceTweet(
        text=sentence,
        paragraph_number=paragraph_number,
        tweet_number=tweet_number,
        tweet_url=source.tweet_url,
        tweet_user_name=source.tweet_user_name,
        tweet_display_name=source.tweet_display_name,
        tweet_avatar_url_original=source.tweet_avatar_url_original.get("original"),
        sentence_length=len(sentence),
        paragraph_length=paragraph_length,
        tweet_media=tweet_media_json,
        tweet_quotes=quoted_tweet_index,
    )
