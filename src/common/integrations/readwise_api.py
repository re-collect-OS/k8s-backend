# -*- coding: utf-8 -*-
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Iterable, Iterator, Optional

import requests
from loguru import logger
from pydantic import AfterValidator, BaseModel, Field, ValidationError

_V2_ENDPOINT = "https://readwise.io/api/v2/export/"
_V3_ENDPOINT = "https://readwise.io/api/v3/list/"
_REQUEST_TIMEOUT_SECS = 10


# Optional[str] that turns "" into None
# Utility that helps deal with Readwise API inconsistencies where fields are
# arbitrarily null or "".
OptionalString = Annotated[
    Optional[str],
    AfterValidator(lambda x: None if x == "" else x),
]


# --- Highlights export (v2) API (https://readwise.io/api_deets#export)

# NB(bruno, 2024-01-29): I've left commented-out fields from the source data
# we're not currently using for reference. They can (should?) be deleted if
# this integration's code remains unchanged for a couple months.


# class Tag(BaseModel):
#     """A tag for a highlight."""
#
#     id: int
#     name: str


class BookHighlight(BaseModel):
    id: int
    text: str
    location: Optional[int]
    location_type: str
    note: OptionalString
    # color: str
    # highlighted_at: datetime
    created_at: datetime
    updated_at: datetime
    # external_id: OptionalString
    # end_location: OptionalString
    url: OptionalString
    book_id: int  # same as parent user_book_id
    # tags: list[Tag]
    # is_favorite: bool
    # is_discard: bool
    readwise_url: str


class Book(BaseModel):
    class Category(Enum):
        BOOKS = "books"
        ARTICLES = "articles"
        TWEETS = "tweets"
        SUPPLEMENTALS = "supplementals"
        PODCASTS = "podcasts"

    user_book_id: int
    title: str
    readable_title: OptionalString
    author: OptionalString
    source: str
    # cover_image_url: str
    # unique_url: OptionalString
    # book_tags: list[str]
    category: Category
    document_note: OptionalString
    readwise_url: str
    source_url: OptionalString
    asin: OptionalString
    highlights: list[BookHighlight]


@dataclass
class V2Result:
    books: list[Book]


def get_v2_data(
    token: str,
    since: Optional[datetime] = None,
    book_ids: Optional[list[int]] = None,
    page_limit: Optional[int] = None,
    log_validation_errors: bool = False,
) -> V2Result:
    """
    Result of a v2 API call to export highlights.
    """

    books: list[Book] = []
    params = {}
    if since:
        params["updatedAfter"] = since.isoformat()
    if book_ids:
        params["ids"] = ",".join(map(str, book_ids))
    for result in _get(
        endpoint=_V2_ENDPOINT,
        token=token,
        params=params,
        page_limit=page_limit,
    ):
        try:
            book = Book.model_validate(result)
        except ValidationError as e:
            if log_validation_errors:
                logger.warning(f"Error validating book: {e}\n{result}")

        book = Book.model_validate(result)
        books.append(book)

    return V2Result(books)


# --- Reader API (https://readwise.io/reader_api#list)


# class Location(Enum):
#     NEW = "new"
#     LATER = "later"
#     SHORTLIST = "shortlist"
#     ARCHIVE = "archive"
#     FEED = "feed"


