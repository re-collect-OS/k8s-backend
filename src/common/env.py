# -*- coding: utf-8 -*-
import os


def require_str(name: str) -> str:
    """Get an environment variable or raise an exception."""
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"Missing required env var: {name}")
    if len(value) == 0:
        raise RuntimeError(f"Empty required env var: {name}")
    return value


def is_local_development() -> bool:
    return os.getenv("ENV") == "local"


def is_production() -> bool:
    return os.getenv("ENV") == "prod"
