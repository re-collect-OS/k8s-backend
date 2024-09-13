# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from common.records.recurring_imports import RecurringImport
from common.records.recurring_imports_rss import RSSImportSettings
from recollect.schemas.user_account import UserAccountCreate, UserAccountState


def user_account(
    user_id: Optional[UUID] = None,
    email: Optional[str] = None,
) -> UserAccountCreate:
    user_id = user_id or uuid4()
    return UserAccountCreate(
        user_id=str(user_id),
        name=f"User {user_id}",
        email=email or f"{user_id}@re-collect.ai",
        settings={
            "default_engine": "paragraph-embedding",
            "available_engines": ["paragraph-embedding"],
        },
        status=UserAccountState.CREATED.value,
        created=datetime.now(timezone.utc),
        modified=datetime.now(timezone.utc),
    )


def recurring_import_rss(
    id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    feed_url: str = "https://example.com/feed.rss",
) -> RecurringImport:
    user_id = user_id or uuid4()
    id = id or RecurringImport.deterministic_id("rss", user_id, feed_url)
    return RecurringImport(
        id=id,
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
        source=RecurringImport.Source.RSS_FEED,
        settings=RSSImportSettings(
            url=feed_url,
            import_content_links=False,
        ).db_safe_dict(),
        context=None,
        enabled=True,
        interval=timedelta(minutes=1),
        next_run_at=datetime.now(timezone.utc),
        last_run_finished_at=None,
        last_run_status=None,
        last_run_detail=None,
    )
