"""Which model reads a supplier catalogue page, and how it is asked.

The extraction service owns WHAT we ask for — the verbatim-evidence prompt and
the envelope every provider must return. This module owns the wire: building a
client, shaping the request for one vendor's API, and turning that vendor's
failures into the pipeline's retry vocabulary. Nothing else in the codebase
constructs a vision client.

Two providers are implemented and either can run the whole pipeline:

  anthropic (default) — Claude reads the page bytes directly (PDF document
      blocks, image blocks) and answers through a forced tool call, so the
      envelope arrives as structured input rather than as text that has to be
      parsed. (Prefilling "{" would be the lighter trick; claude-sonnet-5
      rejects assistant prefill outright.)
  google — Gemini, which this pipeline was built on. Kept because its
      behaviour on dense Hill's pages is measured and known.

Selected by ``CATALOGUE_VISION_PROVIDER``. The choice is recorded on every
observation (``provider``, ``model``), so evidence always says which model read
it and a mixed-provider database stays interpretable.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from pydantic import BaseModel, ConfigDict

DEFAULT_PROVIDER = "anthropic"

# Claude reads the page. Sonnet 5 is the default: current-generation vision at a
# cost that survives fanning out over every page of every catalogue. Set
# ANTHROPIC_VISION_MODEL=claude-opus-5 for maximum-accuracy runs — the
# golden/live smoke harness is the arbiter whenever the model changes.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
# Anthropic requires an explicit output ceiling and rejects one above the
# model's cap. A dense catalogue page emits ~30k tokens of verbatim cells AND
# the thinking budget spends from inside this same ceiling, so the ceiling
# must cover both; it is separate from the Gemini ceiling because the caps
# differ.
DEFAULT_ANTHROPIC_MAX_TOKENS = 64000
# Thinking ON at MEDIUM effort by default (user ruling 2026-08-17): the golden
# evidence was hand-captured in claude.ai chat with Sonnet at MEDIUM thinking,
# and production must read pages under the same conditions or local captures
# and live runs drift apart. The Claude 5 API does this as adaptive thinking
# plus output_config.effort — the same low/medium/high vocabulary the chat
# selector uses — and, probed live on claude-sonnet-5 (2026-08-17), it is
# COMPATIBLE with the forced envelope tool call, so matching the capture
# conditions costs nothing: the structure guarantee stays. Set "none" to
# disable thinking entirely.
DEFAULT_ANTHROPIC_THINKING_EFFORT = "medium"

# The envelope, as a tool the model must call. Deliberately permissive — it
# exists to make the ANSWER STRUCTURED, not to validate it; _VisionEnvelope is
# the authoritative gate and rejects anything this lets through. Cells are
# typed as strings so a price reads "13.10" and not the float 13.1.
_EVIDENCE_TOOL_NAME = "record_catalogue_evidence"
_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "cells": {
            "type": "array",
            "items": {"type": ["string", "null"]},
            "description": "Verbatim cell values aligned by position to this table's columns.",
        },
        "confidence": {"type": "string"},
        "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
    },
}
_EVIDENCE_TOOL = {
    "name": _EVIDENCE_TOOL_NAME,
    "description": "Record the verbatim catalogue evidence found on this page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "page_outcome": {"type": "string", "enum": ["evidence", "no_catalogue_evidence"]},
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "rows": {"type": "array", "items": _ROW_SCHEMA},
                    },
                    "required": ["columns", "rows"],
                },
            },
            "text_observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "confidence": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
        "required": ["page_outcome"],
    },
}

# Vision model, env-overridable. Default is COST-FIRST: gemini-flash-latest
# runs ~4.8x cheaper per output token than 3.1 Pro and matched Pro's row
# completeness on the densest measured Hill's page (36/36). Set
# GEMINI_MODEL=gemini-3.1-pro-preview for maximum-accuracy runs (paid tier).
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
# A dense catalogue page emits ~30k output tokens of verbatim cells, and
# thinking models spend more of the budget before emitting any — too small a
# ceiling truncates the JSON envelope (finish_reason MAX_TOKENS).
DEFAULT_GEMINI_MAX_TOKENS = 65536
# Gemini 3.x "thinking" models silently UNDER-EXTRACT dense catalogue tables at
# low thinking levels: measured on a real Hill's page, LOW returned 0-1 rows and
# MEDIUM 3 rows where HIGH returned the full 36-row table (finish STOP each
# time — no error, just missing products). Empty disables the override.
DEFAULT_GEMINI_THINKING_LEVEL = "HIGH"


# Every knob is read when a page is sent, not when the module loads: extraction
# runs in a worker that outlives any one run, so changing a setting takes effect
# on the next page rather than the next deploy.
def _setting(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _int_setting(name: str, default: int) -> int:
    try:
        return int(_setting(name, str(default)))
    except ValueError:
        return default


class VisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    request_id: str | None = None


class VisionExtractionFailure(Exception):
    """A provider call that failed, classified for the pipeline's retry policy."""

    def __init__(self, *, code: str, public_message: str, retryable: bool):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


