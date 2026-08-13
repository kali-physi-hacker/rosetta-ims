"""Shared test configuration."""

from __future__ import annotations

import pytest


def pytest_unconfigure(config):  # noqa: ARG001
    """Stop Prefect logging into a stream pytest has already closed.

    Several tests need Prefect's ephemeral API, so a temporary server really does
    start. It is torn down at interpreter shutdown, and on the way out it logs
    "Stopping temporary server on http://127.0.0.1:...". That goes through a Rich
    handler still bound to pytest's capture stream, which by then is closed — so
    `logging` prints "--- Logging error ---" and a ValueError traceback AFTER the
    summary line.

    The run has already passed at that point (exit status is 0), but a traceback
    printed under a green summary is the kind of thing that gets investigated
    once per person per team. Detach the handlers while the streams are still
    open so the shutdown message has nowhere to go.
    """
    import logging

    for name in ("prefect", "prefect.server", "prefect.server.api.server"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False


@pytest.fixture(autouse=True)
def _no_vision_retry_backoff():
    """Zero the vision unit-retry backoff so retry-path tests don't sleep.

    Production backs off 20s x attempt between retryable vision failures
    (throttle windows); tests exercising those paths stub the provider and
    must stay fast. The opt-in LIVE smoke keeps the real backoff — throttle
    behaviour is part of what it verifies.
    """

    import os

    from services import catalogue_evidence_extraction as extraction

    if os.environ.get("CATALOGUE_LIVE_SMOKE"):
        yield
        return
    previous = extraction._VISION_RETRY_BACKOFF_SECONDS
    extraction._VISION_RETRY_BACKOFF_SECONDS = 0.0
    yield
    extraction._VISION_RETRY_BACKOFF_SECONDS = previous
