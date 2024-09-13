# -*- coding: utf-8 -*-
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional

import requests
from loguru import logger
from pydantic import BaseModel

# Twitter v2 API client.
#
# References:
# - OAuth 2: https://developer.twitter.com/en/docs/authentication/oauth-2-0
# - Expansions: https://developer.twitter.com/en/docs/twitter-api/expansions
# - Fields: https://developer.twitter.com/en/docs/twitter-api/fields
# - API: https://developer.twitter.com/en/docs/api-reference-index#twitter-api-v2

BASE_URL = "https://api.twitter.com/2"
_DEFAULT_EXPANSIONS = ",".join(
    [
        "author_id",
        # Include media keys for all media attached to tweets.
        "attachments.media_keys",
        # Include quoted/retweeted tweets and their author
        "referenced_tweets.id",
        # Include referenced tweets' authors.
        "referenced_tweets.id.author_id",
    ]
)
_DEFAULT_TWEET_FIELDS = ",".join(
    [
        "created_at",
        "referenced_tweets",
        "attachments",
        "conversation_id",
        # Include full text content for "notes" (i.e. tweets over 280 chars).
        "note_tweet",
    ]
)

_DEFAULT_USER_FIELDS = ",".join(
    [
        "id",
        "name",
        "username",
        "profile_image_url",
    ]
)

_DEFAULT_MEDIA_FIELDS = ",".join(
    [
        "media_key",
        "type",
        # for Photos
        "url",
        # for Videos and GIFs
        "preview_image_url",
        "variants",
    ],
)
_DEFAULT_TWEET_EXPANSIONS_AND_FIELDS = {
    "expansions": _DEFAULT_EXPANSIONS,
    "tweet.fields": _DEFAULT_TWEET_FIELDS,
    "user.fields": _DEFAULT_USER_FIELDS,
    "media.fields": _DEFAULT_MEDIA_FIELDS,
}

_REQUEST_TIMEOUT_SECS = 10


class OAuth2Credentials(BaseModel):
    # NB: When changing this model, consider that it is used by
    # TwitterInternalSettings, which is de/serialized from/to JSON as the
    # setting of a twitter recurring import. This means that all changes must
    # be backwards compatible in terms of pydantic validation (e.g. adding new
    # mandatory fields means existing data will begin throwing errors).
    # Assumption: this model (i.e. twitter's API) won't change so it's safe to
    # use its format to store internal implementation details.
    token_type: str
    access_token: str
    scope: str
    refresh_token: str
    expires_at: datetime

    def is_expired(self, at_instant: datetime) -> bool:
        return at_instant >= self.expires_at


class Profile(BaseModel):
    id: str
    name: str
    username: str
    profile_image_url: str


class Photo(BaseModel):
    # Lazy way to get constant 'type' field when JSON-serializing via pydantic.
    type: Literal["photo"] = "photo"
    url: str


class Variant(BaseModel):
    url: str
    content_type: str
    bit_rate: Optional[int] = None


class GIF(BaseModel):
    type: Literal["gif"] = "gif"
    thumbnail_url: str
    variants: list[Variant] = []


class Video(BaseModel):
    type: Literal["video"] = "video"
    thumbnail_url: str
    variants: list[Variant] = []


class Tweet(BaseModel):
    id: str
    text: str
    created_at: datetime
    author: Profile
    quoted_tweet: Optional["Tweet"] = None
    media: list[Photo | Video | GIF] = []

    @property
    def url(self) -> str:
        """URL of tweet in canonical form."""
        return f"https://twitter.com/{self.author.username}/status/{self.id}"


class ClientError(Exception):
    def __init__(
        self,
        # Twitter API responds with 400 during auth flow errors (e.g. invalid
        # code, expired token, etc.) but 401 for non-auth API calls with
        # invalid credentials. Flagging certain client errors explicitly as
        # auth errors helps streamline logic to handle credential refresh and
        # keeps this 400/401 detail contained to this module.
        is_credential_error: bool,
        status_code: int,
        detail: str,
        errors: list[str],
    ):
        self.is_credential_error = is_credential_error
        self.status_code = status_code
        self.detail = detail
        self.errors = errors
        super().__init__(f"{status_code}, {detail}")

    def detailed_description(self) -> str:
        errors = ", ".join(self.errors) if self.errors else None
        errors = f" ({errors})" if errors else ""
        return f"{self.status_code}, {self.detail}{errors}"

    @staticmethod
    def invalid_credentials(code: int, detail: str, errors: list[str]) -> "ClientError":
        return ClientError(True, code, detail, errors)

    @staticmethod
    def invalid_request(code: int, detail: str, errors: list[str]) -> "ClientError":
        return ClientError(False, code, detail, errors)


