# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from pydantic import BaseModel, HttpUrl

from .oauth2 import create_code_verifier, create_S256_code_challenge, generate_token

# Google API client.
#
# References:
# - https://developers.google.com/identity/protocols/oauth2#expiration
# - https://blog.timekit.io/google-oauth-invalid-grant-nightmare-and-how-to-fix-it-9f4efaf1da35
# - Using OAuth 2.0 to Access Google APIs: https://developers.google.com/identity/protocols/oauth2
# - API settings: https://developers.google.com/workspace/
#
# - Proof Key for Code Exchange by OAuth Public Clients: https://datatracker.ietf.org/doc/html/rfc7636
# - OAuth 2.0 Authorization Flow: https://tools.ietf.org/html/rfc6749#section-1.2
# - Authorization Code grant: https://tools.ietf.org/html/rfc6749#section-1.3.1
# - Refresh token: https://tools.ietf.org/html/rfc6749#section-6


BASE_URL = "https://www.googleapis.com"
_GOOGLE_AUTHORIZATION_CODE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_OAUTH2_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

_REQUEST_TIMEOUT_SECS = 10


class Profile(BaseModel):
    id: str
    email: str
    verified_email: bool
    name: str
    given_name: str | None = None
    family_name: str | None = None
    picture: HttpUrl | None = None
    locale: str | None = None
    hd: str | None = None  # e.g., re-collect.ai


class GoogleDriveAuthRedirect(BaseModel):
    redirect_uri: str
    code_verifier: str
    state: str  # for convenience, contained in redirect_uri


class OAuth2Credentials(BaseModel):
    access_token: str
    refresh_token: str
    scope: str
    expires_at: datetime

    def is_expired(self, at_instant: datetime) -> bool:
        return at_instant >= self.expires_at


def get_new_or_updated_metadata(
    access_token: str, since: datetime, mime_types: list[str]
) -> list[dict[str, Any]]:  # TODO make this response typed by explicit check
    """Get all metadata for files with given MIME types which were modified since desired date."""
    url = "https://www.googleapis.com/drive/v3/files"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"pageSize": 100, "fields": "nextPageToken, files(*)"}
    since_str = since.isoformat()

    new_or_updated_files: list[dict[str, Any]] = []
    for mime_type in mime_types:
        params["q"] = (
            f"(modifiedTime > '{since_str}' or createdTime > '{since_str}') and mimeType = '{mime_type}'"
        )
        # loop through paginated results
        while True:
            response = requests.get(url, headers=headers, params=params).json()
            new_or_updated_files.extend(response.get("files", []))  # type:ignore

            page_token = response.get("nextPageToken")
            if not page_token:
                break
            else:
                params["pageToken"] = page_token

    # filter files that were trashed
    # explicitlyTrashed: Indicates whether the file was explicitly trashed,
    # as opposed to being trashed via a parent folder.
    new_or_updated_files = [
        f for f in new_or_updated_files if not (f["trashed"] and f["explicitlyTrashed"])
    ]

    return new_or_updated_files


def get_file_content_export(access_token: str, export_link: HttpUrl) -> str:
    """Export file via API call."""

    response = requests.get(
        str(export_link), headers={"Authorization": f"Bearer {access_token}"}
    )

    return response.text


def get_file(access_token: str, file_id: str) -> bytes:
    """
    Download an image file from Google Drive given its file ID and an access token.

    :param file_id: The ID of the file to download.
    :param access_token: The access token for authentication with the Google Drive API.
    :return: The content of the file as bytes, or None if the download fails.
    """
    base_url = "https://www.googleapis.com/drive/v3/files/"
    url = f"{base_url}{file_id}?alt=media"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(url, headers=headers)

    return response.content


