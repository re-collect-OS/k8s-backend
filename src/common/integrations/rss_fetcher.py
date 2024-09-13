# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from enum import Enum
from time import struct_time
from typing import Any, Optional

import feedparser  # type: ignore (no stubs)
from loguru import logger
from pydantic import BaseModel

_FEED_RETRIEVER_USER_AGENT = "re:collect/1.0 +https://re-collect.ai/"


class FeedEntry(BaseModel):
    title: str
    link: str
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    author: Optional[str] = None


class Feed(BaseModel):
    version: str
    encoding: str
    entries: list[FeedEntry]
    publish_date: Optional[datetime] = None
    icon_url: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None


class FeedFetchResult(Enum):
    MALFORMED = -1  # no HTTP equivalent for bad server content
    NOT_FOUND = 404
    GONE = 410
    UNAUTHORIZED = 401
    NO_NEW_CONTENT = 304
    NO_CONTENT = 204


# Catch-all for unexpected HTTP status codes.
OtherFeedFetchError = int


def fetch_feed(
    url: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> Feed | FeedFetchResult | OtherFeedFetchError:
    """
    Fetches a feed from the given URL and returns the parsed feed data.

    Primary purpose of this function is to hide away the feedparser library
    behind a typed interface.

    Args:
        url (str): The URL of the feed to fetch.
        etag (Optional[str]): The ETag value for conditional fetching.
            Defaults to None.
        last_modified (Optional[str]): The last modified header value from a
            previous fetch. Defaults to None.

    Returns:
        Union[Feed, FeedFetchResult, OtherFeedFetchError]:
            The parsed feed data or a result/error code.
    """

    # feed is of type FeedParserDict, but since this library is all untyped code
    # treating it as Any helps avoid a lot of type errors/warnings.
    result: Any = feedparser.parse(  # type: ignore
        url,
        etag=etag,
        modified=last_modified,
        agent=_FEED_RETRIEVER_USER_AGENT,
    )

    # TODO: on permanent redirects (i.e. status=301) update the context and
    # try again, up to N times. Reasoning: feedparser automatically follows
    # redirects but if a redirect points to e.g. a 410, the library will still
    # surface the original 301 as the status (which isn't great for debugging
    # failures, as everything will likely fall under 'malformed').

    status = result.status
    if status == 410:
        # Special handling of 410 to signal the caller to stop trying if this
        # feed download is part of a recurring workflow.
        return FeedFetchResult.GONE

    if status == 404:
        return FeedFetchResult.NOT_FOUND

    if status == 401 or status == 403:
        return FeedFetchResult.UNAUTHORIZED

    if status >= 400:
        logger.debug(
            "Feed retrieval for URL {url} failed with status {status}.",
            url=url,
            status=status,
        )
        return status

    # wtf... calling this 'malformed' would be too much to ask.
    # see: https://feedparser.readthedocs.io/en/latest/bozo.html
    malformed = result.bozo
    if malformed:
        return FeedFetchResult.MALFORMED

    if status == 304:
        # Either Etag or Last-Modified match.
        return FeedFetchResult.NO_NEW_CONTENT

    if "etag" in result:
        etag = result.etag

    if "modified" in result:
        last_modified = result.modified

    # Made it this far, it's a valid feed (i.e. result.feed is set)
    publish_date: Optional[datetime] = None
    if "published_parsed" in result.feed:
        publish_date = _to_utc_datetime(result.feed.published_parsed)

    icon_url: Optional[str] = None
    if "icon" in result.feed:
        icon_url = str(result.icon)
    elif "logo" in result.feed:
        icon_url = str(result.logo)
    elif "image" in result.feed and "href" in result.feed.image:
        icon_url = str(result.feed.image.href)

    entries: list[Any] = result.entries
    if not entries:
        return FeedFetchResult.NO_CONTENT

    processed_entries = [_process_entry(e) for e in entries]
    valid_entries = [e for e in processed_entries if e is not None]

    return Feed(
        version=str(result.version),
        encoding=str(result.encoding),
        entries=valid_entries,
        publish_date=publish_date,
        icon_url=icon_url,
        etag=etag,
        last_modified=last_modified,
    )


def _process_entry(entry: Any) -> Optional[FeedEntry]:
    # Mandatory fields; when not present, consider this entry invalid.
    if "title" not in entry or "link" not in entry:
        return None

    title: str = str(entry.title)
    link: str = str(entry.link)

    # Optional fields
    published_at: Optional[datetime] = None
    if "published_parsed" in entry:
        published_at = _to_utc_datetime(entry.published_parsed)
    elif "created_parsed" in entry:
        published_at = _to_utc_datetime(entry.created_parsed)

    updated_at: Optional[datetime] = None
    if "updated_parsed" in entry:
        updated_at = _to_utc_datetime(entry.updated_parsed)
    else:
        updated_at = published_at

    content: Optional[str] = None
    if "content" in entry and len(entry.content) > 0:
        type = entry.content[0].type
        value = str(entry.content[0].value)
        if type == "text/html":
            # NB(bruno): unsure of the motivation behind not handling other
            # types of content; kept from original RSS feed import code.
            content = value

    summary: Optional[str] = None
    if "summary" in entry and "summary_detail" in entry:
        type = entry.summary_detail.type
        value = str(entry.summary)
        if type == "text/html":
            # Same as above
            summary = value

    author: Optional[str] = None
    if "author" in entry:
        author = str(entry.author)

    return FeedEntry(
        title=title,
        link=link,
        published_at=published_at,
        updated_at=updated_at,
        content=content,
        summary=summary,
        author=author,
    )


def _to_utc_datetime(struct_time_utc: struct_time) -> datetime:
    # Library parses header and content timestamps and converts content to UTC
    # struct_time, taking into account local and server timezone deltas.
    # We favor datetime type; convert to a datetime object with UTC timezone.
    datetime_utc = datetime(*struct_time_utc[:6])
    datetime_utc = datetime_utc.replace(tzinfo=timezone.utc)
    return datetime_utc
