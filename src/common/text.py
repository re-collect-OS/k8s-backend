# -*- coding: utf-8 -*-
import re
from dataclasses import dataclass
from typing import Callable

import requests


def snake_case(text: str) -> str:
    text = text.replace("-", " ")  # hyphens to spaces (handle kebab case)
    text = re.sub(r"[^\w\s]", "", text)  # remove non-words (except space)
    text = re.sub(r"\s+", "_", text)  # space to _
    return text.lower()


@dataclass
class CrossEncodeInput:
    text: str
    sentences: list[str]
    sentence_numbers: list[int]


@dataclass
class CrossEncodeOutput:
    sentence: str
    sentence_number: int
    score: float


def _not_implemented():
    raise NotImplementedError()


# Text embedding function signature.
EmbedFunc = Callable[[str], list[float]]

# A no-op embedding function, to satisfy dependencies in situations where no
# embedding actually takes place.
NotImplementedEmbedFunc: EmbedFunc = lambda _: _not_implemented()

# Cross-encoding function signature.
CrossEncodeFunc = Callable[[CrossEncodeInput], CrossEncodeOutput]

# A no-op cross-encoding function, to satisfy dependencies in situations where
# no cross-encoding actually takes place.
NotImplementedCrossEncodeFunc: CrossEncodeFunc = lambda _: _not_implemented()


class TextServices:
    """
    Class that implements EmbedFunc and CrossEncodeFunc backed by remote
    HTTP services.
    """

    def __init__(
        self,
        embedding_service_url: str,
        cross_encoding_service_url: str,
    ) -> None:
        self._embed_svc_url = embedding_service_url
        self._xenc_svc_url = cross_encoding_service_url

    def embed(self, text: str) -> list[float]:
        payload_embed = {
            "document": [
                {
                    "text": text,
                    # Required by service but not relevant for text embedding.
                    # (Service supports embedding a string as well as documents,
                    # but these arguments are only relevant for documents.)
                    "paragraph_number": 1,
                    "sentence_numbers": [1],
                }
            ]
        }
        response = requests.post(self._embed_svc_url, json=payload_embed)

        # Guaranteed to always return content
        query_vector: list[float] = response.json()[0]["vector"]

        return query_vector

    def cross_encode(self, input: CrossEncodeInput) -> CrossEncodeOutput:
        """
        Given a query (text) and a list of sentences, return the best matching
        sentence.

        See: https://www.sbert.net/examples/applications/cross-encoder/README.html
        """
        payload_xenc = {
            "query": input.text,
            "sentences": input.sentences,
            "sentence_numbers": input.sentence_numbers,
        }

        response = requests.post(self._xenc_svc_url, json=payload_xenc).json()

        return CrossEncodeOutput(
            sentence=str(response["sentence"]),
            sentence_number=int(response["sentence_number"]),
            score=float(response["score"]),
        )
