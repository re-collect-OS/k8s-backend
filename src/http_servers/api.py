# -*- coding: utf-8 -*-
import os
from uuid import UUID

from boto3 import client
from botocore.exceptions import NoCredentialsError
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from mypy_boto3_s3 import S3Client
from starlette.responses import RedirectResponse

from common.sqldb import sqldb_from_env

from .controllers.recurring_imports.apple_notes import AppleNotesController
from .controllers.recurring_imports.base import AnyController
from .controllers.recurring_imports.google_drive import GoogleDriveController
from .controllers.recurring_imports.readwise_v2 import ReadwiseV2Controller
from .controllers.recurring_imports.readwise_v3 import ReadwiseV3Controller
from .controllers.recurring_imports.rss import RSSController
from .controllers.recurring_imports.twitter import TwitterController
from .controllers.tracking_sessions import TrackingSessionsController
from .controllers.typeform import TypeformController
from .middleware.auth import CognitoAuthMiddleware
from .middleware.maintenance import MaintenanceMiddleware
from .responses import accepted, created, no_content, ok

logger.configure(**LOG_CONFIG)
uvicorn_logger.addFilter(EndpointFilter(path="/"))
sqldb = sqldb_from_env()

_bucket_name = os.environ["S3_BUCKET_USERFILES"]
_s3: S3Client = client(
    "s3",
    aws_access_key_id=os.environ["S3_AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["S3_AWS_SECRET_ACCESS_KEY"],
)


# API V2 ----------------------------------------------------------------------

allowed_origins = (os.getenv("ALLOW_ORIGINS") or "").split(",")

api_v2 = FastAPI(
    title="API v2 authenticated routes",
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
# Apply the auth middleware before CORS to allow the correct preflight request overrides
# See: https://www.starlette.io/middleware/#:~:text=of%20HTTP%20request...-,CORS%20preflight%20requests,-These%20are%20any
api_v2.add_middleware(CognitoAuthMiddleware)
api_v2.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
api_v2.add_middleware(MaintenanceMiddleware)

# /recurring-imports/*/

_recurring_imports_controllers = {
    # NB: The key string will be part of the path in the URL.
    "apple-notes": AppleNotesController(),
    "readwise-v2": ReadwiseV2Controller(),
    "readwise-v3": ReadwiseV3Controller(),
    "rss": RSSController(),
    "twitter": TwitterController(),
    "google-drive": GoogleDriveController(),
    # Add more implementations of RecurringImportsController here.
}


def _get_recurring_imports_controller(source: str) -> AnyController:
    controller = _recurring_imports_controllers.get(source)
    if controller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown recurring import source: {source}",
        )
    return controller  # type: ignore (generics in python are just terrible)


@api_v2.post("/recurring-imports/{source}")
async def create_recurring_import_with_source(
    request: Request,
    source: str,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    # Massage the request body into the correct class for the integration.
    config = controller.ext_settings_cls.model_validate_json(await request.body())
    new_record = controller.create(request.user, config)
    return created(
        location=f"/recurring-imports/{source}/{new_record.id}",
        data=new_record,
    )


@api_v2.get("/recurring-imports/{source}")
async def list_recurring_imports_by_source(
    request: Request,
    source: str,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    return ok(data=controller.list(request.user))


@api_v2.post("/recurring-imports/external-auth/google-drive")
async def redirect_to_auth_by_source(
    request: Request,
) -> Response:
    controller = GoogleDriveController()
    payload = await request.json()
    redirect_uri = payload.get("redirect_uri", None)
    return ok(controller.get_authorization_redirect(redirect_uri))


@api_v2.get("/recurring-imports/{source}/{id}")
async def read_recurring_import_by_source_and_id(
    request: Request,
    source: str,
    id: UUID,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    return ok(controller.read(request.user, id))


@api_v2.put("/recurring-imports/{source}/{id}")
async def update_recurring_import_by_source_and_id(
    request: Request,
    source: str,
    id: UUID,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    # Massage the request body into the correct class for the integration.
    update = controller.ext_settings_cls.model_validate_json(await request.body())
    return ok(controller.update_settings(request.user, id, update))


@api_v2.post("/recurring-imports/{source}/{id}/run")
async def create_new_recurring_import_run(
    request: Request,
    source: str,
    id: UUID,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    controller.run_now(request.user, id)
    return accepted()


@api_v2.patch("/recurring-imports/{source}/{id}")
async def patch_recurring_import_by_source_and_id(
    request: Request,
    source: str,
    id: UUID,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    # Massage the request body into the correct class for the integration.
    patch = controller.ext_patch_cls.model_validate_json(await request.body())
    return ok(controller.patch_settings(request.user, id, patch))


@api_v2.delete("/recurring-imports/{source}/{id}")
async def delete_recurring_import_by_source_and_id(
    request: Request,
    source: str,
    id: UUID,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    controller.delete(request.user, id)
    return no_content()


@api_v2.delete("/recurring-imports/{source}")
async def delete_recurring_imports_by_source(
    request: Request,
    source: str,
) -> Response:
    controller = _get_recurring_imports_controller(source)
    controller.delete_all(request.user)
    return no_content()


# /tracking-sessions
@api_v2.post("/tracking-sessions/log")
async def log_tracking_sessions(
    request: Request,
) -> Response:
    controller = TrackingSessionsController()
    data = controller.ext_model_cls.model_validate_json(await request.body())
    return ok(controller.log_sessions(request.user, data.sessions))


# e.g., /thumbnail/?s3_path=path/to/screenshot_thumbnail.jpg
@api_v2.get("/thumbnail/")
async def get_thumbnail(s3_path: str):
    try:
        presigned_url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket_name, "Key": s3_path},
            ExpiresIn=3600,  # 1 hour
        )
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")

    if not presigned_url:
        raise HTTPException(status_code=404, detail="Failed to generate URL")

    # Redirect to the presigned URL
    return RedirectResponse(url=presigned_url, status_code=307)


# Public API

api_public = FastAPI(
    title="API v2 public routes",
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


@api_public.post("/typeform/waitlist")
async def typeform_webhook(
    request: Request,
) -> Response:
    controller = TypeformController()
    data = controller.ext_model_cls.model_validate_json(await request.body())
    return ok(controller.handle_submission(data))


# App -------------------------------------------------------------------------

app = FastAPI(
    title="re:collect public API",
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


@app.get("/", status_code=status.HTTP_200_OK)
async def get_root() -> str:
    # For ALB health checks; no middleware. Non-2xx code responses will cause
    # the ALB to mark the instance as unhealthy.
    return "ok"


app.mount(path="/v2", app=api_v2)
app.mount(path="/public", app=api_public)