class Highlight(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    # readwise_url: str = Field(alias="url")
    content: str
    # Highlights always have parent_id, as they reference another document.
    document_id: str = Field(alias="parent_id")


class Note(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    title: OptionalString
    content: str
    # readwise_url: str = Field(serialization_alias="url")
    # Notes always have a parent_id, as they reference another document
    highlight_id: str = Field(alias="parent_id")


class Document(BaseModel):
    class Category(Enum):
        ARTICLE = "article"
        EMAIL = "email"
        RSS = "rss"
        PDF = "pdf"
        EPUB = "epub"
        TWEET = "tweet"
        VIDEO = "video"
        # NB: "note" and "highlight" omitted from this list, as they're treated
        # as different, first-class entities.

    id: str
    created_at: datetime
    updated_at: datetime
    # readwise_url: str = Field(alias="url")
    source_url: str
    category: Category
    notes: OptionalString
    title: OptionalString
    # author: OptionalString
    # source: OptionalString
    # location: Location
    # site_name: str
    # word_count: Optional[int]
    # published_date: Optional[int]
    # summary: OptionalString
    # image_url: str
    # parent_id: OptionalString
    # reading_progress: float

    def fix_data(self) -> None:
        # NB: It was observed in production that some v3 documents' source_url
        # had no scheme; naively add "https://". This may require a bit more
        # robust handling if there are more convoluted cases of invalid URLs.
        if not self.source_url.startswith("http"):
            self.source_url = f"https://{self.source_url}"


@dataclass
class V3Result:
    """
    Result of a v3 (Reader) API call.

    Attributes:
        documents (dict[str, Document]): A dictionary of documents, keyed by ID.
        highlights (dict[str, Highlight]): A dictionary of highlights, keyed by
            ID.
        notes (dict[str, Note]): A dictionary of notes, keyed by ID.
    """

    documents: list[Document]
    highlights: list[Highlight]
    notes: list[Note]

    @property
    def item_count(self) -> int:
        return len(self.documents) + len(self.highlights) + len(self.notes)

    def is_empty(self) -> bool:
        return self.item_count == 0

    def resolve_references(self) -> list["ResolvedDocument"]:
        return resolve_references(
            documents=self.documents,
            highlights=self.highlights,
            notes=self.notes,
        )


def get_v3_data(
    token: str,
    since: Optional[datetime] = None,
    page_limit: Optional[int] = None,
    log_validation_errors: bool = False,
) -> V3Result:
    """
    Fetch documents from Readwise Reader (v3) API.

    Raises `requests.HTTPException` if the API call fails.

    Args:
        token (str): The Readwise API token.
        since (Optional[datetime], optional): Only fetch documents updated since
            this datetime. Defaults to None.
        limit (Optional[int], optional): Maximum number of documents to fetch.
            Defaults to None.
        log_validation_errors (bool, optional): Whether to log validation
            errors. Defaults to False.
    """

    docs: list[Document] = []
    highlights: list[Highlight] = []
    notes: list[Note] = []
    params = {}
    if since:
        params["updatedAfter"] = since.isoformat()
    for result in _get(
        endpoint=_V3_ENDPOINT,
        token=token,
        params=params,
        page_limit=page_limit,
    ):
        try:
            # Highlights and notes have a slightly different format:
            # - they always have a parent_id (i.e. ref to another doc)
            # - they never have a source_url (since they reference another doc)
            # - always have content (note text or highlighted text)
            if result["category"] == "highlight":
                highlight = Highlight.model_validate(result)
                highlights.append(highlight)
            elif result["category"] == "note":
                note = Note.model_validate(result)
                notes.append(note)
            else:
                doc = Document.model_validate(result)
                doc.fix_data()
                docs.append(doc)
        except ValidationError as e:
            if log_validation_errors:
                logger.warning(
                    f"Error validating Readwise V3 response entry: {e}\n{result}"
                )

    return V3Result(
        documents=docs,
        highlights=highlights,
        notes=notes,
    )


class ResolvedHighlight(Highlight):
    parent_document: Document
    note: Optional[Note]


class ResolvedDocument(Document):
    highlights: list[ResolvedHighlight]


def resolve_references(
    documents: Iterable[Document],
    highlights: Iterable[Highlight],
    notes: Iterable[Note],
) -> list[ResolvedDocument]:
    """
    Utility that converts disjoint collections of documents, highlights, and
    notes into a single list of documents, each containing its associated
    highlights, and each highlight containing its associated note (if any).

    Unresolvable highlights and notes (i.e. where parent cannot be found in
    supplied data) are dropped.
    """

    resolved_docs: dict[str, ResolvedDocument] = {}
    for doc in documents:
        resolved_docs[doc.id] = ResolvedDocument.model_construct(
            **doc.model_dump(),
            highlights=[],
        )

    resolved_highlights: dict[str, ResolvedHighlight] = {}
    for highlight in highlights:
        parent_doc = resolved_docs.get(highlight.document_id)
        if parent_doc is None:
            logger.trace(
                "Dropping highlight with id {id}: parent document not found",
                id=highlight.id,
            )
            continue

        resolved_highlight = ResolvedHighlight.model_construct(
            **highlight.model_dump(),
            parent_document=parent_doc,
            note=None,
        )
        resolved_highlights[highlight.id] = resolved_highlight
        parent_doc.highlights.append(resolved_highlight)

    for note in notes:
        parent_highlight = resolved_highlights.get(note.highlight_id)
        if parent_highlight is None:
            logger.trace(
                "Dropping note with id {id}: parent highlight not found",
                id=note.id,
            )
            continue

        parent_highlight.note = note

    return list(resolved_docs.values())


# --- Common fetch logic to v2 and v3 APIs


def _get(
    endpoint: str,
    token: str,
    params: dict[str, str] = {},
    page_limit: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    next_page: Optional[str] = None

    page = 0

    # NB: API returns data in descending order of updated_at (i.e. most-recent
    # first). Supplying a page_limit means older results will never be retrieved
    # if all subsequent calls use the highest updated_at from the previous call.
    while True:
        if next_page:
            params["pageCursor"] = next_page

        response = requests.get(
            timeout=_REQUEST_TIMEOUT_SECS,
            url=endpoint,
            params=params,
            headers={"Authorization": f"Token {token}"},
            verify=True,
        )
        response.raise_for_status()

        json_response = response.json()
        results = json_response["results"]
        next_page = json_response.get("nextPageCursor")
        for result in results:
            yield result

        if next_page is None:
            break

        page += 1
        if page_limit is not None and page >= page_limit:
            break
