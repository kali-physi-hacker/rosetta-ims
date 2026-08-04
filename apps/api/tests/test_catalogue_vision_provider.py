"""Which model reads a catalogue page, and what it is actually sent.

The pipeline runs on Claude by default and on Gemini when an operator says so.
Everything downstream — the envelope, observation identity, the retry policy —
is identical either way, so what needs pinning here is the wire: the right
block type for the bytes, a hard JSON guarantee, credentials that never leave
the seam, and provenance that names whoever actually read the page.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import types

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from services import catalogue_vision_provider as vision  # noqa: E402

PROMPT = "Extract only verbatim catalogue evidence."
ENVELOPE = {"page_outcome": "evidence", "tables": [], "text_observations": []}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("CATALOGUE_VISION_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    yield


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _ToolUse:
    type = "tool_use"
    name = "record_catalogue_evidence"

    def __init__(self, payload):
        self.input = payload


class _Message:
    def __init__(self, blocks, id="msg_01ABC"):
        self.content = blocks
        self.id = id


def _fake_anthropic(monkeypatch, reply=None):
    """Stand in for the SDK, capturing exactly what the seam sends."""
    if reply is None:
        reply = _Message([_ToolUse(ENVELOPE)])
    captured: dict = {}

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            if isinstance(reply, Exception):
                raise reply
            return reply

    class _Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            if isinstance(reply, Exception) and not hasattr(reply, "status_code"):
                raise reply
            return _Stream()

    class _Anthropic:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.messages = _Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_Anthropic))
    return captured


def test_anthropic_is_the_default_provider():
    assert vision.active_provider().name == "anthropic"


def test_the_env_toggle_switches_which_model_reads_the_page(monkeypatch):
    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", "google")
    assert vision.active_provider().name == "google"

    # Said the way people say it, and case/space tolerant.
    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", "  Claude ")
    assert vision.active_provider().name == "anthropic"


def test_an_unknown_provider_is_refused_rather_than_quietly_substituted(monkeypatch):
    """Falling back would label every observation with a model that never read it."""
    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", "openai")
    with pytest.raises(vision.VisionExtractionFailure) as exc:
        vision.active_provider()
    assert exc.value.code == "EXTRACTION_CONFIGURATION_ERROR"
    assert exc.value.retryable is False
    assert "anthropic or google" in exc.value.public_message


def test_each_provider_reports_configuration_from_its_own_key(monkeypatch):
    anthropic_provider, google_provider = vision.AnthropicVisionProvider(), vision.GeminiVisionProvider()
    assert anthropic_provider.is_configured() is False
    assert google_provider.is_configured() is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert anthropic_provider.is_configured() is True
    assert google_provider.is_configured() is False, "one vendor's key does not configure the other"

    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    assert google_provider.is_configured() is True


def test_a_pdf_page_is_sent_as_a_document_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = _fake_anthropic(monkeypatch)

    vision.AnthropicVisionProvider().call(b"%PDF-1.7 page", media_type="application/pdf", prompt=PROMPT)

    block, text = captured["messages"][0]["content"]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(block["source"]["data"]) == b"%PDF-1.7 page"
    assert text["text"] == PROMPT


def test_a_scan_is_sent_as_an_image_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = _fake_anthropic(monkeypatch)

    vision.AnthropicVisionProvider().call(b"\x89PNG bytes", media_type="image/png", prompt=PROMPT)

    block = captured["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_the_envelope_arrives_as_a_forced_tool_call(monkeypatch):
    """Not text that has to be parsed, and not optional.

    Prefilling the assistant turn with "{" would be the lighter trick, but
    claude-sonnet-5 rejects assistant prefill outright (400), so the structure
    has to come from the tool.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = _fake_anthropic(monkeypatch)

    response = vision.AnthropicVisionProvider().call(b"pdf", media_type="application/pdf", prompt=PROMPT)

    assert captured["tool_choice"] == {"type": "tool", "name": "record_catalogue_evidence"}
    assert [t["name"] for t in captured["tools"]] == ["record_catalogue_evidence"]
    assert [m["role"] for m in captured["messages"]] == ["user"], "no assistant prefill"
    assert json.loads(response.text)["page_outcome"] == "evidence"
    assert response.request_id == "msg_01ABC"


