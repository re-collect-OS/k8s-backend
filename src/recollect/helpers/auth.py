# -*- coding: utf-8 -*-
import os

from fastapi_cognito import CognitoAuth, CognitoSettings
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    check_expiration: bool = True
    jwt_header_prefix: str = "Bearer"
    jwt_header_name: str = "Authorization"
    userpools: dict[str, dict[str, str]] = {
        "us": {
            "region": os.environ["COGNITO_REGION"],
            "userpool_id": os.environ["COGNITO_USERPOOL_ID"],
            "app_client_id": os.environ["COGNITO_APP_CLIENT_ID"],
        },
    }


cognito = CognitoAuth(
    settings=CognitoSettings.from_global_settings(Settings()),
    userpool_name="us",
)
