"""Thin wrapper around the Claude API for structured (Pydantic) outputs."""
from __future__ import annotations

import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict):
        self.model = cfg.get("model", "claude-opus-5-5")
        self.effort = cfg.get("effort", "medium")
        self.client = anthropic.Anthropic()

    def parse(self, system: str, prompt: str, schema: type[T], effort: str | None = None) -> T:
        try:
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort or self.effort},
                # If a request is declined, the API retries it on a suitable fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise LLMError(f"rate limited, try again later: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"could not reach the Claude API: {e}") from e

        if resp.stop_reason == "refusal":
            raise LLMError("request was declined by the model")
        if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            raise LLMError(f"incomplete structured output (stop_reason={resp.stop_reason})")
        return resp.parsed_output
