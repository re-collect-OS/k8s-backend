# -*- coding: utf-8 -*-
from http import HTTPStatus
from typing import Any, TypeVar

from fastapi import Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel


def created(location: str, data: BaseModel) -> JSONResponse:
    return JSONResponse(
        status_code=HTTPStatus.CREATED,
        headers={"Location": location},
        content=_json_dump(data),
    )


T = TypeVar("T", bound=BaseModel)


def ok(data: BaseModel | list[T]) -> JSONResponse:
    if isinstance(data, list):
        content = [_json_dump(item) for item in data]
    else:
        content = _json_dump(data)

    return JSONResponse(status_code=HTTPStatus.OK, content=content)


def accepted() -> Response:
    return Response(status_code=HTTPStatus.ACCEPTED)


def no_content() -> Response:
    return Response(status_code=HTTPStatus.NO_CONTENT)


def _json_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True, mode="json")


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(status_code=HTTPStatus.TEMPORARY_REDIRECT, url=url)
