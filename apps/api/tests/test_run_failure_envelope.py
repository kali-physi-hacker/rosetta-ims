"""Failure envelope v2: one clean sentence, attempts instead of stutter,
stage + retryable riding along for the UI's plain-words layer."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from orchestration.catalogue_run_lifecycle import failure_envelope


def test_stutter_becomes_attempts():
    msg = "; ".join(["Vision provider could not extract source evidence"] * 6)
    env = failure_envelope("EXTRACTION_EVIDENCE_ERROR", msg)
    assert env["message"] == "Vision provider could not extract source evidence"
    assert env["attempts"] == 6
    assert env["stage"] == "understanding"
    assert env["retryable"] is True
    assert "detail" not in env


def test_distinct_messages_keep_detail():
    env = failure_envelope("SOURCE_PAGE_READ_ERROR", "page 3 unreadable; page 7 unreadable")
    assert env["message"] == "page 3 unreadable"
    assert env["detail"] == "page 7 unreadable"
    assert env["attempts"] == 2
    assert env["stage"] == "reading"


def test_permanent_file_problem_is_not_retryable():
    env = failure_envelope("SOURCE_VERIFICATION_ERROR", "Source PDF is password protected")
    assert env["retryable"] is False
    assert env["stage"] == "reading"
    assert env["attempts"] == 1


def test_unknown_code_gets_conservative_defaults():
    env = failure_envelope("SOMETHING_NEW", "boom")
    assert env["stage"] == "recording"
    assert env["retryable"] is False