def get_credentials(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> OAuth2Credentials:
    response = requests.post(
        timeout=_REQUEST_TIMEOUT_SECS,
        url=f"{BASE_URL}/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
    )
    _raise_for_authorization_status(response)

    return _oauth2_credentials_from(response)


def extend_access(
    current_refresh_token: str,
    client_id: str,
) -> OAuth2Credentials:
    """
    Generate an OAuth 2 PKCE access token from a refresh token.

    Refresh token must be a valid refresh token previously generated by the
    Twitter OAuth 2 PKCE flow for the same client ID.

    Reference: https://developer.twitter.com/en/docs/authentication/oauth-2-0/obtaining-user-access-tokens
    """
    response = requests.post(
        url=f"{BASE_URL}/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": current_refresh_token,
        },
        timeout=_REQUEST_TIMEOUT_SECS,
    )

    _raise_for_authorization_status(response)
    return _oauth2_credentials_from(response)


def get_self_profile(
    access_token: str,
) -> Profile:
    """
    Get the profile of the authenticated user.

    Requires an OAuth 2 Bearer access token (i.e. request made on behalf of
    user).

    Reference: https://developer.twitter.com/en/docs/authentication/oauth-2-0/user-access-token
    """
    response = requests.get(
        timeout=_REQUEST_TIMEOUT_SECS,
        url=f"{BASE_URL}/users/me",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        params={
            "user.fields": _DEFAULT_USER_FIELDS,
        },
    )
    _raise_for_api_call_status(response)
    return Profile.model_validate(response.json()["data"])


def get_bookmarks_page(
    oauth2_pkce_access_token: str,
    user_id: str,
    max_results: int,
    pagination_token: Optional[str],
) -> tuple[list[Tweet], Optional[str]]:
    """
    Get the bookmarks for the authenticated user.

    Requires an OAuth2 Bearer access token (i.e. request made on behalf of
    user) since a user's bookmarks are private.

    Reference: https://developer.twitter.com/en/docs/twitter-api/tweets/bookmarks/api-reference/get-users-id-bookmarks
    """

    params = {
        **_DEFAULT_TWEET_EXPANSIONS_AND_FIELDS,
        "max_results": max_results if max_results else 100,
    }
    if pagination_token:
        params["pagination_token"] = pagination_token

    response = None

    # NB: Fails if user_id does not match access token.
    response = requests.get(
        timeout=_REQUEST_TIMEOUT_SECS,
        url=f"{BASE_URL}/users/{user_id}/bookmarks",
        headers={
            "Authorization": f"Bearer {oauth2_pkce_access_token}",
        },
        params=params,
    )
    _raise_for_api_call_status(response)

    response_json: dict[str, Any] = response.json()

    meta = response_json["meta"]
    next_token = meta.get("next_token", None)

    tweets = _parse_tweets_response(response_json)

    return tweets, next_token


def get_bookmarks_paginated_until_condition(
    oauth2_pkce_access_token: str,
    user_id: str,
    condition: Callable[[list[Tweet]], bool],
    count: int,
) -> list[Tweet]:
    bookmarks: list[Tweet] = []
    pagination_token = None

    while True:
        if pagination_token:
            new_bookmarks, next_token = get_bookmarks_page(
                oauth2_pkce_access_token=oauth2_pkce_access_token,
                user_id=user_id,
                max_results=count,
                pagination_token=pagination_token,
            )
        else:
            new_bookmarks, next_token = get_bookmarks_page(
                oauth2_pkce_access_token=oauth2_pkce_access_token,
                user_id=user_id,
                max_results=count,
                pagination_token=None,
            )

        if not new_bookmarks:
            break

        bookmarks.extend(new_bookmarks)

        if not next_token or condition(new_bookmarks):
            break

        pagination_token = next_token

    return bookmarks