def get_credentials(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> OAuth2Credentials:
    response = requests.post(
        timeout=_REQUEST_TIMEOUT_SECS,
        url=_GOOGLE_OAUTH2_TOKEN_ENDPOINT,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
    )

    _raise_for_authorization_status(response)

    return _oauth2_credentials_from(response)


def get_authorization_redirect(
    client_id: str, scope: str, redirect_uri: str
) -> GoogleDriveAuthRedirect:
    state = generate_token()
    # secret known by only us to be verified when getting token
    # after successful authorization
    code_verifier = create_code_verifier(50)
    code_challenge = create_S256_code_challenge(code_verifier)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",  # assume we don't have the refresh token always
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    params = urlencode(list(params.items()))

    return GoogleDriveAuthRedirect(
        code_verifier=code_verifier,
        state=state,
        redirect_uri=f"{_GOOGLE_AUTHORIZATION_CODE_ENDPOINT}?{params}",
    )


# TODO Google Oauth DOES NOT refresh the refresh token every time the refresh
# token is used to update the access_token. It is assumed to be long-lived.
# We have to handle the case the refresh token expires, unlike for the Twitter implementation!
# https://developers.google.com/identity/protocols/oauth2#expiration
def extend_access(
    current_refresh_token: str, client_id: str, client_secret: str
) -> OAuth2Credentials:
    """
    Generate an OAuth 2 access token from a refresh token. (Referesh token is long lived and does not expire.)
    """
    response = requests.post(
        url=_GOOGLE_OAUTH2_TOKEN_ENDPOINT,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": current_refresh_token,
        },
        timeout=_REQUEST_TIMEOUT_SECS,
    )

    _raise_for_authorization_status(response)

    return _oauth2_credentials_from(
        response, is_refresh=True, refresh_token=current_refresh_token
    )


def _oauth2_credentials_from(
    response: requests.Response,
    is_refresh: bool | None = False,
    refresh_token: str | None = None,
) -> OAuth2Credentials:
    """Example response from https://oauth2.googleapis.com/token on first non-refresh request:
    {'access_token': 'foo',
     'expires_in': 3599,
     'refresh_token': 'bar',
     'scope': 'https://www.googleapis.com/auth/userinfo.profile openid',
     'token_type': 'Bearer',
     'id_token': 'baz'}
    """
    if is_refresh and refresh_token is None:
        raise AssertionError("is_refresh is True, but no refresh_token provided.")

    response_json: dict[str, Any] = response.json()
    now = datetime.now(timezone.utc)
    # expires_in ~= 3599 s = 1h, give one minute extra buffer
    seconds_to_expiry = int(response_json["expires_in"]) - 60
    expires_at = now + timedelta(seconds=seconds_to_expiry)

    if is_refresh and refresh_token is not None:
        refresh_token = refresh_token
    else:
        refresh_token = response_json["refresh_token"]

    return OAuth2Credentials(
        access_token=response_json["access_token"],
        refresh_token=refresh_token,  # type: ignore
        scope=response_json["scope"],
        expires_at=expires_at,
    )


def get_self_profile(
    access_token: str,
) -> Profile:
    """
    Get the profile of the authenticated user.

    Requires an OAuth 2 Bearer access token (i.e. request made on behalf of
    user).

    """
    response = requests.get(
        timeout=_REQUEST_TIMEOUT_SECS,
        url=f"{BASE_URL}/oauth2/v2/userinfo",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )
    _raise_for_api_call_status(response)

    return Profile.model_validate(response.json())


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


def _error_details(response: requests.Response) -> tuple[str, list[str]]:
    try:
        response_json: dict[str, Any] = response.json()
        # Google error response bodies have an inconsistent format:
        # for auth flow, body is {"error": <key>, "error_description": <text>}
        # for API calls, body is :
        # {
        #   "error": {
        #     "errors": [
        #       {
        #         "domain": "global",
        #         "reason": "authError",
        #         "message": "Invalid Credentials",
        #         "locationType": "header",
        #         "location": "Authorization",
        #       }
        #     ],
        #     "code": 401,
        #     "message": "Invalid Credentials"
        #   }
        # }
        if "error_description" in response_json:
            detail = response_json["error_description"]
            errors = []
        else:
            detail = response_json.get("error", {}).get("message", "API call error")
            errors = [
                error.get("message")
                for error in response_json.get("error", {}).get("errors", {})
            ]
            errors = [error for error in errors if error is not None]
    except requests.exceptions.JSONDecodeError:
        detail = response.reason
        errors = []

    return detail, errors


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
                # and pattern-match against specific, known Google API error
                # descriptions for auth flow errors
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
