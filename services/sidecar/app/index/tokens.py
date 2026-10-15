"""Token helpers shared by chunking, hashing embeddings, and lexical rank.

No third-party tokenizer is imported. CJK characters are kept as unigrams and
adjacent pairs so Chinese overlap survives a whitespace tokenizer.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def token_count(text: str) -> int:
    """Count words and CJK characters. Bigrams are not included."""
    if not text or not text.strip():
        return 0
    return len(_WORD.findall(text))


def tokens(text: str) -> list[str]:
    """Unigrams / words plus CJK bigrams, lowercased, in appearance order."""
    if not text:
        return []
    parts = [match.group(0).lower() for match in _WORD.finditer(text)]
    cjk = [part for part in parts if len(part) == 1 and "\u4e00" <= part <= "\u9fff"]
    bigrams = [cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)]
    return parts + bigrams


def trigram_query(text: str, *, limit: int = 24) -> str:
    """OR-query of 3-character windows for the FTS5 trigram tokenizer."""
    compact = re.sub(r"\s+", "", text or "")
    grams: list[str] = []
    for index in range(max(0, len(compact) - 2)):
        gram = compact[index : index + 3]
        if gram not in grams:
            grams.append(gram)
        if len(grams) >= limit:
            break
    if not grams:
        return ""
    return " OR ".join('"' + gram.replace('"', '""') + '"' for gram in grams)
