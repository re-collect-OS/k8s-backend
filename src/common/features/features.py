# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Generic, TypeVar


class BaseToggle(ABC):
    """Base class for all feature toggles."""

    @property
    @abstractmethod
    def key(self) -> str:
        raise NotImplementedError()


class Release(BaseToggle, ABC):
    """
    A feature toggle used to enable/disable codepaths for unfinished features.

    Allows deploying to production without exposing unfinished features to
    users.

    Expected lifetime: 30 days.
    """

    @abstractmethod
    def is_enabled(self) -> bool:
        pass


class Experiment(BaseToggle, ABC):
    """
    A feature toggle used to perform multivariate or A/B testing, determining
    which of two codepaths activates for a given user.

    Expected lifetime: 30 days.
    """

    @abstractmethod
    def is_enabled(self, user: str) -> bool:
        pass


T = TypeVar("T")


class Operational(Generic[T], BaseToggle, ABC):
    """
    A feature toggle used to control operational aspects of the system's
    behavior.

    Allows quick experimentation of infrastruction settings (timeouts, retries,
    delays, etc.) until an optimal configuration is found.

    These features can contain the following types of values:
    - int, bool, str
    - dataclasses

    Expected lifetime: 7-30 days.
    """

    @abstractmethod
    def get(self) -> T:
        pass


class Killswitch(BaseToggle, ABC):
    """
    A killswitch is a particular type of operational toggle that can be
    seen as a manually-managed circuit breaker.

    These features are boolean and take no further context information — each
    killswitch should target one specific, narrow piece of functionality.

    Examples: disabling a specific API endpoint, load shedding, etc.

    Expected lifetime: permanent.
    """

    @abstractmethod
    def is_enabled(self) -> bool:
        pass


class Permission(BaseToggle, ABC):
    """
    A feature toggle used to change the features or product experience that
    certain users receive.

    Example: restricting certain APIs to administration accounts.

    Expected lifetime: permanent.
    """

    @abstractmethod
    def is_allowed(self, actor: str) -> bool:
        pass


class Features(ABC):
    """
    Contract for a source of feature toggles.

    Based on https://martinfowler.com/articles/feature-toggles.html
    """

    @abstractmethod
    def release(
        self,
        key: str,
        default_enabled: bool = False,
    ) -> Release:
        pass

    @abstractmethod
    def experiment(
        self,
        key: str,
        default_enabled: bool = False,
    ) -> Experiment:
        pass

    @abstractmethod
    def operational(
        self,
        key: str,
        type_cls: type[T],
        default_value: T,
    ) -> Operational[T]:
        pass

    @abstractmethod
    def killswitch(self, key: str) -> Killswitch:
        pass

    @abstractmethod
    def permission(
        self,
        key: str,
        default_allowed: bool = False,
    ) -> Permission:
        pass
