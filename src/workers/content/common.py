# -*- coding: utf-8 -*-
from typing import Optional

from pydantic import BaseModel

from recollect.models.urlcontent import Urlcontent
from recollect.models.urlstate import Urlstate

_supported_encodings = {"utf-8", "iso-8859-1"}


def is_supported_encoding(encoding: str) -> bool:
    return encoding.lower() in _supported_encodings


def needs_content_removal_on_failure(
    urlcontent: Optional[Urlcontent],
    urlstate: Urlstate,
) -> bool:
    if (
        urlcontent is not None
        and urlcontent._metadata is not None
        and "source_entry" in urlcontent._metadata
        and urlcontent._metadata["source_entry"] is not None
    ):
        return True

    if urlstate.source == "history_import":
        return True

    return False


class RetrieveResult(BaseModel):
    content: str
    detail: str


class RetrieveError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
