# -*- coding: utf-8 -*-
from typing import Any

import weaviate  # type: ignore (no stubs)

from ...text import CrossEncodeFunc, CrossEncodeInput, EmbedFunc
from ..collections import Collections
from ..paragraph import Filter, ParagraphV2, ParagraphV2Collection, SentenceMatch
from ..user_collection import Collection
from .user_collection import (
    WeaviateObjectMapper,
    WeaviateUserCollection,
    get_data_objects,
    user_id_eq,
)


def _search_filter_to_where_clause(filter: Filter) -> dict[str, Any]:
    operands = [user_id_eq(filter.user_id)]

    if filter.domain is not None:
        operands.append(
            {
                "path": ["domain"],
                "operator": "Equal",
                "valueText": filter.domain,
            }
        )

    if filter.author is not None:
        operands.append(
            {
                "path": ["byline"],
                "operator": "Equal",
                "valueText": filter.author,
            }
        )

    if filter.title is not None:
        operands.append(
            {
                "path": ["title"],
                "operator": "Equal",
                "valueText": filter.title,
            }
        )

    if filter.doc_type is not None:
        if type(filter.doc_type) == str:
            operands.append(
                {
                    "path": ["doc_type"],
                    "operator": "Equal",
                    "valueText": filter.doc_type,
                }
            )
        # multiple concurrent types
        if type(filter.doc_type) == list:
            operands.append(
                {
                    "path": ["doc_type"],
                    "operator": "ContainsAny",
                    "valueText": filter.doc_type,
                }
            )

    if filter.start_time is not None:
        operands.append(
            {
                "path": ["last_visited"],
                "operator": "GreaterThanEqual",
                "valueDate": filter.start_time.isoformat(),
            }
        )

    if filter.end_time is not None:
        operands.append(
            {
                "path": ["last_visited"],
                "operator": "LessThanEqual",
                "valueDate": filter.end_time.isoformat(),
            }
        )

    return {
        "operator": "And",
        "operands": operands,
    }


class WeaviateParagraphV2Mapper(
    WeaviateObjectMapper[ParagraphV2],
):
    def __init__(self) -> None:
        super().__init__(ParagraphV2)

    def fields(self) -> list[str]:
        return super().fields() + [
            "doc_id",
            "text",
            "paragraph_number",
            "sentence_numbers",
            "doc_type",
            "domain",
            "title",
            "summary",
            "byline",
            "last_visited",
        ]

    def additional_fields(self) -> list[str]:
        return super().additional_fields() + ["score"]


class WeaviateParagraphV2Collection(
    ParagraphV2Collection,
    WeaviateUserCollection[ParagraphV2],
):
    def __init__(
        self,
        client: weaviate.Client,
        embed_func: EmbedFunc,
        cross_encode_func: CrossEncodeFunc,
        collection: Collection = Collections.Paragraph_v20231120,
    ) -> None:
        super().__init__(client, collection, WeaviateParagraphV2Mapper())
        self._embed = embed_func
        self._xenc = cross_encode_func

    def find_similar_sentences(
        self,
        search_text: str,
        filter: Filter,
        limit_results: int,
        hybrid_search_factor: float = 1.0,
    ) -> list[SentenceMatch]:
        # Embed the search text
        query_vector = self._embed(search_text)

        # Query the vector database for similar paragraphs.
        collection_name = self.collection_class.name
        result: dict[str, Any] = (
            self._client.query.get(collection_name, self._mapper.fields())
            .with_where(_search_filter_to_where_clause(filter))
            .with_hybrid(
                query=search_text,
                vector=query_vector,
                properties=["text"],  # restrict to text only, not e.g. titles
                alpha=hybrid_search_factor,
            )
            .with_additional(self._mapper.additional_fields())
            .with_limit(limit_results)
            .do()
        )
        matches = get_data_objects(
            result,
            collection_name,
            description=lambda: f"find similar sentences for user_id={filter.user_id}",
        )

        # Pick the best matching sentence from each paragraph.
        best_matches = [self._best_match(search_text, m) for m in matches]

        return best_matches

    def _best_match(
        self,
        text: str,
        match: dict[str, Any],
    ) -> SentenceMatch:
        # No need to cross-encode if there's only one match.
        if len(match["sentence_numbers"]) == 1:
            return SentenceMatch(
                document_id=match["doc_id"],
                sentence_number=match["sentence_numbers"][0],
                sentence=match["text"],
                paragraph_search_score=match["_additional"]["score"],
                cross_encoding_score=0.0,
            )

        input = CrossEncodeInput(
            text=text,
            sentences=match["text"].split("</s><s>"),
            sentence_numbers=match["sentence_numbers"],
        )

        output = self._xenc(input)
        return SentenceMatch.from_cross_encode_output(
            output=output,
            document_id=match["doc_id"],
            paragraph_search_score=match["_additional"]["score"],
        )
