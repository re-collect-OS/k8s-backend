# -*- coding: utf-8 -*-
import pulumi
import pulumi_aws

from . import iam


def delete_users_in_pool(
    user_pool: pulumi_aws.cognito.UserPool,
) -> pulumi.Output[iam.PolicyStatement]:
    user_pool_arn: pulumi.Output[str] = user_pool.arn
    return user_pool_arn.apply(
        lambda arn: {
            "Effect": "Allow",
            "Action": "cognito-idp:AdminDeleteUser",
            "Resource": arn,
        },
    )