class VisionProvider:
    """One vendor's vision API, behind the pipeline's single question.

    ``name`` is what lands in evidence provenance and must stay stable — it is
    written into persisted observations, so renaming one rewrites history.
    """

    name: str = ""
    model: str = ""

    def is_configured(self) -> bool:
        raise NotImplementedError

    def call(self, content: bytes, *, media_type: str, prompt: str) -> VisionResponse:
        raise NotImplementedError


class AnthropicVisionProvider(VisionProvider):
    name = "anthropic"

    @property
    def model(self) -> str:
        return _setting("ANTHROPIC_VISION_MODEL", DEFAULT_ANTHROPIC_MODEL)

    def is_configured(self) -> bool:
        return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())

    def call(self, content: bytes, *, media_type: str, prompt: str) -> VisionResponse:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=(os.environ.get("ANTHROPIC_API_KEY") or "").strip())
            source = {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(content).decode("ascii"),
            }
            # A PDF page is a document block; a scan or photo is an image block.
            block = (
                {"type": "document", "source": source}
                if media_type == "application/pdf"
                else {"type": "image", "source": source}
            )
            effort = _setting("ANTHROPIC_VISION_THINKING_EFFORT", DEFAULT_ANTHROPIC_THINKING_EFFORT).lower()
            request: dict[str, Any] = {
                "model": self.model,
                "max_tokens": _int_setting("ANTHROPIC_VISION_MAX_TOKENS", DEFAULT_ANTHROPIC_MAX_TOKENS),
                "messages": [{"role": "user", "content": [block, {"type": "text", "text": prompt}]}],
                "tools": [_EVIDENCE_TOOL],
                # Forced, so the page cannot come back as prose — and kept
                # even with thinking on: adaptive thinking + a forced tool is
                # a valid combination on the Claude 5 API (probed live).
                "tool_choice": {"type": "tool", "name": _EVIDENCE_TOOL_NAME},
            }
            if effort not in ("", "none", "off", "0"):
                request["thinking"] = {"type": "adaptive"}
                request["output_config"] = {"effort": effort}

            # Streamed, not because anything consumes the chunks, but because
            # the SDK refuses a blocking call whose max_tokens could run past
            # ten minutes — and a dense catalogue page asks for ~30k tokens.
            # The assembled message is identical either way.
            with client.messages.stream(**request) as stream:
                response = stream.get_final_message()

            text = _anthropic_envelope(response)
            if not text:
                raise VisionExtractionFailure(
                    code="MALFORMED_PROVIDER_RESPONSE",
                    public_message="Vision provider returned no evidence envelope",
                    retryable=True,
                )
            return VisionResponse(text=text, request_id=getattr(response, "id", None))
        except VisionExtractionFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - provider surface is broad
            raise classify_provider_failure(exc) from exc


def _anthropic_envelope(response: Any) -> str:
    """The envelope as JSON text, however the model chose to deliver it.

    Normally that is the forced tool call's input; a text answer is accepted
    as a belt-and-braces fallback, and thinking blocks are never the answer.
    """
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "input", None) is not None:
            return json.dumps(block.input, ensure_ascii=False)
    return _anthropic_text(response)


