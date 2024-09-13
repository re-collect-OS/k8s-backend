# -*- coding: utf-8 -*-
from fastapi import FastAPI

# Mocks for ray HTTP services. For local development only.

app = FastAPI(title="ray services mock")


@app.post("/engine-paragraph/embed")
def embed():
    return [
        {
            "paragraph_number": 0,
            "sentence_numbers": [0],
            "text": "title",
            "vector": [1, 1, 1, 1],
        }
    ]


@app.post("/engine-paragraph/cross-encoder")
def cross_encode():
    raise NotImplementedError()
