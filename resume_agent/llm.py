"""Claude calls with structured (Pydantic) output."""
from __future__ import annotations

import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel

MODEL = os.environ.get("RESUME_AGENT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("RESUME_AGENT_EFFORT", "high")

T = TypeVar("T", bound=BaseModel)
_client: anthropic.Anthropic | None = None


class Refused(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def ask(system: str, user: str, schema: type[T], effort: str = EFFORT) -> T:
    """One Claude call that must return an instance of `schema`.

    The system prompt is cached, so the master rules and chat memory are not
    billed in full on every call. A refusal on the main model falls back to a
    server-chosen model before giving up."""
    resp = client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        output_format=schema,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        why = getattr(resp.stop_details, "explanation", None) if resp.stop_details else None
        raise Refused(why or "Claude declined this request.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("The reply hit the token limit before finishing. Try a shorter job description.")
    if resp.parsed_output is None:
        raise RuntimeError("Claude returned no structured output.")
    return resp.parsed_output
