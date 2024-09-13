# -*- coding: utf-8 -*-
import spacy

nlp = spacy.load("en_core_web_sm")


def collapse_spaces(text: str) -> str:
    if len(text) < 1:
        return text
    prev = ""
    while prev != text:
        prev = text
        text = text.replace("  ", " ")
    return text


def text_to_sentences(text: str) -> list[str]:
    raw_sentences = [str(s).strip() for s in nlp(text).sents]
    sentences = [collapse_spaces(s) for s in raw_sentences if len(s) > 0]
    return sentences