def get_tweets(
    access_token: str,
    tweet_ids: list[str],
) -> list[Tweet]:
    """
    Retrieve a list of tweets by IDs.

    Works with both an OAuth2 Bearer access token (i.e. request made on behalf
    of user) and an OAuth2 Client access token (i.e. request made on behalf of
    application). When using the latter, the response will only include tweets
    that are publicly available.

    Private tweets will be omitted from the response if the request is made
    using an application access token or with a user token for an account that
    does not have permission to view the tweet.

    Reference:
    https://developer.twitter.com/en/docs/twitter-api/tweets/lookup/api-reference/get-tweets
    """
    response = requests.get(
        timeout=_REQUEST_TIMEOUT_SECS,
        url=f"{BASE_URL}/tweets/",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        params={
            "ids": ",".join(tweet_ids),
            **_DEFAULT_TWEET_EXPANSIONS_AND_FIELDS,
        },
    )
    _raise_for_authorization_status(response)

    response_json = response.json()
    return _parse_tweets_response(response_json)


def _raise_for_authorization_status(response: requests.Response) -> None:
    """
    Raise error for non-2xx status code API responses to authorization flow
    calls. Extracts further information from the error if the response is a 4xx
    error status code (expected to have a JSON body with error details.)

    Should only be used for auth-related API calls, which return 400 for auth
    flow errors (e.g. invalid code, expired refresh token, etc.) but are
    rethrown as 401-code ClientError ()
    """
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        if e.response is None:
            raise e  # Request error, e.g. timeout, regression; rethrow as is.

        # naively raise ever 400 as auth error
        if 400 <= e.response.status_code < 500:
            detail, errors = _error_details(e.response)
            if e.response.status_code == 400:
                # Naively assume all 400's mean bad values supplied during auth
                # flow (e.g. invalid code, expired refresh token, etc.)
                # A more robust solution would inspect the error description
                # and pattern-match against specific, known Twitter API error
                # descriptions for auth flow errors — but since such list does
                # not exist in Twitter dev docs, this'll have to do.
                raise ClientError.invalid_credentials(400, detail, errors)
            raise ClientError.invalid_request(e.response.status_code, detail, errors)


def _raise_for_api_call_status(response: requests.Response) -> None:
    """
    Raise error for non-2xx status code API responses. Extracts further
    information from the error if the response is a 4xx error status code
    (expected to have a JSON body with error details.)
    """
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        if e.response is None:
            raise e  # Request error, e.g. timeout, regression; rethrow as is.

        # 400 responses typically include a JSON body with error description.
        if 400 <= e.response.status_code < 500:
            # Most likely causes willis expired access token. A scope permission
            # error would mean we're trying to access functionality for which
            # we did not request scope permissions when the user went through
            # the OAuth2 PKCE flow.
            detail, errors = _error_details(e.response)
            if e.response.status_code == 401:
                raise ClientError.invalid_credentials(401, detail, errors)
            raise ClientError.invalid_request(e.response.status_code, detail, errors)

        raise e


def _error_details(response: requests.Response) -> tuple[str, list[str]]:
    try:
        response_json: dict[str, Any] = response.json()
        # Twitter error response bodies have an inconsistent format:
        # for auth flow, body is {"error": <key>, "error_description": <text>}
        # for API calls, body is {"detail": <text>, "errors": [{"message": <text>, ...}]}
        if "error_description" in response_json:
            detail = response_json["error_description"]
            errors = []
        else:
            detail = response_json.get("detail", response.reason)
            errors = [error.get("message") for error in response_json.get("errors", [])]
            errors = [error for error in errors if error is not None]
    except requests.exceptions.JSONDecodeError:
        detail = response.reason
        errors = []

    return detail, errors


def _oauth2_credentials_from(response: requests.Response) -> OAuth2Credentials:
    response_json: dict[str, Any] = response.json()
    now = datetime.now(timezone.utc)

    return OAuth2Credentials(
        token_type=response_json["token_type"],
        access_token=response_json["access_token"],
        scope=response_json["scope"],
        refresh_token=response_json["refresh_token"],
        # Twitter returns the number of seconds until the token expires; convert
        # to the absolute time (in UTC) at which it expires, as it's more useful
        # to determine if a refresh is required before issuing API requests.
        expires_at=now + timedelta(seconds=response_json["expires_in"]),
    )