def _anthropic_text(response: Any) -> str:
    """The assistant's answer, ignoring any thinking blocks that precede it."""
    parts = [
        block.text
        for block in (getattr(response, "content", None) or [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "".join(parts).strip()


class GeminiVisionProvider(VisionProvider):
    name = "google"

    @property
    def model(self) -> str:
        return _setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

    def is_configured(self) -> bool:
        return bool((os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip())

    def call(self, content: bytes, *, media_type: str, prompt: str) -> VisionResponse:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
            )
            contents = [types.Part.from_bytes(data=content, mime_type=media_type), prompt]

            def _generate(cap_thinking: bool):
                # Native JSON mode (response_mime_type) forces syntactically
                # valid JSON — the observed failure mode (fences/truncation
                # garbage). Deliberately NO response_schema: google-genai 2.8
                # rejects the contract envelope's JSON schema client-side
                # (exclusiveMinimum from gt=0) and the API rejects
                # additionalProperties, so schema enforcement stays with
                # _VisionEnvelope.model_validate after the call — which is the
                # authoritative gate either way.
                config_kwargs: dict[str, Any] = {
                    "max_output_tokens": _int_setting("CATALOGUE_VISION_MAX_TOKENS", DEFAULT_GEMINI_MAX_TOKENS),
                    "response_mime_type": "application/json",
                }
                thinking_level = _setting("CATALOGUE_VISION_THINKING_LEVEL", DEFAULT_GEMINI_THINKING_LEVEL)
                if cap_thinking and thinking_level:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
                return client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs),
                )

            try:
                response = _generate(cap_thinking=True)
            except Exception as exc:  # noqa: BLE001 - provider surface is broad
                # Some models don't accept thinking_level (400 INVALID_ARGUMENT).
                # Retry once without the cap rather than failing the page.
                if _setting("CATALOGUE_VISION_THINKING_LEVEL", DEFAULT_GEMINI_THINKING_LEVEL) and _is_invalid_argument(exc):
                    response = _generate(cap_thinking=False)
                else:
                    raise
            try:
                text = response.text
            except Exception:
                text = None
            if not text or not text.strip():
                raise VisionExtractionFailure(
                    code="MALFORMED_PROVIDER_RESPONSE",
                    public_message="Vision provider returned no text response",
                    retryable=True,
                )
            return VisionResponse(text=text, request_id=getattr(response, "response_id", None))
        except VisionExtractionFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - provider surface is broad
            raise classify_provider_failure(exc) from exc


_PROVIDERS: dict[str, type[VisionProvider]] = {
    AnthropicVisionProvider.name: AnthropicVisionProvider,
    GeminiVisionProvider.name: GeminiVisionProvider,
    # Said the way people say it.
    "claude": AnthropicVisionProvider,
    "gemini": GeminiVisionProvider,
}


def active_provider() -> VisionProvider:
    """The configured vision provider.

    Read per call rather than memoized: extraction runs in a worker process
    that outlives any one run, and an operator changing the provider should
    take effect on the next run rather than the next deploy. An unknown name
    is refused loudly — silently falling back to a different model than the
    one an operator named would mislabel every observation it produced.
    """
    name = (os.environ.get("CATALOGUE_VISION_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise VisionExtractionFailure(
            code="EXTRACTION_CONFIGURATION_ERROR",
            public_message=(
                f"Unknown vision provider {name!r} — set CATALOGUE_VISION_PROVIDER to "
                "anthropic or google"
            ),
            retryable=False,
        )
    return provider()


def _is_invalid_argument(exc: Exception) -> bool:
    """True for a provider 400 INVALID_ARGUMENT (e.g. an unsupported config field)."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = f"{exc}"
    return code == 400 or text.strip().startswith("400") or "INVALID_ARGUMENT" in text


def classify_provider_failure(exc: Exception) -> VisionExtractionFailure:
    """Retry classification, in terms both vendors' SDKs answer.

    Anthropic's ``APIStatusError`` and google-genai's error both carry the HTTP
    status (``status_code`` / ``code``). 408/409/429 and 5xx are retryable —
    including Anthropic's 529 overloaded; 401/403 are configuration errors,
    never retried as transient; other 4xx are non-retryable provider errors.
    Network-level failures (timeouts, connection resets) carry no status and
    fall back to a conservative message heuristic. Raw provider details are
    never propagated to the client — messages stay sanitized.
    """

    # A provider can refuse for reasons no retry and no code change will fix.
    # Anthropic returns plain 400 invalid_request_error when the account is out
    # of credit, which read as "Vision provider could not extract source
    # evidence" — repeated once per page, 56 times, for ten minutes, without
    # once saying the word billing. An operator cannot act on that.
    text = f"{exc}".lower()
    if any(marker in text for marker in ("credit balance", "billing", "quota", "insufficient funds")):
        return VisionExtractionFailure(
            code="EXTRACTION_CONFIGURATION_ERROR",
            public_message="Vision provider rejected the request: the account has no remaining credit",
            retryable=False,
        )
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 600:
        if status in {408, 409, 429} or status >= 500:
            return VisionExtractionFailure(
                code="TRANSIENT_PROVIDER_ERROR",
                public_message="Vision provider failed temporarily",
                retryable=True,
            )
        if status in {401, 403}:
            return VisionExtractionFailure(
                code="EXTRACTION_CONFIGURATION_ERROR",
                public_message="Vision provider rejected the configured credentials",
                retryable=False,
            )
        return VisionExtractionFailure(
            code="PROVIDER_ERROR",
            public_message="Vision provider could not extract source evidence",
            retryable=False,
        )
    retryable = _looks_transient(exc)
    return VisionExtractionFailure(
        code="TRANSIENT_PROVIDER_ERROR" if retryable else "PROVIDER_ERROR",
        public_message=(
            "Vision provider failed temporarily"
            if retryable
            else "Vision provider could not extract source evidence"
        ),
        retryable=retryable,
    )


def _looks_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "rate limit",
            "overloaded",
            "temporar",
            "connection",
            "503",
            "529",
        )
    )


__all__ = [
    "VisionExtractionFailure",
    "VisionProvider",
    "VisionResponse",
    "active_provider",
    "classify_provider_failure",
]
