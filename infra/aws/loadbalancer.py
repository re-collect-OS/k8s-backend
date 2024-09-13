# -*- coding: utf-8 -*-

import pulumi
import pulumi_eks
import pulumi_kubernetes as k8s

from . import iam


def declare_alb_controller(
    cluster: pulumi_eks.Cluster,
    k8s_provider: k8s.Provider,
) -> None:
    """
    Deploys the AWS Application Load Balancer (ALB) Controller to the cluster
    under the "alb-controller" namespace.

    This controller manages the creation and assignment of Elastic Load
    Balancers and Target Groups to Kubernetes Ingress resources.

    For more information, refer to the [official guide](https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.6/deploy/installation/)
    """

    name = "alb-controller"
    namespace = k8s.core.v1.Namespace(
        name,
        metadata={
            "name": name,
            "labels": {
                "app.kubernetes.io/name": name,
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[cluster],
        ),
    )

    # Latest version of this policy can be found at:
    # https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json
    with open("./infra/aws/alb_controller_iam_policy.json") as policy_file:
        policy_doc = policy_file.read()

    service_account = iam.declare_service_account(
        name=name,
        namespace=namespace,
        cluster=cluster,
        k8s_provider=k8s_provider,
        policy_json=pulumi.Output.from_input(policy_doc),
    )

    k8s.helm.v3.Release(
        name,
        version="1.6.1",
        chart="aws-load-balancer-controller",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://aws.github.io/eks-charts",
        ),
        namespace=namespace.metadata.name,
        values={
            "region": cluster.core.aws_provider.region,
            "serviceAccount": {
                "name": service_account.metadata.name,
                "create": False,
            },
            "ingressClass": "alb",
            "vpcId": cluster.eks_cluster.vpc_config.vpc_id,
            "clusterName": cluster.eks_cluster.name,
            "podLabels": {
                "app": name,
            },
        },
        opts=pulumi.ResourceOptions(
            parent=namespace,
            provider=k8s_provider,
            # Ignore changes to checksum; bug in pulumi-kubernetes; see:
            # https://github.com/pulumi/pulumi-kubernetes/issues/2649
            # Remove this once that issue is resolved.
            ignore_changes=["checksum"],
        ),
    )
