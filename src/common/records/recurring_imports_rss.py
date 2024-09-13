# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from .recurring_imports import UnstructuredImportData


class RSSImportSettings(UnstructuredImportData):
    """
    RSS Feed recurring import settings.

    Attributes:
        url (str): The URL of the RSS feed.
        import_content_links (bool): Whether to import links found in content.
    """

    url: str
    import_content_links: bool


class RSSImportContext(UnstructuredImportData):
    """
    Import context for RSS Feed recurring imports.

    None of the fields that can be used to determine whether the feed has
    changed are mandatory in the spec. Synchronization/fetches should be skipped
    if any of the following are present and match the last known values:
    - HTTP response Etag header
    - HTTP response Last-Modified header
    - Feed publish/build date
    - content hash of the last N items (hash of all item titles and links)

    When pubDate or lastBuildDate are present, only entries with a pubDate
    higher than these values should be (re)processed.

    Content hash should not be computed and/or stored if better options are
    available. It is only meant to be used as a last-resort for poor feed
    implementations. To reduce potential of explosions of work for very large
    feeds, only the last N (e.g. 5) entries should be used to compute the
    content hash. When this value changes, all those N entries should be
    reprocessed. This does have the potential to miss changes if >N entries
    are produced between import runs, but that's an acceptable tradeoff
    considering a) the feed is high-volume, and b) has poor metadata.

    Attributes:
        etag (Optional[str], optional): The Etag returned in a previous fetch.
        last_modified (Optional[str], optional): The Last-Modified header
            returned in a previous fetch.
        publish_date (datetime): The feed publish date from the last fetch.
    """

    etag: Optional[str] = None
    last_modified: Optional[str] = None
    publish_date: Optional[datetime] = None
    content_hash: Optional[str] = None
