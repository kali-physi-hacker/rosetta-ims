# Extraction-attempt history for Stage 3

Status: follow-up, not urgent

## Current design (Option B — volatile-metadata exclusion)

Extracted-evidence replay safety is implemented by excluding an explicit
allowlist of per-attempt operational keys from material equality
(`_VOLATILE_ATTEMPT_METADATA_KEYS` + `extraction_confidence` + `captured_at`
in `services/catalogue_pipeline_stages.py::_raw_material`):

- identical evidence with a new `provider_request_id` / trace id / attempt
  timestamp / warnings / confidence **reuses** the immutable first
  observation; nothing is overwritten;
- the first-persisted observation retains its original request id and
  confidence for audit;
- material changes (raw text/cells, source location, bounding box,
  extraction method, provider/model identity, stable source metadata)
  still produce a controlled `IdempotencyConflict` (same identity) or a
  distinct observation (different content+location digest).

## The debt

Attempt-level provenance is currently only the first attempt's values frozen
onto the observation. Later attempts' request ids, timings, and confidence
values are discarded (visible only in logs/metrics).

## When picking this up (Option A)

Add an append-only extraction-attempt record (mirroring
`CatalogueRawStageAttempt` for the raw stage) carrying: ingestion run ID,
source file ID, unit/page key, provider, model, provider request ID, attempt
number, started/completed timestamps, status, sanitized error code. Extracted
observations then reference their first-producing attempt, and `_raw_material`
can stop special-casing volatile keys because attempt data no longer lives in
`source_metadata`.
