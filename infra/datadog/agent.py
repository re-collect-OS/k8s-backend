# -*- coding: utf-8 -*-
from typing import Dict, Optional

import pulumi
import pulumi_eks
import pulumi_kubernetes as k8s


def declare_datadog_cluster_agent(
    api_key: pulumi.Output[str],
    cluster: pulumi_eks.Cluster,
    k8s_provider: Optional[k8s.Provider],
) -> None:
    """
    Deploy the DataDog Kubernetes Cluster Agent under the "datadog" namespace.

    For more information, refer to the [official guide](https://docs.datadoghq.com/agent/kubernetes/cluster/).
    """

    ns_id = "datadog"
    namespace = k8s.core.v1.Namespace(
        ns_id,
        metadata={
            "name": ns_id,
            "labels": {
                "app.kubernetes.io/name": ns_id,
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[cluster],
        ),
    )

    k8s.helm.v3.Release(
        "datadog-agent",
        version="3.40.2",
        chart="datadog",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://helm.datadoghq.com",
        ),
        namespace=namespace.metadata.name,
        value_yaml_files=[pulumi.FileAsset("./infra/datadog/values.yaml")],
        # Overrides (or extra key-values) applied to values.yaml
        values={
            "datadog": {
                "apiKey": api_key,
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            parent=namespace,
            depends_on=[cluster],
            # Ignore changes to checksum; bug in pulumi-kubernetes; see:
            # https://github.com/pulumi/pulumi-kubernetes/issues/2649
            # Remove this once that issue is resolved.
            ignore_changes=["checksum"],
        ),
    )


def datadog_labels(
    env: pulumi.Output[str] | str,
    service: pulumi.Output[str] | str,
) -> Dict[str, pulumi.Output[str] | str]:
    return {
        "tags.datadoghq.com/env": env,
        "tags.datadoghq.com/service": service,
        # Mark the pod as a target for the DataDog Admission Controller mutation
        # webhook (see infra/datadog/values.yaml#clusterAgent.admissionController)
        "admission.datadoghq.com/enabled": "true",
    }


def datadog_annotations(
    enable_apm: bool = False,
) -> Dict[str, str]:
    annotations: Dict[str, str] = {}

    if enable_apm:
        # Instrumentation (traces) for Python services.
        # Releases: https://gallery.ecr.aws/datadog/dd-lib-python-init
        annotations["admission.datadoghq.com/python-lib.version"] = "v1.20.5"

    return annotations
