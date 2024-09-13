# -*- coding: utf-8 -*-
import os
from typing import Any

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Asm, Mail


class ExternalModel(BaseModel):
    event_id: str
    form_response: dict[str, Any]


class Response(BaseModel):
    email: str | None


# /public/typeform/waitlist


class TypeformController:
    def __init__(
        self,
        ext_model_cls: type[ExternalModel] = ExternalModel,
    ) -> None:
        self.ext_model_cls = ext_model_cls

    def handle_submission(
        self,
        data: ExternalModel,
    ) -> Response:
        email = None

        for answer in data.form_response["answers"]:
            if answer["field"]["type"] == "email":
                email = answer["email"]

        if not email:
            raise HTTPException(status_code=400, detail=f"email not found")

        message = Mail(
            from_email=("hello@re-collect.ai", "re:collect"),
            to_emails=email,
        )
        message.template_id = os.environ["SENDGRID_WAITLIST_CONFIRMATION"]
        message.asm = Asm(int(os.environ["SENDGRID_ASM_GROUP_ID"]))

        # no PID in logs
        redacted_email = f"###@{email.split('@')[-1]}"
        try:
            sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
            _ = sg.send(message)  # type: ignore
            logger.success(f"Waitlist-confirmation email to {redacted_email} sent")
        except Exception as e:
            logger.warning(
                f"Failed to send waitlist-confirmation email to {redacted_email}, {e}"
            )

        return Response(email=email)
