# -*- coding: utf-8 -*-
from datetime import UTC, datetime
from typing import Optional

from dateutil import parser


def datetime_to_iso_8601_str(t: datetime | None) -> str:
    if t is None:
        return None

    if t.tzinfo is None:
        # python's isoformat() is non RFC3339 compliant if the datetime object
        # does not have timezone information. See:
        # https://stackoverflow.com/a/23705687/366091
        t = t.replace(tzinfo=UTC)

    return t.isoformat()


def str_to_datetime(t: str | None) -> Optional[datetime]:
    if t is None:
        return None

    date = parser.parse(t)
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)

    return date
