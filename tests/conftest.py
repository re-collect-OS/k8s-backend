# -*- coding: utf-8 -*-
import pytest

from .test_lib.services import TestServices


@pytest.fixture(scope="session")
def external_deps():
    with TestServices() as services:
        yield services
