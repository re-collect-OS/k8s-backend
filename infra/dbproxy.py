# -*- coding: utf-8 -*-
from typing import Mapping

import pulumi
import pulumi_kubernetes as k8s

from .app import AppZone


def declare_dbproxy(
    zone: AppZone,
) -> None:
    res_name = "dbproxy"

    data: pulumi.Output[Mapping[str, str]] = zone.config_map.data
    target_address = data.apply(
        lambda d: f"tcp4:{d['POSTGRESQL_HOST']}:{d['POSTGRESQL_PORT']}"
    )

    k8s.apps.v1.Deployment(
        res_name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=res_name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
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
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name=res_name,
                            image="alpine/socat",
                            image_pull_policy="Always",
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
                            command=[
                                "socat",
                                "-dd",
                                "tcp4-listen:5432,fork,reuseaddr",
                                target_address,
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                limits={
                                    "cpu": "20m",
                                    "memory": "32Mi",
                                },
                            ),
                        )
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider=zone.k8s_provider,
            parent=zone.namespace,  # top-level under namespace
        ),
    )
