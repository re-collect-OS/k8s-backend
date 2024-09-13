# -*- coding: utf-8 -*-
import re
from typing import Annotated, Tuple
from urllib.parse import ParseResult, parse_qs, urlparse, urlunparse

from pydantic import AnyHttpUrl, BeforeValidator, HttpUrl, TypeAdapter

# These custom URL types are a workaround for the fact that pydantic V2 Urls no
# longer sublass str, which would break most of of the existing codebase that's
# built on that assumption (e.g. passing models with *Url fields to pgsql, as
# JSON payloads, etc.)
#
# It's a low-effort workaround to minimize changes to code logic chosen based on
# the fact that there is, at time of writing, no test coverage. This should
# eventually be dropped in favor of properly handling the new pydantic Url type.

HttpUrlString = Annotated[
    str,
    BeforeValidator(lambda value: str(TypeAdapter(HttpUrl).validate_python(value))),
]

AnyHttpUrlString = Annotated[
    str,
    BeforeValidator(lambda value: str(TypeAdapter(AnyHttpUrl).validate_python(value))),
]


TWEET_URL_PATTERN = r"^https?://(?:www\.)?(twitter\.com|x\.com)/[^/?#]+/status/\d+"
IDEA_NOTE_CARD_URL_PATTERN = r"^https://app\.re-collect\.ai/idea/[^/?#]+#card=."
ANNOTATION_NOTE_CARD_URL_PATTERN = (
    r"^https://app\.re-collect\.ai/artifact\?url=.+#card=."
)
DAILY_LOG_NOTE_CARD_URL_PATTERN = (
    r"^https://app\.re-collect\.ai/daily-log\?day=.+#card=."
)
# https://webapps.stackexchange.com/questions/54443/format-for-id-of-youtube-video
YOUTUBE_URL_PATTERN = (
    r"^https?://w{0,3}\.?(?:youtube\.com/watch\?v=|youtu\.be/)([\d\w\-_]{11})"
)
SPARSE_DOCUMENT_URL_PATTERN = r"^https://app\.re-collect\.ai/sparse-document/."

APPLE_NOTES_URL_PATTERN = r"^https://app\.re-collect\.ai/apple-note/.+/ICNote/."


def is_tweet_url(url: str) -> bool:
    return re.match(TWEET_URL_PATTERN, url) != None


def is_pdf_url(url: str) -> bool:
    return (
        url.lower().endswith(".pdf")
        or "arxiv.org/pdf" in url
        or "pdf.sciencedirectassets.com" in url
    )


def is_youtube_url(url: str) -> bool:
    return re.match(YOUTUBE_URL_PATTERN, url) != None


def get_youtube_hash(url: str) -> str:
    rematch = re.match(YOUTUBE_URL_PATTERN, url)
    return rematch.group(1) if rematch is not None else ""


def is_mp3_url(url: str) -> bool:
    return url.lower().endswith(".mp3")


def is_idea_note_card_url(url: str) -> bool:
    return re.match(IDEA_NOTE_CARD_URL_PATTERN, url) != None


def is_annotation_note_card_url(url: str) -> bool:
    return re.match(ANNOTATION_NOTE_CARD_URL_PATTERN, url) != None


def is_daily_log_note_card_url(url: str) -> bool:
    return re.match(DAILY_LOG_NOTE_CARD_URL_PATTERN, url) != None


def is_note_card_url(url: str) -> bool:
    return (
        is_idea_note_card_url(url)
        or is_annotation_note_card_url(url)
        or is_daily_log_note_card_url(url)
    )


def is_sparse_document_url(url: str) -> bool:
    return re.match(SPARSE_DOCUMENT_URL_PATTERN, url) != None


def apple_note_path_to_url(path: str) -> str:
    pattern = re.compile(
        r"x-coredata://([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})/ICNote/(\w+)"
    )
    match = pattern.match(path)
    persistent_id, note_id = match.groups() if match else (None, None)
    return f"https://app.re-collect.ai/apple-note/{persistent_id}/ICNote/{note_id}"


def is_apple_note_url(url: str) -> bool:
    return re.match(APPLE_NOTES_URL_PATTERN, url) != None


def normalize_google_scholar_url(parsed: ParseResult) -> str | None:
    # return normalized URL or None if can't normalize
    query = parse_qs(parsed.query)
    if "case" not in query:
        return None
    return urlunparse(
        ParseResult(
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            f"case={query['case'][0]}",
            "",
        )
    )


url_normalizer_map = {"scholar.google.com": normalize_google_scholar_url}


def normalize_url(url: str) -> str:
    """
    Pre-process a URL by putting it in our "normal" form.
    For most URLs this means removing query parameters and fragments.
    """
    # Youtube urls are special, we want to keep the '?v=***'
    if is_youtube_url(url):
        return re.match(YOUTUBE_URL_PATTERN, url).group(0)
    url = url.strip()
    parsed = urlparse(url)
    if parsed.hostname:
        fn = url_normalizer_map.get(parsed.hostname)
        if fn:
            normalized = fn(parsed)
            if normalized:
                return normalized

    url = url.split("?")[0]
    url = url.split("#")[0]
    url = url.rstrip("/")
    return url
