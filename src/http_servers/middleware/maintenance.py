# -*- coding: utf-8 -*-
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from common import killswitches
from common.features.features import Killswitch


class MaintenanceMiddleware(BaseHTTPMiddleware):
    """
    Middleware that returns 503 while maintenance killswitch is enabled.

    Used to prevent requests from reaching the application during maintenance
    windows.
    """

    def __init__(
        self,
        app: FastAPI,
        killswitch: Killswitch = killswitches.maintenance,
    ):
        super().__init__(app)
        self._killswitch = killswitch

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if self._killswitch.is_enabled():
            return Response(
                status_code=503,
                content="Service is undergoing maintenance",
            )
        return await call_next(request)
