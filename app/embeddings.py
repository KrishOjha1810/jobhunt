"""Semantic embeddings via the Gemini API (free tier). Used for optional semantic matching.

OFF unless SEMANTIC_MATCHING=1 and a Gemini key is set. Every call is wrapped so any failure
returns None and the caller falls back to keyword matching, the embedding path can never break
the core matcher.
"""
import math
import requests
from .config import SEMANTIC_MATCHING, LLM_API_KEY, LLM_PROVIDER, EMBED_MODEL


def enabled() -> bool:
    return SEMANTIC_MATCHING and bool(LLM_API_KEY) and LLM_PROVIDER == "gemini"


def embed(text: str):
    """Return an embedding vector (list of floats) for text, or None on any issue."""
    if not enabled() or not text:
        return None
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}"
               f":embedContent?key={LLM_API_KEY}")
        r = requests.post(
            url,
            json={"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": text[:8000]}]}},
            timeout=20,
        )
        if not r.ok:
            print(f"[embeddings] error {r.status_code}: {r.text[:160]}")
            return None
        return r.json().get("embedding", {}).get("values")
    except Exception as e:
        print(f"[embeddings] error: {e}")
        return None


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