def _parse_tweets_response(response_json: dict[str, Any]) -> list[Tweet]:
    """
    Parse a Twitter API response containing tweet data into a list of Tweets
    that includes all its quoted tweets and authors.
    """
    if "data" not in response_json:
        return []

    tweets = [_Tweet.model_validate(tweet) for tweet in response_json["data"]]
    included_tweets = {
        tweet["id"]: _Tweet.model_validate(tweet)
        for tweet in response_json.get("includes", {}).get("tweets", {})
    }
    included_users = {
        user["id"]: Profile.model_validate(user)
        for user in response_json.get("includes", {}).get("users", {})
    }
    included_media = {
        media["media_key"]: _Media.model_validate(media)
        for media in response_json.get("includes", {}).get("media", {})
    }

    return [
        Tweet(
            id=tweet.id,
            created_at=tweet.created_at,
            author=_link_author(tweet, included_users),
            # If a note is available (only present for tweets with content over
            # 280 chars) use its full text content (since 'text' is clipped).
            text=tweet.note_tweet.text if tweet.note_tweet is not None else tweet.text,
            quoted_tweet=_link_quoted_tweet(tweet, included_users, included_tweets),
            media=_link_media(tweet, included_media),
        )
        for tweet in tweets
    ]


class _Tweet(BaseModel):
    """
    Internal model for parsing/validating tweet data from Twitter API response.
    """

    class _Note(BaseModel):
        text: str

    class _Reference(BaseModel):
        id: str
        type: str  # "retweeted", "quoted", "replied_to"

    class _Attachments(BaseModel):
        media_keys: Optional[list[str]] = None
        # poll_ids: Optional[list[str]] = None

    id: str
    created_at: datetime
    author_id: str
    # Tweets longer than 280 chars have their top-level text field truncated.
    text: str
    # Tweets longer than 280 chars ("notes") have their full content available
    # under a different field, which must be specified in the API request.
    # (see _DEFAULT_TWEET_FIELDS)
    note_tweet: Optional[_Note] = None
    referenced_tweets: list[_Reference] = []
    attachments: Optional[_Attachments] = None


class _Media(BaseModel):
    media_key: str
    type: str
    # Only present for "photo" type
    url: Optional[str] = None
    # Only present for "video" and "animated_gif" types
    preview_image_url: Optional[str] = None
    variants: list[Variant] = []


def _link_author(
    tweet: _Tweet,
    included_users: dict[str, Profile],
) -> Profile:
    return included_users[tweet.author_id]


def _link_quoted_tweet(
    tweet: _Tweet,
    included_users: dict[str, Profile],
    included_tweets: dict[str, _Tweet],
) -> Optional[Tweet]:
    for ref in tweet.referenced_tweets:
        if ref.type != "quoted":
            continue
        quoted_tweet = included_tweets[ref.id]
        return Tweet(
            id=quoted_tweet.id,
            created_at=quoted_tweet.created_at,
            author=_link_author(quoted_tweet, included_users),
            text=quoted_tweet.text,
            # No further expansion on quoted tweets.
        )

    return None


def _link_media(
    tweet: _Tweet,
    included_media: dict[str, _Media],
) -> list[Photo | Video | GIF]:
    """
    Parse media data from Twitter API response into a list of Photo, Video and
    GIF models.
    """

    if tweet.attachments is None or tweet.attachments.media_keys is None:
        return []

    media: list[Photo | Video | GIF] = []
    for media_key in tweet.attachments.media_keys:
        media_data = included_media[media_key]
        media_item: Optional[Photo | Video | GIF] = None
        if media_data.type == "photo" and media_data.url is not None:
            media_item = Photo(url=media_data.url)
        elif media_data.type == "video" and media_data.preview_image_url is not None:
            media_item = Video(
                thumbnail_url=media_data.preview_image_url,
                variants=media_data.variants,
            )
        elif (
            media_data.type == "animated_gif"
            and media_data.preview_image_url is not None
        ):
            media_item = GIF(
                thumbnail_url=media_data.preview_image_url,
                variants=media_data.variants,
            )

        if media_item is not None:
            media.append(media_item)

    return media
