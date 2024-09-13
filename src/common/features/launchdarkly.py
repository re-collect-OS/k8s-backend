# -*- coding: utf-8 -*-
from abc import ABC
from dataclasses import is_dataclass
from typing import Optional, TypeVar
from venv import logger

import ldclient
import ldclient.evaluation

from .features import Experiment, Features, Killswitch, Operational, Permission, Release
from .types import check_expected_value_type, check_valid_type

# Timeout to wait for a successful connection to LaunchDarkly (or relay proxy).
_LD_CONNECTION_TIMEOUT_SECS = 5

T = TypeVar("T")


def _eval(
    key: str,
    ctx: ldclient.Context,
    client: ldclient.LDClient,
    type_cls: type[T],
    default: Optional[T] = None,
) -> T:
    """
    Evaluate a LaunchDarkly feature flag for a given key and return the value.

    Args:
        key (str): The key of the feature flag to evaluate.
        ctx (ldclient.Context): The context to use for evaluation.
        client (ldclient.LDClient): The LaunchDarkly client instance to use for evaluation.
        type_cls (type[T]): The expected type of the flag value.
        default (Optional[T], optional): The default value to use if the flag evaluation fails.
            Defaults to None.

    Returns:
        T: The evaluated flag value.

    Raises:
        Exception: If the flag evaluation fails and no default value is specified.
    """

    # Overzealous checks against programmer error.
    check_valid_type(key, type_cls, default)

    # Evaluate the flag.
    detail = client.variation_detail(key, ctx, default=None)
    err_kind: Optional[str] = detail.reason.get("errorKind", None)
    if err_kind is not None:
        if default is None:
            # Raise if eval cannot tolerate errors (i.e. no default specified).
            raise Exception(f"error evaluating {key}: {err_kind}")
        else:
            # Otherwise log a warning and assume a default value.
            logger.warning(
                f"Error evaluating {key}: {err_kind}; "
                f"returning default value ({default})"
            )

    ret_val = detail.value or default
    if ret_val is None:
        # NB: This cannot happen since:
        # - if the flag eval succeeds, detail.value will always have a value.
        # - if the flag eval fails, err_kind check above will either raise or
        #   guarantee a default value.
        raise Exception(
            f"error evaluating {key}: "
            "evaluation returned None and no default value specified"
        )

    if is_dataclass(type_cls):
        # When type_cls is a dataclass, expect flag value type to be JSON. These
        # are returned as a dict by the LaunchDarkly SDK.
        check_expected_value_type(key, dict, ret_val)
        return type_cls(**ret_val)
    else:
        # Otherwise, we assume the flag value type to be a primitive type
        check_expected_value_type(key, type_cls, ret_val)
        return type_cls(ret_val)


class _LDFeature(ABC):
    def __init__(self, key: str, client: ldclient.LDClient):
        self._key = key
        self._client = client

    @property
    def key(self) -> str:
        return self._key


class _LDRelease(_LDFeature, Release):
    def __init__(self, key: str, client: ldclient.LDClient, default: bool):
        super().__init__(key, client)
        self._default = default

    def is_enabled(self) -> bool:
        ctx = ldclient.Context.create("default")
        return _eval(self.key, ctx, self._client, bool, self._default)


class _LDExperiment(_LDFeature, Experiment):
    def __init__(self, key: str, client: ldclient.LDClient, default: bool):
        super().__init__(key, client)
        self._default = default

    def is_enabled(self, user: str) -> bool:
        ctx = ldclient.Context.create(user)
        return _eval(self.key, ctx, self._client, bool, self._default)


class _LDOperational(_LDFeature, Operational[T]):
    def __init__(
        self,
        key: str,
        client: ldclient.LDClient,
        type_cls: type[T],
        default: T,
    ):
        super().__init__(key, client)
        self._type_cls = type_cls
        self._default = default

    def get(self) -> T:
        ctx = ldclient.Context.create("default")
        return _eval(self.key, ctx, self._client, self._type_cls, self._default)


class _LDKillswitch(_LDFeature, Killswitch):
    def __init__(self, key: str, client: ldclient.LDClient):
        super().__init__(key, client)

    def is_enabled(self):
        ctx = ldclient.Context.create("default")
        return _eval(self.key, ctx, self._client, bool, False)


class _LDPermission(_LDFeature, Permission):
    def __init__(self, key: str, client: ldclient.LDClient, default: bool):
        super().__init__(key, client)
        self._default = default

    def is_allowed(self, actor: str) -> bool:
        ctx = ldclient.Context.create(actor)
        return _eval(self.key, ctx, self._client, bool, self._default)


class LDFeatures(Features):
    """LaunchDarkly implementation for Features."""

    def __init__(self, config: ldclient.Config):
        self._client = ldclient.LDClient(
            config=config,
            start_wait=_LD_CONNECTION_TIMEOUT_SECS,
        )

    def release(
        self,
        key: str,
        default_enabled: bool = False,
    ) -> Release:
        return _LDRelease(f"release.{key}", self._client, default_enabled)

    def experiment(
        self,
        key: str,
        default_enabled: bool = False,
    ) -> Experiment:
        return _LDExperiment(f"experiment.{key}", self._client, default_enabled)

    def operational(
        self,
        key: str,
        type_cls: type[T],
        default_value: T,
    ) -> Operational[T]:
        key = f"operational.{key}"
        check_valid_type(key, type_cls, None)
        return _LDOperational(key, self._client, type_cls, default_value)

    def killswitch(self, key: str) -> Killswitch:
        return _LDKillswitch(f"killswitch.{key}", self._client)

    def permission(
        self,
        key: str,
        default_allowed: bool = False,
    ) -> Permission:
        return _LDPermission(f"permission.{key}", self._client, default_allowed)
