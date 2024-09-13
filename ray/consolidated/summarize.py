# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
from typing import Any

import aiohttp
import requests
from fastapi import FastAPI
from pydantic import BaseModel

from ray import serve


async def process(url: str, headers: dict, data: dict = None):
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            return await response.read()


class PostPayload(BaseModel):
    text: str
    model: str
    prompt: str | None = None
    system_prompt: str | None = None


class PostResponse(BaseModel):
    summary: str


fastapi_app = FastAPI()


@serve.deployment()
@serve.ingress(fastapi_app)
class Summarizer:
    def __init__(self):
        self._temperature = 0.0
        self._max_parallel_requests = 5
        match os.getenv("LLM_PROVIDER"):
            case "anyscale":
                self._url = os.getenv("ANYSCALE_ENDPOINTS_CLOUD_URL")
                self._headers = {
                    "Authorization": f"Bearer {os.getenv('ANYSCALE_ENDPOINTS_API_KEY')}",
                }
            case "fireworks":
                self._url = os.getenv("FIREWORKS_ENDPOINTS_CLOUD_URL")
                self._headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.getenv('FIREWORKS_ENDPOINTS_API_KEY')}",
                }
            case _:
                self._url = os.getenv("ANYSCALE_ENDPOINTS_CLOUD_URL")
                self._headers = {
                    "Authorization": f"Bearer {os.getenv('ANYSCALE_ENDPOINTS_API_KEY')}",
                }

    @fastapi_app.post("/", response_model=PostResponse)
    async def summarize(self, payload: PostPayload):
        """Summarize text using LLM endpoint service
        # Anyscale Endpoints
        Supported models: https://docs.endpoints.anyscale.com/category/supported-models

        # Fireworks.ai Endpoints
        Supported models: https://fireworks.ai/models
        """
        payload = json.loads((payload.model_dump_json()))
        return await self.handle_batch(payload)

    # TODO anyscale match concurrent requests is 5
    @serve.batch(max_batch_size=5, batch_wait_timeout_s=0.1)
    async def handle_batch(self, payloads: list[dict[str, Any]]) -> list[str]:
        batch = []
        for payload in payloads:
            system_prompt = payload["system_prompt"] or "You are a helpful assistant."
            prompt = payload["prompt"] or "Summarize the following: \n{text} Summary:"
            text = re.sub(r"\s+", " ", payload["text"])
            user_prompt = prompt.format(text=text)
            data = {
                "model": payload["model"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self._temperature,
            }
            batch.append(data)

        tasks = [process(self._url, self._headers, elem) for elem in batch]
        responses = await asyncio.gather(*tasks)

        results = []
        for response in responses:
            try:
                response = json.loads(response.decode())
                summary = response["choices"][0]["message"]["content"]
                results.append({"summary": summary.strip()})
            except Exception as e:
                results.append({"summary": str(e)})

        return results


app = Summarizer.options().bind()
