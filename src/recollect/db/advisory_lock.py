# -*- coding: utf-8 -*-
import hashlib
from typing import Any

from sqlalchemy import func, sql
from sqlalchemy.orm import Session


class AdvisoryLockNotAvailable(Exception):
    def __init__(self, key, message="key already used for lock"):
        self.key = key
        self.message = message
        super().__init__(self.message)


# https://leontrolski.github.io/postgres-advisory-locks.html
def acquire_advisory_lock(session: Session, *ids: Any) -> None:
    # make an integer key
    key_str = "-".join([str(id_) for id_ in ids])
    key_bytes: bytes = key_str.encode("utf-8")
    m = hashlib.sha256()
    m.update(key_bytes)
    # pg_try_advisory_xact_lock is limited to an 8-byte signed integer
    key = int.from_bytes(m.digest()[:8], byteorder="big", signed=True)

    # get a lock on the db with the key
    rows = session.execute(sql.select(func.pg_try_advisory_xact_lock(key)))
    locked = not next(rows)[0]

    # if it is already locked by another transaction, raise an error
    if locked:
        raise AdvisoryLockNotAvailable(key)
