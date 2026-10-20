# -*- coding: utf-8 -*-
"""Skill-search reranker helpers."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Protocol

import httpx
from ...credential import DashScopeCredential


class SkillRerankerBase(Protocol):
    """Async protocol for reranking candidate skill documents."""

    async def rank(
        self,
        *,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Return one score per document, aligned with the input order."""


class CrossEncoderSkillReranker:
    """Wrap a sentence-transformers cross encoder for async reranking."""

    def __init__(
        self,
        *,
        model_name: str,
        query_prompt: str,
        default_prompt_name: str = "query",
    ) -> None:
        self._model_name = model_name
        self._query_prompt = query_prompt
        self._default_prompt_name = default_prompt_name
        self._model = None
        self._load_lock = asyncio.Lock()
        self._rank_lock = asyncio.Lock()

    def _build_model(self):
        from sentence_transformers import CrossEncoder

        return CrossEncoder(
            self._model_name,
            prompts={"query": self._query_prompt},
            default_prompt_name=self._default_prompt_name,
        )

    async def _ensure_model(self):
        if self._model is not None:
            return self._model

        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._build_model)

        return self._model

    async def rank(
        self,
        *,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Rerank candidate skill documents with sigmoid-normalized scores."""
        if not documents:
            return []

        model = await self._ensure_model()

        async with self._rank_lock:
            return await asyncio.to_thread(
                self._rank_sync,
                model,
                query,
                documents,
            )

    @staticmethod
    def _rank_sync(
        model,
        query: str,
        documents: list[str],
    ) -> list[float]:
        import torch

        rankings = model.rank(
            query,
            documents,
            activation_fn=torch.nn.Sigmoid(),
        )
        scores = [0.0] * len(documents)
        for item in rankings:
            corpus_id = int(item["corpus_id"])
            scores[corpus_id] = float(item["score"])
        return scores


class DashScopeSkillReranker:
    """Call DashScope text-rerank for async skill reranking."""

    def __init__(
        self,
        *,
        credential: DashScopeCredential,
        model: str,
        instruct: str,
        top_n: int | None = None,
    ) -> None:
        self._api_key = credential.api_key.get_secret_value()
        self._model = model
        self._instruct = instruct
        self._top_n = top_n

    async def rank(
        self,
        *,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Rerank candidate skill documents via DashScope text-rerank."""
        if not documents:
            return []

        return await asyncio.to_thread(
            self._rank_sync,
            query,
            documents,
        )

    def _rank_sync(
        self,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Call the DashScope API and map scores back to input order."""
        import dashscope

        response = dashscope.TextReRank.call(
            model=self._model,
            query=query,
            documents=documents,
            top_n=self._top_n,
            api_key=self._api_key,
            instruct=self._instruct,
        )
        if response.status_code != HTTPStatus.OK:
            raise RuntimeError(
                "DashScope text-rerank API error: "
                f"status={response.status_code}, code={response.code!r}, "
                f"message={response.message!r}, request_id={response.request_id!r}",
            )

        results = getattr(response.output, "results", None)
        if results is None:
            raise RuntimeError(
                "DashScope text-rerank API returned no results.",
            )

        scores = [0.0] * len(documents)
        for item in results:
            index = int(item.index)
            if index < 0 or index >= len(documents):
                raise RuntimeError(
                    "DashScope text-rerank API returned an out-of-range "
                    f"document index: {index}",
                )
            scores[index] = float(item.relevance_score)
        return scores


class LlamaServeSkillReranker:
    """Call one llama serve ``/v1/rerank`` endpoint for async skill reranking."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        top_n: int | None = None,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._top_n = top_n
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            trust_env=False,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client when owned by this instance."""
        if self._owns_client:
            await self._http.aclose()

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extract a readable error detail from one rerank response."""
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            for key in ("detail", "error", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        text = response.text.strip()
        if text:
            return text
        return f"llama serve rerank request failed with status {response.status_code}."

    async def rank(
        self,
        *,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Rerank candidate skill documents via llama serve ``/v1/rerank``."""
        if not documents:
            return []

        response = await self._http.post(
            "rerank",
            json={
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": self._top_n or len(documents),
            },
        )
        if response.status_code != HTTPStatus.OK:
            raise RuntimeError(
                "llama serve rerank API error: "
                f"status={response.status_code}, detail={self._error_detail(response)!r}",
            )

        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError(
                "llama serve rerank API returned no results.",
            )

        scores = [0.0] * len(documents)
        for item in results:
            if not isinstance(item, dict):
                raise RuntimeError(
                    "llama serve rerank API returned one malformed result item.",
                )
            index = item.get("index")
            if not isinstance(index, int):
                raise RuntimeError(
                    "llama serve rerank API returned one result without a valid index.",
                )
            if index < 0 or index >= len(documents):
                raise RuntimeError(
                    "llama serve rerank API returned an out-of-range "
                    f"document index: {index}",
                )
            try:
                scores[index] = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "llama serve rerank API returned one result without a valid relevance_score.",
                ) from exc
        return scores
