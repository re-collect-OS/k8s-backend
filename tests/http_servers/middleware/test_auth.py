# -*- coding: utf-8 -*-
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from hamcrest import assert_that, is_

from http_servers.middleware.auth import CognitoAuthMiddleware, CognitoSettings

from ...test_lib.boto_helpers import CognitoTestHelper
from ...test_lib.services import TestServices


@pytest.fixture(scope="module")
def cognito(external_deps: TestServices) -> CognitoTestHelper:
    return CognitoTestHelper(external_deps.cognito_client())


@pytest.fixture(scope="module")
def settings(cognito: CognitoTestHelper) -> CognitoSettings:
    pool_id, pool_name = cognito.create_pool()
    app_client_id = cognito.create_client_app(pool_id)

    # cognitojwt does not expose a programmatic way to change the endpoint
    # URL; it has to be done via env var AWS_COGNITO_JWKS_PATH
    jwks_path = f"{cognito.endpoint_url}/{pool_id}/.well-known/jwks.json"
    os.environ.setdefault("AWS_COGNITO_JWKS_PATH", jwks_path)

    return CognitoSettings(
        userpool_name=pool_name,
        userpool_id=pool_id,
        app_client_id=app_client_id,
        aws_region="irrelevant",
        detailed_errors=False,
    )


@pytest.fixture(scope="module")
def test_app(settings: CognitoSettings) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CognitoAuthMiddleware,
        settings=settings,
    )

    @app.get("/protected", response_class=PlainTextResponse)
    async def _(request: Request) -> str:
        return f"hello, {request.user.email}"

    return app


@pytest.mark.integration
def test_cognito_auth_middleware(
    cognito: CognitoTestHelper,
    settings: CognitoSettings,
    test_app: FastAPI,
):
    user_email, user_pw = cognito.create_test_user(settings.userpool_id)
    valid_bearer_token = cognito.get_bearer_token(
        userpool_id=settings.userpool_id,
        app_client_id=settings.app_client_id,
        email=user_email,
        password=user_pw,
    )

    client = TestClient(test_app)

    # Invalid authentications
    response = client.get("/protected")
    assert_that(response.status_code, is_(401))
    assert_that(response.text, is_("Authentication failed"))

    response = client.get(
        "/protected",
        headers={"Authorization": "Basic 1234"},
    )
    assert_that(response.status_code, is_(401))
    assert_that(response.text, is_("Authentication failed"))

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert_that(response.status_code, is_(401))
    assert_that(response.text, is_("Authentication failed"))

    response = client.get(
        "/protected",
        headers={"Authorization": f"Bearer {_INVALID_BEARER_TOKEN}"},
    )
    assert_that(response.status_code, is_(401))
    assert_that(response.text, is_("Authentication failed"))

    # Valid authentication
    response = client.get(
        "/protected",
        headers={"Authorization": f"Bearer {valid_bearer_token}"},
    )
    assert_that(response.text, is_(f"hello, {user_email}"))


# Copied from a previous test run; extremely unlikely to accidentaly be valid.
_INVALID_BEARER_TOKEN = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IkNvZ25pdG9Mb2NhbCJ9.eyJjb2dua"
    "XRvOnVzZXJuYW1lIjoiYXV0aC10ZXN0LXVzZXJAZW1haWwuY29tIiwiYXV0aF90aW1lIjoxNzA"
    "wNDcwODcxLCJlbWFpbCI6ImF1dGgtdGVzdC11c2VyQGVtYWlsLmNvbSIsImVtYWlsX3Zlcmlma"
    "WVkIjpmYWxzZSwiZXZlbnRfaWQiOiI5NmZkYmNiMi1kMDc5LTQ3MWYtYTZkNS1kNTE2MGQzZmY"
    "yZGIiLCJpYXQiOjE3MDA0NzA4NzEsImp0aSI6IjE3OGI1NTdjLWM3MDMtNDY3Zi1iOGQ4LWU0M"
    "Tk3ZjcxNzFiZCIsInN1YiI6IjBhOTkwYjhkLWMwNmUtNDM5MC1hNDU2LWIzODM1MTQ3YjU0ZSI"
    "sInRva2VuX3VzZSI6ImlkIiwiZXhwIjoxNzAwNTU3MjcxLCJhdWQiOiJkMWVjdmJraW51MHZ3b"
    "m9sa3VkcTVvZDEzIiwiaXNzIjoiaHR0cDovL2xvY2FsaG9zdDo5MjI5L2xvY2FsXzdRYXI5dXF"
    "FIn0.W-i0BzR11iqp8xcReQqkXkx-YOY3U9I-oW-VmgE6--5TdtWiZy_ccTABXnZJ6j5LetGuu"
    "j_sOaCBpg1Mx_Kb5oDVWSzmFUg_KPP31J0pc6zBYQngp4JSiH0WkLDtRSsiDg9CzLob0SWbsPP"
    "qx1URW_aw4XAOLBrkeJo1csd1hQWtIUrI_txpKleU8WgUhu76HHjUzgVJJShKyo4ng5SX0-qLk"
    "P9kBmbI0NZ7f2AvIbRVrGhgbW0mFyJTjJ9kaW46bFe9O-ZzvZIf0WbK-ANE36qJxrKPXmHcHqQ"
    "RljxsBHeVSejgWZztw020SQ2IfBND4pLVoOzG0JALidx_zzq_bQ"
)
