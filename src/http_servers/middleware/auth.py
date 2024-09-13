# -*- coding: utf-8 -*-
import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from cognitojwt import CognitoJWTException
from cognitojwt import decode_async as cognito_jwt_decode
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp

from common import env

_DEFAULT_USERPOOL = "us"


@dataclass
class CognitoUser(BaseUser):
    id: UUID
    email: str

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.email

    @property
    def identity(self) -> str:
        return str(self.id)


@dataclass
class CognitoSettings:
    userpool_name: str
    userpool_id: str
    app_client_id: str
    aws_region: str
    detailed_errors: bool = False

    @staticmethod
    def from_env() -> "CognitoSettings":
        return CognitoSettings(
            userpool_name=_DEFAULT_USERPOOL,
            userpool_id=env.require_str("COGNITO_USERPOOL_ID"),
            app_client_id=env.require_str("COGNITO_APP_CLIENT_ID"),
            aws_region=env.require_str("COGNITO_REGION"),
            # Only show detailed auth errors in non-production environments.
            detailed_errors=os.getenv("ENV") != "prod",
        )


class CognitoAuthBackend(AuthenticationBackend):
    """
    Starlette AuthenticationBackend implementation for Cognito IDP
    authentication JSON WebToken.

    To be used with AuthenticationMiddleware, adds a CognitoUser to the request
    state if the Authorization header is present and valid.

    Throws AuthenticationError (results in 401 response) if the Authorization
    header is missing or invalid.

    Example:
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware, backend=CognitoAuthBackend())

        @app.get("/protected")
        def protected(request: Request):
            user = request.state.user
            return f"Hello {user.email}"
    """

    def __init__(self, settings: CognitoSettings):
        self._settings = settings

    async def authenticate(
        self,
        conn: HTTPConnection,
    ) -> Optional[tuple[AuthCredentials, CognitoUser]]:
        auth_header = conn.headers.get("Authorization")
        if not auth_header:
            raise self._auth_failed("missing 'Authorization' header in request")

        if not auth_header.startswith("Bearer "):
            raise self._auth_failed("format must be 'Bearer <token>'")

        token = auth_header.split(" ")[1]
        try:
            data: dict[str, str] = await cognito_jwt_decode(
                token=token,
                region=self._settings.aws_region,
                userpool_id=self._settings.userpool_id,
                app_client_id=self._settings.app_client_id,
                testmode=False,
            )
        except ValueError as error:
            raise self._auth_failed(f"malformed header, {str(error)}")
        except CognitoJWTException as error:
            raise self._auth_failed(str(error))

        cognito_id = data.get("sub")
        if not cognito_id or len(cognito_id) == 0:
            raise self._auth_failed("missing 'sub' claim in response")

        # NB: On account creation, we set the user's email both as cognito
        # 'username' and 'email' attributes, but the frontend only has access
        # to the 'username' claim.
        email = data.get("username")
        if not email or len(email) == 0:
            raise self._auth_failed("missing 'username' claim in response")

        try:
            cognito_id = UUID(cognito_id)
        except ValueError as e:
            raise self._auth_failed(f"invalid 'sub' claim in response: {e}")

        return AuthCredentials(), CognitoUser(cognito_id, email)

    def _auth_failed(self, details: str) -> AuthenticationError:
        message = "Authentication failed"
        if self._settings.detailed_errors:
            message += f": {details}"
        return AuthenticationError(message)


class CognitoAuthMiddleware(AuthenticationMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        settings: Optional[CognitoSettings] = None,
    ):
        settings = settings or CognitoSettings.from_env()
        super().__init__(
            app,
            backend=CognitoAuthBackend(settings),
            # Return 401 instead of 400 for auth errors.
            on_error=lambda _, exc: PlainTextResponse(
                content=str(exc),
                status_code=401,
            ),
        )
