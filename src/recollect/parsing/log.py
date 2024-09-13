# -*- coding: utf-8 -*-
import logging
import os
import sys
import typing as t


class EndpointFilter(logging.Filter):
    # remove ALB health checks from datadog logs
    def __init__(
        self,
        path: str,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        super().__init__(*args, **kwargs)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1


LOG_CONFIG: dict[str, t.Any] = {
    "handlers": [
        {
            "sink": sys.stdout,
            "colorize": False,
            "format": "{level} | {message}",
            "level": os.getenv("LOG_LEVEL", "INFO"),
        },
    ],
}

uvicorn_logger = logging.getLogger("uvicorn.access")
