# -*- coding: utf-8 -*-
from dataclasses import dataclass

import pytest
from hamcrest import assert_that, is_
from ldclient import Config
from ldclient.integrations.test_data import TestData

from common.features.launchdarkly import LDFeatures


@pytest.fixture
def td() -> TestData:
    return TestData.data_source()


@pytest.fixture
def features(td: TestData) -> LDFeatures:
    return LDFeatures(config=Config("fake-key", update_processor_class=td))


def test_launchdarkly_release(features: LDFeatures, td: TestData):
    td.update(td.flag("release.always-disabled").on(False))
    assert_that(
        features.release("always-disabled").is_enabled(),
        is_(False),
    )

    td.update(td.flag("release.always-enabled").on(True))
    assert_that(
        features.release("always-enabled").is_enabled(),
        is_(True),
    )


def test_launchdarkly_experiment(features: LDFeatures, td: TestData):
    td.update(td.flag("experiment.disabled-for-all").fallthrough_variation(False))
    disabled_for_all_experiment = features.experiment("disabled-for-all")
    for user in ["user-1", "user-2"]:
        assert_that(
            disabled_for_all_experiment.is_enabled(user),
            is_(False),
        )

    td.update(td.flag("experiment.enabled-for-all").fallthrough_variation(True))
    enabled_for_all_experiment = features.experiment("enabled-for-all")
    for user in ["user-1", "user-2"]:
        assert_that(
            enabled_for_all_experiment.is_enabled(user),
            is_(True),
        )

    td.update(
        td.flag("experiment.selectively-enabled")
        .fallthrough_variation(False)
        .variation_for_user("user-1", True)
    )
    selectively_enabled_experiment = features.experiment("selectively-enabled")
    assert_that(
        selectively_enabled_experiment.is_enabled("user-1"),
        is_(True),
    )
    assert_that(
        selectively_enabled_experiment.is_enabled("user-2"),
        is_(False),
    )


def test_launchdarkly_killswitch(features: LDFeatures, td: TestData):
    td.update(td.flag("killswitch.maintenance-mode").fallthrough_variation(True))
    maintenance_mode = features.killswitch("maintenance-mode")
    assert_that(
        maintenance_mode.is_enabled(),
        is_(True),
    )


def test_launchdarkly_permission(features: LDFeatures, td: TestData):
    td.update(
        td.flag("permission.admin-access")
        .variation_for_user("admin@re-collect.ai", True)
        .fallthrough_variation(False)
    )

    admin_permission = features.permission("admin-access")
    assert_that(
        admin_permission.is_allowed("admin@re-collect.ai"),
        is_(True),
    )
    assert_that(
        admin_permission.is_allowed("user@re-collect.ai"),
        is_(False),
    )


def test_launchdarkly_operational(features: LDFeatures, td: TestData):
    td.update(td.flag("operational.str-value").value_for_all("foo"))

    # String operational flag backed by LD flag
    str_value_op = features.operational(
        key="str-value",
        type_cls=str,
        default_value="irrelevant",
    )
    assert_that(str_value_op.get(), is_("foo"))

    # String operational flag *not* backed by LD flag (i.e. missing flag)
    unset_op = features.operational(
        key="unset",
        type_cls=str,
        default_value="default",
    )
    assert_that(unset_op.get(), is_("default"))

    # Integer operational flag
    td.update(td.flag("operational.int-value").value_for_all(1))
    int_value_op = features.operational("int-value", int, default_value=-1)
    assert_that(int_value_op.get(), is_(1))

    # JSON/dataclass operational flag
    @dataclass
    class DC:
        a: str
        b: int

    td.update(td.flag("operational.dc").value_for_all({"a": "qux", "b": 1}))
    foo_value_op = features.operational(
        type_cls=DC,
        key="dc",
        default_value=DC(a="default", b=-1),
    )
    assert_that(
        foo_value_op.get(),
        is_(DC(a="qux", b=1)),
    )
