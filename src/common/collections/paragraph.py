# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from ..text import CrossEncodeOutput
from .collections import CollectionObject
from .user_collection import UserCollection


@dataclass
class SentenceMatch:
    document_id: str
    sentence: str
    sentence_number: int
    paragraph_search_score: float
    cross_encoding_score: float

    @staticmethod
    def from_cross_encode_output(
        output: CrossEncodeOutput,
        document_id: str,
        paragraph_search_score: float,
    ) -> "SentenceMatch":
        return SentenceMatch(
            document_id=document_id,
            sentence=output.sentence,
            sentence_number=output.sentence_number,
            paragraph_search_score=paragraph_search_score,
            cross_encoding_score=output.score,
        )


@dataclass
class Filter:
    user_id: UUID
    domain: Optional[str] = None
    author: Optional[str] = None
    title: Optional[str] = None
    doc_type: list[str] | str | None = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class ParagraphV1(CollectionObject):
    # fields
    doc_id: str
    text: str
    paragraph_number: int
    sentence_numbers: list[int]
    # _additional
    certainty: Optional[float]


class ParagraphV1Collection(
    UserCollection[ParagraphV1],
    ABC,
):
    @abstractmethod
    def find_similar_sentences(
        self,
        search_text: str,
        user_id: UUID,
        limit_results: int,
        min_score: float = 0.7,
    ) -> list[SentenceMatch]:
        raise NotImplementedError()


class ParagraphV2(CollectionObject):
    # fields
    doc_id: str
    text: str
    paragraph_number: int
    sentence_numbers: list[int]
    doc_type: str
    domain: Optional[str]  # can be null in sentence_source
    title: Optional[str]  # can be null in sentence_source
    # NB: Paragraphs have no summary yet, only titles.
    # (sentence_numbers == [0] can beused to distinguish titles from paragraphs)
    summary: Optional[str]
    byline: Optional[str]  # can be null in sentence_source
    last_visited: datetime
    # _additional
    score: float

    @property
    def is_title(self) -> bool:
        return self.summary is None


class ParagraphV2Collection(
    UserCollection[ParagraphV2],
    ABC,
):
    @abstractmethod
    def find_similar_sentences(
        self,
        search_text: str,
        filter: Filter,
        limit_results: int,
        hybrid_search_factor: float = 1.0,
    ) -> list[SentenceMatch]:
        raise NotImplementedError()
