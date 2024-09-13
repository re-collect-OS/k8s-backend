# -*- coding: utf-8 -*-
import os

import weaviate

from common import env


def self_hosted_client() -> weaviate.Client:
    return weaviate.Client(
        url=env.require_str("WEAVIATE_URL"),
        auth_client_secret=weaviate.AuthApiKey(
            # NB: No key in staging/prod, hence os.getenv() vs env.require_str()
            api_key=(os.getenv("WEAVIATE_API_KEY") or "")
        ),
    )
