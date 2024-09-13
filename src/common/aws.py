# -*- coding: utf-8 -*-
import os

import boto3
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient
from mypy_boto3_s3 import S3Client  # type: ignore
from mypy_boto3_sqs import SQSClient


def sqs_client_from_env() -> SQSClient:
    return boto3.client(
        "sqs",
        endpoint_url=os.getenv("SQS_ENDPOINT_URL"),
    )


def s3_client_from_env() -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL"),
    )


def cognito_client_from_env() -> CognitoIdentityProviderClient:
    return boto3.client(
        "cognito-idp",
        endpoint_url=os.getenv("COGNITO_ENDPOINT_URL"),
    )
