# -*- coding: utf-8 -*-
from typing import Any
from uuid import UUID

import weaviate  # type: ignore (no stubs)

from ...text import CrossEncodeFunc, CrossEncodeInput, EmbedFunc
from ..collections import Collections
from ..paragraph import ParagraphV1, ParagraphV1Collection, SentenceMatch
from ..user_collection import Collection
from .user_collection import (
    WeaviateObjectMapper,
    WeaviateUserCollection,
    get_data_objects,
    user_id_eq,
)


class WeaviateParagraphV1Mapper(
    WeaviateObjectMapper[ParagraphV1],
):
    def __init__(self) -> None:
        super().__init__(ParagraphV1)

    def fields(self) -> list[str]:
        return super().fields() + [
            "doc_id",
            "text",
            "paragraph_number",
            "sentence_numbers",
        ]

    def additional_fields(self) -> list[str]:
        return super().additional_fields() + ["certainty"]


class WeaviateParagraphV1Collection(
    ParagraphV1Collection,
    WeaviateUserCollection[ParagraphV1],
):
    def __init__(
        self,
        client: weaviate.Client,
        # TODO(bruno): embed + xenc should be separate from collection.
        # This is a use-case for composition.
        embed_func: EmbedFunc,
        cross_encode_func: CrossEncodeFunc,
        collection: Collection = Collections.Paragraph_v20230517,
    ) -> None:
        super().__init__(client, collection, WeaviateParagraphV1Mapper())
        self._embed = embed_func
        self._xenc = cross_encode_func

    def find_similar_sentences(
        self,
        search_text: str,
        user_id: UUID,
        limit_results: int,
        min_score: float = 0.7,
    ) -> list[SentenceMatch]:
        # Embed the search text
        class_name = self.collection_class.name
        query_vector = self._embed(search_text)

        # Query the vector database for similar paragraphs
        result: dict[str, Any] = (
            self._client.query.get(class_name, self._mapper.fields())
            .with_where(user_id_eq(user_id))
            .with_near_vector({"vector": query_vector, "certainty": min_score})
            .with_additional(self._mapper.additional_fields())
            .with_limit(limit_results)
            .do()
        )
        matches = get_data_objects(
            result,
            class_name,
            description=lambda: f"find similar sentences for user_id={user_id}",
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
                paragraph_search_score=match["_additional"]["certainty"],
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
            paragraph_search_score=match["_additional"]["certainty"],
        )