def test_cells_are_typed_as_strings_so_a_price_keeps_its_trailing_zero(monkeypatch):
    """$13.10 as a JSON number comes back 13.1 — a different printed value."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = _fake_anthropic(monkeypatch)
    vision.AnthropicVisionProvider().call(b"pdf", media_type="application/pdf", prompt=PROMPT)

    rows = captured["tools"][0]["input_schema"]["properties"]["tables"]["items"]["properties"]["rows"]
    assert rows["items"]["properties"]["cells"]["items"]["type"] == ["string", "null"]


def test_thinking_downgrades_the_tool_from_required_to_requested(monkeypatch):
    """The API refuses a forced tool alongside thinking, so the guarantee softens.

    That trade is the whole reason thinking is off by default; when it is on,
    a plain text envelope has to be accepted too.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_VISION_THINKING_BUDGET", "4096")
    captured = _fake_anthropic(monkeypatch, reply=_Message([
        _Block("thinking"),
        _Block("text", json.dumps(ENVELOPE)),
    ]))

    response = vision.AnthropicVisionProvider().call(b"pdf", media_type="application/pdf", prompt=PROMPT)

    assert captured["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert captured["tool_choice"] == {"type": "auto"}
    # The thinking block is not the answer.
    assert json.loads(response.text)["page_outcome"] == "evidence"


def test_an_empty_reply_is_retryable_rather_than_silently_empty_evidence(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _fake_anthropic(monkeypatch, reply=_Message([]))

    with pytest.raises(vision.VisionExtractionFailure) as exc:
        vision.AnthropicVisionProvider().call(b"pdf", media_type="application/pdf", prompt=PROMPT)
    assert exc.value.code == "MALFORMED_PROVIDER_RESPONSE"
    assert exc.value.retryable is True


def test_provider_errors_are_classified_and_never_leak_vendor_detail(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class _Overloaded(Exception):
        status_code = 529

    _fake_anthropic(monkeypatch, reply=_Overloaded("upstream said: model overloaded, key sk-ant-secret"))

    with pytest.raises(vision.VisionExtractionFailure) as exc:
        vision.AnthropicVisionProvider().call(b"pdf", media_type="application/pdf", prompt=PROMPT)
    assert exc.value.retryable is True
    assert exc.value.public_message == "Vision provider failed temporarily"
    assert "sk-ant" not in exc.value.public_message


def test_the_model_is_read_when_a_page_is_sent_so_provenance_follows_the_setting(monkeypatch):
    """A worker outlives a run; changing the model must not need a deploy."""
    assert vision.AnthropicVisionProvider().model == vision.DEFAULT_ANTHROPIC_MODEL
    monkeypatch.setenv("ANTHROPIC_VISION_MODEL", "claude-opus-5")
    assert vision.AnthropicVisionProvider().model == "claude-opus-5"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
    assert vision.GeminiVisionProvider().model == "gemini-3.1-pro-preview"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_running_out_of_credit_says_so_instead_of_blaming_the_page():
    """Measured: a 56-page run spent ten minutes retrying and reported
    "Vision provider could not extract source evidence" once per page, never
    once saying billing. Nothing about that tells an operator what to do."""
    class _NoCredit(Exception):
        status_code = 400

    failure = vision.classify_provider_failure(
        _NoCredit("Error code: 400 - {'type': 'invalid_request_error', 'message': "
                  "'Your credit balance is too low to access the Anthropic API.'}")
    )
    assert failure.code == "EXTRACTION_CONFIGURATION_ERROR"
    assert failure.retryable is False, "no retry and no code change will fix it"
    assert "credit" in failure.public_message


def test_a_genuine_bad_request_is_still_a_provider_error():
    class _BadRequest(Exception):
        status_code = 400

    failure = vision.classify_provider_failure(_BadRequest("unsupported media type"))
    assert failure.code == "PROVIDER_ERROR"
