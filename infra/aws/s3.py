# -*- coding: utf-8 -*-
import pulumi

from . import iam

# NB: S3 ARN's do not include account or region; see:
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-arn-format.html


def list_objects_in_bucket(
    bucket_name: str,
) -> pulumi.Output[iam.PolicyStatement]:
    return pulumi.Output.from_input(
        {
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{bucket_name}"],
        }
    )


def delete_objects_in_bucket(
    bucket_name: str,
) -> pulumi.Output[iam.PolicyStatement]:
    return pulumi.Output.from_input(
        {
            "Effect": "Allow",
            "Action": ["s3:DeleteObject"],
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
        }
    )


def get_objects_in_bucket(
    bucket_name: str,
) -> pulumi.Output[iam.PolicyStatement]:
    return pulumi.Output.from_input(
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:HeadObject",
            ],
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
        }
    )


def put_objects_in_bucket(
    bucket_name: str,
) -> pulumi.Output[iam.PolicyStatement]:
    return pulumi.Output.from_input(
        {
            "Effect": "Allow",
            "Action": ["s3:PutObject"],
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
        }
    )
