# -*- coding: utf-8 -*-
"""Skill-search reranker helpers."""

from __future__ import annotations

import asyncio
from typing import Protocol


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
