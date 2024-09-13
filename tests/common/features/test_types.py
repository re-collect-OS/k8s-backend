# -*- coding: utf-8 -*-
from dataclasses import dataclass

from common.features.types import check_expected_value_type, check_valid_type


@dataclass
class Foo:
    bar: str


class Bar:
    pass


def test_check_valid_types():
    check_valid_type("bool-flag", bool, None)
    check_valid_type("bool-flag", bool, True)
    check_valid_type("str-flag", str, None)
    check_valid_type("str-flag", str, "foo")
    check_valid_type("int-flag", int, None)
    check_valid_type("int-flag", int, 42)
    check_valid_type("dataclass-flag", Foo, None)
    check_valid_type("dataclass-flag", Foo, Foo(bar="baz"))

    try:
        check_valid_type("list-flag", list, ["one", 2])
        assert False, "expected TypeError"
    except TypeError:
        pass

    try:
        check_valid_type("dict-flag", dict, {"one": 2})
        assert False, "expected TypeError"
    except TypeError:
        pass

    try:
        check_valid_type("class-flag", Bar, None)
        assert False, "expected TypeError"
    except TypeError:
        pass


def test_check_expected_value_type():
    check_expected_value_type("bool-flag", bool, True)
    check_expected_value_type("str-flag", str, "foo")
    check_expected_value_type("int-flag", int, 42)
    check_expected_value_type("foo-flag", dict, {"bar": "baz"})

    try:
        check_expected_value_type("bool-flag", str, True)
        assert False, "expected TypeError"
    except TypeError:
        pass

    try:
        check_expected_value_type("str-flag", int, "foo")
        assert False, "expected TypeError"
    except TypeError:
        pass
