# -*- coding: utf-8 -*-
from dataclasses import dataclass

import pulumi
import pulumi_docker as docker
import pulumi_eks
import pulumi_kubernetes as k8s

from .aws import iam
from .aws.ecr import declare_image_in_ecr
from .common import image_sha, to_resource_name
from .datadog.agent import datadog_annotations, datadog_labels


@dataclass
class AppZone:
    name: str
    namespace: k8s.core.v1.Namespace
    config_map: k8s.core.v1.ConfigMap
    secrets: k8s.core.v1.Secret
    k8s_provider: k8s.Provider
    cluster: pulumi_eks.Cluster


def declare_app_zone(
    name: str,
    cluster: pulumi_eks.Cluster,
    config_kv_pairs: dict[str, pulumi.Output[str] | str] = {},
    secret_kv_pairs: dict[str, pulumi.Output[str] | str] = {},
) -> AppZone:
    """
    Create a new application zone in the cluster.

    An AppZone is a logical grouping of apps that should live in an isolated
    space. Currently this is just logical grouping, but is meant to be a
    foundation for future work where we can restrict e.g. communication between
    apps in different app zones, outbound access, etc.

    Args:
        name (str): The name of the AppZone.
        cluster (pulumi_eks.Cluster): The EKS cluster to create the AppZone in.
        config_kv_pairs (dict[str, pulumi.Output[str] | str], optional):
            A dictionary of key-value pairs to store in the AppZone's ConfigMap.
            Defaults to {}.
        secret_kv_pairs (dict[str, pulumi.Output[str] | str], optional):
            A dictionary of key-value pairs to store in the AppZone's Secret.
            Defaults to {}.
    """
    name = to_resource_name(name)

    k8s_provider = k8s.Provider(
        f"{name}-provider",
        enable_server_side_apply=True,
        kubeconfig=cluster.kubeconfig_json,
        namespace=f"{name}",
    )

    namespace = k8s.core.v1.Namespace(
        f"{name}",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=name,
            labels={
                "app.kubernetes.io/name": name,
            },
        ),
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[k8s_provider, cluster],
        ),
    )

    config_map = k8s.core.v1.ConfigMap(
        f"{name}-config",
        data=config_kv_pairs,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"{name}-config",
            namespace=namespace.metadata.name,
        ),
        opts=pulumi.ResourceOptions(
            parent=namespace,
            provider=k8s_provider,
            depends_on=[namespace],
        ),
    )

    secrets = k8s.core.v1.Secret(
        f"{name}-secrets",
        data=secret_kv_pairs,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"{name}-secret",
            namespace=namespace.metadata.name,
        ),
        opts=pulumi.ResourceOptions(
            parent=namespace,
            provider=k8s_provider,
            depends_on=[namespace],
        ),
    )

    return AppZone(
        name=name,
        namespace=namespace,
        config_map=config_map,
        secrets=secrets,
        k8s_provider=k8s_provider,
        cluster=cluster,
    )


@dataclass
class App:
    resource_name: str
    zone: AppZone
    service_account: k8s.core.v1.ServiceAccount
    image: docker.Image
    deployment: k8s.apps.v1.Deployment


def declare_app(
    app_name: str,
    env: pulumi.Output[str] | str,
    zone: AppZone,
    dockerfile_or_image: docker.Image | str,
    replicas: int = 1,
    env_overrides: dict[str, str] = {},
    is_allowed_to: list[pulumi.Output[iam.PolicyStatement]] = [],
) -> App:
    """
    Declare an application to run in the cluster.

    An application consists of:
    - A service account that'll be attached to the deployment for the app. This
        service account will be able to assume an IAM role with the access
        policies supplied below (which will grant the pods access to AWS
        resources).
    - An ECR repo that'll be used to host built images for the app.
    - A docker image build process for the app.
    - A deployment for this app, with the service account created above
        attached to it (so pods can access necessary AWS resources).

    Args:
        app_name (str): The name of the application.
        env (pulumi.Output[str] | str): The environment where the application
            will be deployed.
        zone (AppZone): The zone where the application will be deployed.
        dockerfile_or_image (str | docker.Image): Either a docker.Image
            instance, or a path to a Dockerfile to build the image from.
            When a dockerfile is provided, an ECR repo will be created to host
            the image.
        replicas (int, optional): The number of replicas to create for the
            application. Defaults to 1.
        env_overrides (dict[str, str], optional): A dictionary of environment
            variables to override. Defaults to {}.
        is_allowed_to (list[pulumi.Output[iam.PolicyStatement]], optional):
            A list of IAM policy statements that define the permissions granted
            to the application. Defaults to [].
    """

    # Create a service account that'll be attached to the deployment for the
    # app. This service account will be able to assume an IAM role with the
    # access policies supplied below (which will grant the pods access to AWS
    # resources).
    res_name = to_resource_name(app_name)
    service_account = iam.declare_service_account(
        res_name,
        namespace=zone.namespace,
        policy_json=iam.policy_json_from_statements(*is_allowed_to),
        cluster=zone.cluster,
        k8s_provider=zone.k8s_provider,
    )

    if isinstance(dockerfile_or_image, docker.Image):
        image = dockerfile_or_image
    else:
        image = declare_image_in_ecr(
            name=app_name,
            aws_provider=zone.cluster.core.aws_provider,
            dockerfile=dockerfile_or_image,  # dockerfile
        ).image

    # Finally, declare the deployment for this app, with the service account
    # created above attached to it (so pods can access necessary AWS resources).
    deployment = k8s.apps.v1.Deployment(
        res_name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=res_name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=replicas,
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                # Roll out pods with new version one at a time.
                # This'll have to be revisited if we end up with a service that
                # needs many replicas (1x1 rollouts would take a long time).
                type="RollingUpdate",
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=1,
                    max_unavailable=1,
                ),
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels={
                    "app": res_name,
                },
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels={
                        "app": res_name,
                    },
                    annotations={
                        # image.repo_digest changes whenever a new image is
                        # built, which will result in a deployment update (and
                        # the consequent rolling restart of pods).
                        "image-sha": image_sha(image.repo_digest),
                    },
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    service_account_name=service_account.metadata.name,
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name=res_name,
                            image=image.image_name,
                            image_pull_policy="Always",
                            stdin=True,
                            tty=True,
                            env_from=[
                                k8s.core.v1.EnvFromSourceArgs(
                                    config_map_ref=k8s.core.v1.ConfigMapEnvSourceArgs(
                                        name=zone.config_map.metadata.name,
                                    )
                                ),
                                k8s.core.v1.EnvFromSourceArgs(
                                    secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                        name=zone.secrets.metadata.name,
                                    )
                                ),
                            ],
                            env=[
                                k8s.core.v1.EnvVarArgs(name=key, value=value)
                                for key, value in env_overrides.items()
                            ],
                        )
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=zone.k8s_provider,
            parent=zone.namespace,  # top-level under namespace
            depends_on=[
                image,
                service_account,
            ],
        ),
    )

    return App(
        resource_name=res_name,
        zone=zone,
        service_account=service_account,
        image=image,
        deployment=deployment,
    )
