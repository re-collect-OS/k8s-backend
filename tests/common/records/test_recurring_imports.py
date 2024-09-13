# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

import pytest
from hamcrest import assert_that, contains_inanyorder, equal_to, has_length, is_
from sqlalchemy import Engine

from common.records.records import Record
from common.records.recurring_imports import (
    RecurringImport,
    RecurringImportRecords,
    UnstructuredImportData,
)

from ...test_lib.services import TestServices


@pytest.fixture
def engine(external_deps: TestServices) -> Engine:
    return external_deps.sql_db_client(truncate_all_tables=True)


@pytest.mark.integration
def test_get_by_id(engine: Engine) -> None:
    imports = RecurringImportRecords()

    class _Settings(UnstructuredImportData):
        foo: str
        baz: list[int]
        timestamp: datetime

    settings = _Settings(
        foo="bar",
        baz=[1, 2],
        timestamp=datetime.now(timezone.utc),
    )
    with engine.begin() as conn:
        created = imports.create(
            conn,
            id=Record.deterministic_id("some_id"),
            user_id=Record.deterministic_id("user_1"),
            source=RecurringImport.Source.READWISE_V3,
            settings=settings,
            interval=timedelta(minutes=1),
        )
        retrieved = imports.get_by_id(conn, id=created.id)

    assert_that(retrieved, is_(equal_to(created)))

    # Check row mapping works as expected
    expected = RecurringImport(
        id=Record.deterministic_id("some_id"),
        user_id=Record.deterministic_id("user_1"),
        source=RecurringImport.Source.READWISE_V3,
        settings=settings.model_dump(mode="json"),
        context=None,
        enabled=True,
        interval=timedelta(minutes=1),
        next_run_at=created.next_run_at,
        last_run_finished_at=None,
        last_run_status=None,
        last_run_detail=None,
        created_at=created.created_at,
    )
    assert_that(retrieved, is_(equal_to(expected)))


@pytest.mark.integration
def test_reschedule_due(engine: Engine) -> None:
    imports = RecurringImportRecords()

    now = datetime.now(timezone.utc)
    some_time_ago = now - timedelta(minutes=1)
    some_time_from_now = now + timedelta(minutes=1)

    with engine.begin() as conn:
        # Create two imports for user_1 that are overdue for a run
        imports.create(
            conn,
            id=Record.deterministic_id("overdue_1"),
            user_id=Record.deterministic_id("user_1"),
            source=RecurringImport.Source.READWISE_V3,
            settings=UnstructuredImportData(),
            interval=timedelta(minutes=1),
            first_run_at=some_time_ago,
        )
        imports.create(
            conn,
            id=Record.deterministic_id("overdue_2"),
            user_id=Record.deterministic_id("user_1"),
            source=RecurringImport.Source.READWISE_V3,
            settings=UnstructuredImportData(),
            interval=timedelta(minutes=1),
            first_run_at=some_time_ago,
        )
        # Create two imports for user_2, one overdue and one in the future
        imports.create(
            conn,
            id=Record.deterministic_id("overdue_3"),
            user_id=Record.deterministic_id("user_2"),
            source=RecurringImport.Source.READWISE_V3,
            settings=UnstructuredImportData(),
            interval=timedelta(minutes=1),
            first_run_at=some_time_ago,
        )
        imports.create(
            conn,
            id=Record.deterministic_id("not_yet_due"),
            user_id=Record.deterministic_id("user_2"),
            source=RecurringImport.Source.RSS_FEED,
            settings=UnstructuredImportData(),
            interval=timedelta(minutes=1),
            first_run_at=some_time_from_now,
        )

    with engine.begin() as conn:
        rescheduled = imports.reschedule_due(conn, instant=now)

    assert_that(rescheduled, has_length(3))
    ids = [r.id for r in rescheduled]
    assert_that(
        ids,
        contains_inanyorder(
            Record.deterministic_id("overdue_1"),
            Record.deterministic_id("overdue_2"),
            Record.deterministic_id("overdue_3"),
        ),
    )


@pytest.mark.integration
def test_set_next_run_at(engine: Engine) -> None:
    imports = RecurringImportRecords()

    now = datetime.now(timezone.utc)
    some_time_from_now = now + timedelta(minutes=1)

    with engine.begin() as conn:
        created = imports.create(
            conn,
            id=Record.deterministic_id("some_id"),
            user_id=Record.deterministic_id("user_1"),
            source=RecurringImport.Source.READWISE_V3,
            settings=UnstructuredImportData(),
            interval=timedelta(minutes=1),
        )
        updated = imports.update_next_run_at(
            conn,
            id=created.id,
            instant=some_time_from_now,
        )
        assert_that(updated, is_(True))
        reloaded = imports.get_by_id(conn, id=created.id)
        assert reloaded is not None
        assert_that(reloaded.next_run_at, is_(equal_to(some_time_from_now)))
