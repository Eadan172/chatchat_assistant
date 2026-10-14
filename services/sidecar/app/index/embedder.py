"""Local embedder stub.

FlagEmbedding and torch are referenced only inside ``_load_model``, which M0
startup never calls.
"""

from __future__ import annotations


def _load_model():
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device=None)


class LocalEmbedder:
    def encode_dense(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("embedder is an M0 stub")

    def encode_sparse(self, texts: list[str]) -> list[dict]:
        raise NotImplementedError("embedder is an M0 stub")
