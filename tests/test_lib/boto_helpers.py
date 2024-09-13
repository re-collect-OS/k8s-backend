# -*- coding: utf-8 -*-
import uuid
from typing import Optional

from mypy_boto3_cognito_idp import CognitoIdentityProviderClient
from mypy_boto3_s3 import S3Client


class CognitoTestHelper:
    def __init__(self, client: CognitoIdentityProviderClient) -> None:
        self.client = client

    @property
    def endpoint_url(self) -> str:
        return self.client.meta.endpoint_url

    def create_pool(self, pool_name: Optional[str] = None) -> tuple[str, str]:
        pool_name = pool_name or str(uuid.uuid4())
        response = self.client.create_user_pool(
            PoolName=pool_name,
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )
        return response["UserPool"]["Id"], pool_name

    def create_client_app(
        self,
        pool_id: str,
        client_name: Optional[str] = None,
    ) -> str:
        client_name = client_name or str(uuid.uuid4())
        response = self.client.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=client_name,
        )
        return response["UserPoolClient"]["ClientId"]

    def create_test_user(
        self,
        userpool_id: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
    ) -> tuple[str, str]:
        # Generate a random email if email is None
        email = email or f"{uuid.uuid4()}@example.com"
        password = password or uuid.uuid4().hex
        self.client.admin_create_user(
            UserPoolId=userpool_id,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}],
            TemporaryPassword=password,
            MessageAction="SUPPRESS",
        )
        return email, password

    def get_bearer_token(
        self,
        userpool_id: str,
        app_client_id: str,
        email: str,
        password: str,
    ) -> str:
        response = self.client.admin_initiate_auth(
            UserPoolId=userpool_id,
            ClientId=app_client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": email,
                "PASSWORD": password,
            },
        )
        return response["AuthenticationResult"]["AccessToken"]

    def all_usernames(self, userpool_id: str) -> list[str]:
        response = self.client.list_users(UserPoolId=userpool_id)
        return [u["Username"] for u in response["Users"]]


class S3TestHelper:
    def __init__(self, client: S3Client) -> None:
        self.client = client

    def create_bucket(self, bucket_name: Optional[str] = None) -> str:
        bucket_name = bucket_name or str(uuid.uuid4())
        self.client.create_bucket(Bucket=bucket_name)
        return bucket_name

    def all_file_keys(self, bucket_name: str) -> list[str]:
        response = self.client.list_objects_v2(Bucket=bucket_name)
        return [o["Key"] for o in response["Contents"]]

    def create_files(
        self,
        bucket_name: str,
        user_id: uuid.UUID,
        count: int = 1,
    ) -> None:
        for i in range(count):
            self.client.put_object(
                Bucket=bucket_name,
                Key=f"{user_id}/file-{i}.txt",
                Body=f"test-data-{i}".encode("utf-8"),
                ContentType="text/plain",
            )
