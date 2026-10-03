"""
Shared access to the language model.

This module is transport only: it knows how to reach Groq, how long to wait and how to
fail. It holds no domain knowledge, which is what keeps the two agents that use it
(planner, writer) independent of each other and of the data layer.

The governing principle for the whole pipeline: THE MODEL CHOOSES, THE CODE COMPUTES.
No number a user ever sees is produced by the model.
"""

from __future__ import annotations

import os

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
# A second model to name in the README and fall back to by hand if the first is retired.
FALLBACK_MODEL = os.environ.get("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")

TIMEOUT_S = 30
_client = None


def _groq():
    """Create the client once, and only if a key is configured."""
    global _client
    if _client is None and os.environ.get("GROQ_API_KEY"):
        from groq import Groq
        # max_retries=0 is deliberate: on a free-tier 429 we want to drop to the
        # deterministic path immediately rather than make the user wait out a retry.
        _client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=TIMEOUT_S, max_retries=0)
    return _client


def available() -> bool:
    return _groq() is not None


def error_reason(exc: Exception) -> str:
    """A short, honest label for why the model was not used - surfaced, not swallowed."""
    text = str(exc).lower()
    if "429" in text or "rate" in text:
        return "rate limited"
    if "401" in text or "403" in text or "api key" in text:
        return "no valid API key"
    if "timeout" in text or "timed out" in text:
        return f"timed out after {TIMEOUT_S}s"
    if "model" in text and ("not found" in text or "decommission" in text):
        return "model unavailable"
    return type(exc).__name__


def chat(system: str, user: str, *, as_json: bool = False, temperature: float = 0.0) -> str:
    """
    One completion. Raises on failure; every caller is expected to have a path that
    works without the model at all.
    """
    client = _groq()
    if client is None:
        raise RuntimeError("no GROQ_API_KEY configured")
    kwargs: dict = {"model": MODEL, "temperature": temperature,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}]}
    if as_json:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()
