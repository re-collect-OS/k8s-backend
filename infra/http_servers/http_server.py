# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Optional

import pulumi
import pulumi_docker as docker
import pulumi_kubernetes as k8s

from ..app import AppZone, declare_app
from ..aws import iam


@dataclass
class PublicHttpServer:
    certificate_arn: pulumi.Output[str] | str
    subdomains: list[str]
    domain: str


def declare_http_server(
    server_name: str,
    zone: AppZone,
    env: pulumi.Output[str] | str,
    dockerfile_or_image: str | docker.Image,
    replicas: int = 1,
    env_overrides: dict[str, str] = {},
    exposed_as: Optional[PublicHttpServer] = None,
    is_allowed_to: list[pulumi.Output[iam.PolicyStatement]] = [],
) -> None:
    if exposed_as is not None and len(exposed_as.subdomains) == 0:
        raise ValueError(
            f"declare_http_server({server_name}, exposed=...): "
            "must specify at least one subdomain to expose"
        )

    app = declare_app(
        app_name=server_name,
        zone=zone,
        env=env,
        replicas=replicas,
        dockerfile_or_image=dockerfile_or_image,
        env_overrides={
            "SERVER_MODULE": server_name,
            **env_overrides,
        },
        is_allowed_to=is_allowed_to,
    )

    service = k8s.core.v1.Service(
        app.resource_name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=app.resource_name,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            type="NodePort",
            selector={
                "app": app.resource_name,
            },
            ports=[
                k8s.core.v1.ServicePortArgs(
                    port=80,  # could be configurable, but no strong motivation
                    target_port=8080,  # hardcoded in start.sh
                    protocol="TCP",
                )
            ],
        ),
        opts=pulumi.ResourceOptions(
            provider=app.zone.k8s_provider,
            parent=app.deployment,  # service makes no sense without deployment
            depends_on=[app.deployment],
        ),
    )

    if exposed_as is not None:
        for subdomain in exposed_as.subdomains:
            _expose_service(
                service=service,
                env=env,
                host=f"{subdomain}.{exposed_as.domain}",
                certificate_arn=exposed_as.certificate_arn,
                k8s_provider=zone.k8s_provider,
            )


def _expose_service(
    service: k8s.core.v1.Service,
    host: str,
    env: pulumi.Output[str] | str,
    certificate_arn: pulumi.Output[str] | str,
    k8s_provider: k8s.Provider,
    path: str = "/",
) -> None:
    """
    Expose an HTTP server to internet traffic on the given host.

    Declares an Ingress with annotations for AWS Application Load Balancer.
    When ALB Controller is installed in the cluster, an AWS EC2 Load Balancer
    with linked Target Group will be created.

    Traffic to ports 80 and 443 will be routed to the service's port 80.

    As an example, a subdomain of "foo" for service "bar" would result in the
    creation of an ELB named "k8s-<namespace>-foo-<hash>" linked to a Target
    Group named "k8s-<namespace>-bar-<other_hash>".
    """

    k8s.networking.v1.Ingress(
        f"{host}-ingress",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=host,  # display name (for k8s dashboard/cli tools)
            annotations={
                # reference for ALB specific annotations:
                # https://kubernetes-sigs.github.io/aws-load-balancer-controller/v2.6/guide/ingress/annotations/
                "kubernetes.io/ingress.class": "alb",
                "alb.ingress.kubernetes.io/scheme": "internet-facing",
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTP": 80}, {"HTTPS":443}]',
                "alb.ingress.kubernetes.io/ssl-redirect": "443",
                "alb.ingress.kubernetes.io/certificate-arn": certificate_arn,
                "alb.ingress.kubernetes.io/tags": f"Environment={env}",
            },
        ),
        spec=k8s.networking.v1.IngressSpecArgs(
            rules=[
                k8s.networking.v1.IngressRuleArgs(
                    host=host,  # e.g. api.domain.com
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path=path,  # e.g. "/", "/some/path"
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=k8s.networking.v1.IngressServiceBackendArgs(
                                        name=service.metadata.name,
                                        port=k8s.networking.v1.ServiceBackendPortArgs(
                                            number=80,
                                        ),
                                    )
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            parent=service,  # ingress makes no sense without service
            depends_on=[service],
        ),
    )
