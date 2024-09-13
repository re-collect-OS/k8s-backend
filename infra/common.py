# -*- coding: utf-8 -*-
from base64 import b64encode
from typing import Optional

import pulumi


def to_resource_name(name: str) -> str:
    """
    Converts a given name to a Kubernetes resource name by replacing
    underscores with hyphens and converting to lowercase.
    """
    return name.replace("_", "-").lower()


def image_sha(
    image: pulumi.Output[str],
    length: Optional[int] = 7,
) -> pulumi.Output[str]:
    """
    Extracts the SHA256 digest from a Docker image reference.

    The length of the digest can be specified; defaults to 7 characters.
    """
    return image.apply(lambda i: i.split("sha256:")[1][0:length])


def to_k8s_secret(cfg: pulumi.Config, key: str) -> pulumi.Output[str]:
    """
    Read the given key from the pulumi configuration object and return it as a
    base64-encoded string to populate a value for a Kubernetes Secret.
    """
    value = cfg.require_secret(key)
    return value.apply(lambda s: base64_str(s))


def base64_str(input: str) -> str:
    string_bytes = input.encode("utf-8")
    base64_bytes = b64encode(string_bytes)
    return base64_bytes.decode("utf-8")
