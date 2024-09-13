# -*- coding: utf-8 -*-
from dataclasses import dataclass

import pulumi
import pulumi_aws
import pulumi_docker as docker

from ..common import to_resource_name


@dataclass
class ECRImage:
    repo: pulumi_aws.ecr.Repository
    image: docker.Image


def declare_image_in_ecr(
    name: str,
    aws_provider: pulumi_aws.Provider,
    dockerfile: str,
) -> ECRImage:
    """
    Declare a Docker image build and ECR repository for it.

    Args:
        name (str): Name for the repository and image.
        aws_provider (pulumi_aws.Provider): The AWS provider to use.
        dockerfile (str): Path to the Dockerfile to build.
    """
    res_name = to_resource_name(name)

    # Declare the ECR repo that'll be used to host built images for this app.
    repo = pulumi_aws.ecr.Repository(
        f"{res_name}-repository",
        name=res_name,  # ECR repo name
        force_delete=True,
        opts=pulumi.ResourceOptions(
            provider=aws_provider,
            parent=aws_provider,
        ),
    )

    auth_token = pulumi_aws.ecr.get_authorization_token_output(
        registry_id=repo.registry_id,
    )

    image_name = repo.repository_url.apply(lambda url: f"{url}:latest")

    # Declare the docker image build process for this app.
    image = docker.Image(
        f"{res_name}-image",
        build=docker.DockerBuildArgs(
            args={"BUILDKIT_INLINE_CACHE": "1"},
            cache_from=docker.CacheFromArgs(images=[image_name]),
            context="./",
            dockerfile=dockerfile,
            platform="linux/amd64",
        ),
        image_name=image_name,
        registry=docker.RegistryArgs(
            username=auth_token.user_name,
            password=auth_token.password,
            server=auth_token.proxy_endpoint,
        ),
        opts=pulumi.ResourceOptions(
            # Built locally; needs no provider.
            parent=repo,  # image makes no sense without repo
            depends_on=[repo],
        ),
    )

    return ECRImage(
        repo=repo,
        image=image,
    )
