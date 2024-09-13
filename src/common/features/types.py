# -*- coding: utf-8 -*-
from dataclasses import is_dataclass
from typing import Optional, TypeVar

T = TypeVar("T")


def check_valid_type(
    flag_key: str,
    type_cls: type[T],
    default: Optional[T],
) -> None:
    """
    Type check for feature toggle types.

    Acceptable types are bool, int, str, and dataclass instances.

    :param type_cls: The type of the feature toggle.
    :param default: The value of the feature toggle, if any.

    :raises TypeError: When type_cls is not a bool, int, str or dataclass.
    :raises TypeError: When default is provided and its type does not match
        type_cls.
    """

    # Only allow a few primitive types and dataclasses.
    if not type_cls in (bool, str, int) and not is_dataclass(type_cls):
        raise TypeError(
            f"unsupported type {type_cls.__name__} for flag {flag_key} "
            "(must be bool, str, int or dataclass)"
        )

    # When a value is provided, check that its type matches type_cls.
    if default is not None and type(default) != type_cls:
        raise TypeError(
            f"expected default value to be {type_cls.__name__} "
            f"for flag {flag_key}, got {type(default).__name__}"
        )


def check_expected_value_type(
    flag_key: str,
    expected_type: type,
    value: object,
) -> None:
    if not isinstance(value, expected_type):
        raise TypeError(
            f"expected evaluation of {flag_key} to return "
            f"{expected_type.__name__}, got {type(value).__name__}"
        )
