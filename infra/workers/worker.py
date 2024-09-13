# -*- coding: utf-8 -*-
import pulumi
import pulumi_docker as docker

from ..app import AppZone, declare_app
from ..aws import iam


def declare_worker(
    worker_name: str,
    zone: AppZone,
    env: pulumi.Output[str] | str,
    dockerfile_or_image: str | docker.Image,
    replicas: int = 1,
    env_overrides: dict[str, str] = {},
    is_allowed_to: list[pulumi.Output[iam.PolicyStatement]] = [],
) -> None:
    declare_app(
        app_name=worker_name,
        zone=zone,
        env=env,
        replicas=replicas,
        dockerfile_or_image=dockerfile_or_image,
        env_overrides={
            "WORKER_MODULE": worker_name,
            **env_overrides,
        },
        is_allowed_to=is_allowed_to,
    )
