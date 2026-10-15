"""Embeddings: OpenAI-compatible HTTP, optional local BGE, or a hashing fallback.

FlagEmbedding and torch are imported only when EMBED_PROVIDER=bge. The default
path is a deterministic hashing embedder so tests and the desktop demo run
without API keys or model weights.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import struct
from typing import Sequence

from app.index.tokens import tokens
from app.llm.endpoint import Endpoint, embed_endpoint

logger = logging.getLogger(__name__)

EMBED_DIM = 1024


def _load_model():
    """Lazy BGE loader. Not called during process startup or the default path."""
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device=None)


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Feature-hash tokens into a unit vector. The same text always matches."""
    vec = [0.0] * dim
    found = tokens(text)
    if not found:
        vec[0] = 1.0
        return vec
    for token in found:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vec))
    if norm == 0.0:
        vec[0] = 1.0
        return vec
    return [value / norm for value in vec]


def pack_f32(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_f32(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob[: count * 4]))


def fit_dim(vector: Sequence[float], dim: int = EMBED_DIM) -> list[float]:
    values = [float(item) for item in vector]
    if len(values) == dim:
        return values
    if len(values) > dim:
        values = values[:dim]
    else:
        values = values + [0.0] * (dim - len(values))
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        values[0] = 1.0
        return values
    return [value / norm for value in values]


class LocalEmbedder:
    """Dense encoder. ``encode_sparse`` stays unavailable until a sparse model is wired."""

    def __init__(self, registry=None) -> None:
        self._registry = registry

    def encode_dense(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        provider = (os.getenv("EMBED_PROVIDER") or "auto").strip().lower()
        if provider == "bge":
            try:
                return self._bge(texts)
            except Exception:
                logger.exception("bge embedder failed; using the hashing embedder")
        elif provider != "hash":
            endpoint = embed_endpoint(self._registry)
            if endpoint is not None:
                try:
                    return _openai_embed(endpoint, texts)
                except Exception:
                    logger.exception("remote embedder failed; using the hashing embedder")
        return [hash_embed(text) for text in texts]

    def encode_sparse(self, texts: list[str]) -> list[dict]:
        raise NotImplementedError("sparse embeddings are not part of the M3 offline path")

    def _bge(self, texts: list[str]) -> list[list[float]]:
        model = _load_model()
        output = model.encode(texts, batch_size=8, max_length=1024)
        dense = output["dense_vecs"]
        return [fit_dim(row) for row in dense]


def embed_texts(texts: list[str], registry=None) -> list[list[float]]:
    return LocalEmbedder(registry).encode_dense(texts)


def _openai_embed(endpoint: Endpoint, texts: list[str]) -> list[list[float]]:
    import httpx

    url = endpoint.join("embeddings")
    headers = {"Authorization": f"Bearer {endpoint.api_key}"}
    body: dict = {"model": endpoint.model, "input": texts, "dimensions": EMBED_DIM}
    with httpx.Client(timeout=endpoint.timeout_s) as client:
        response = client.post(url, json=body, headers=headers)
        if response.status_code == 400 and "dimensions" in body:
            body.pop("dimensions")
            response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
    rows = sorted(payload.get("data") or [], key=lambda item: item.get("index", 0))
    vectors = [fit_dim(row.get("embedding") or []) for row in rows]
    if len(vectors) != len(texts):
        raise RuntimeError("embedding response did not match the input batch")
    return vectors
