# -*- coding: utf-8 -*-
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hamcrest import assert_that, equal_to, is_

from common.features.features import Killswitch
from http_servers.middleware.maintenance import MaintenanceMiddleware


class TestKillswitch(Killswitch):
    enabled: bool = False

    def is_enabled(self) -> bool:
        return self.enabled

    @property
    def key(self) -> str:
        return "test-ks"


@pytest.fixture
def killswitch() -> TestKillswitch:
    return TestKillswitch()


@pytest.fixture
def test_app(killswitch: Killswitch) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MaintenanceMiddleware, killswitch=killswitch)

    @app.get("/")
    async def _() -> str:
        return "hello"

    return app


def test_maintenance_middleware(
    test_app: FastAPI,
    killswitch: TestKillswitch,
):
    client = TestClient(test_app)

    killswitch.enabled = True
    response = client.get("/")
    assert_that(response.status_code, is_(equal_to(503)))
    killswitch.enabled = False
    response = client.get("/")
    assert_that(response.status_code, is_(equal_to(200)))
