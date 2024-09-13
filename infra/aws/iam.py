# -*- coding: utf-8 -*-
import json
from typing import Any, Optional

import pulumi
import pulumi_aws
import pulumi_eks
import pulumi_kubernetes as k8s

from ..common import to_resource_name


def declare_service_account(
    name: str,
    namespace: k8s.core.v1.Namespace,
    cluster: pulumi_eks.Cluster,
    k8s_provider: k8s.Provider,
    policy_json: pulumi.Output[str],
) -> k8s.core.v1.ServiceAccount:
    """
    Declare a Service Account and an IAM role for it to assume with the given
    policy attached to it. The supplied policy includes a definition of AWS
    resources the service account can access.
    """
    res_name = to_resource_name(name)
    serviceaccount_name = f"{res_name}-serviceaccount"

    # Create an IAM Role that can be assumed by the service account
    role = pulumi_aws.iam.Role(
        f"{res_name}-role",
        assume_role_policy=pulumi.Output.all(
            cluster.core.oidc_provider.arn,
            cluster.core.oidc_provider.url,
            namespace.metadata.name,
        ).apply(
            lambda args: json.dumps(
                assume_role_policy(
                    cluster_oidc_arn=args[0],
                    cluster_oidc_url=args[1],
                    service_account_name=f"system:serviceaccount:{args[2]}:{serviceaccount_name}",
                )
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=cluster.core.aws_provider,
            parent=namespace,  # top-level under namespace
            depends_on=[namespace],
        ),
    )

    # Create IAM Policy attached to role
    rolepolicy = pulumi_aws.iam.RolePolicy(
        f"{res_name}-policy",
        role=role.name,
        policy=policy_json,
        opts=pulumi.ResourceOptions(
            provider=cluster.core.aws_provider,
            parent=role,  # rolepolicy makes no sense without role
            depends_on=[role],
        ),
    )

    # Create service account to attach IAM Role to EKS pods.
    # Only deployments/pods in the same namespace can use this service account.
    return k8s.core.v1.ServiceAccount(
        serviceaccount_name,
        metadata={
            "name": serviceaccount_name,
            "namespace": namespace.metadata.name,
            "annotations": {
                "eks.amazonaws.com/role-arn": role.arn,
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            parent=namespace,  # top-level under namespace
            depends_on=[namespace, rolepolicy],
        ),
    )


def assume_role_policy(
    cluster_oidc_arn: str,
    cluster_oidc_url: str,
    service_account_name: str,
) -> dict[str, Any]:
    """
    Returns a dictionary representing an AWS IAM policy that allows a Kubernetes
    service account to assume an AWS IAM role.
    """

    # Reference AWS web identity keys:
    # https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_iam-condition-keys.html#condition-keys-wif
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": cluster_oidc_arn,
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{cluster_oidc_url}:sub": service_account_name,
                    }
                },
            },
        ],
    }


# Type alias for a dictionary representing an AWS IAM Policy Statement.
PolicyStatement = dict[str, Any]

# A no-op empty placeholder for a policy document. Only exists to satisfy the
# requirement that an IAM Role must have a policy document attached to it.
EmptyPolicyDocument = {
    "Version": "2012-10-17",
    "Statement": {
        # IAM requires that a policy document have at least one statement.
        # This statement effectively does nothing.
        "Effect": "Allow",
        "Action": "none:*",
        "Resource": "*",
    },
}


def policy_json_from_statements(
    *statements: Optional[pulumi.Output[PolicyStatement]],
) -> pulumi.Output[str]:
    """
    Create a `pulumi.Output` that'll materialize into a JSON serialized IAM
    Policy document that includes all of the supplied statements.

    If no `PolicyStatement` are supplied, output will materialize into the JSON
    serialization of `EmptyPolicyDocument`.
    """
    filtered_sts = [s for s in statements if s is not None]
    if not filtered_sts:
        return pulumi.Output.from_input(json.dumps(EmptyPolicyDocument))

    return pulumi.Output.all(*filtered_sts).apply(
        lambda sts: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": sts,
            }
        )
    )
