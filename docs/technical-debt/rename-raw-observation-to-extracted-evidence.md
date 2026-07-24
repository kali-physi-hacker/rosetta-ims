# Rename "Raw Observation" to "Extracted Evidence Observation"

Status: follow-up, not urgent

## Problem

Two different things currently share the word "raw":

| Concept | What it is | Where it lives |
|---|---|---|
| Raw stage | File-only preservation + verification of the exact supplier upload (no extraction, no AI) | `orchestration/catalogue_raw_stage.py`, `CatalogueSourceDocument`, `CatalogueRawStageAttempt` |
| "Raw Observation" | Verbatim evidence (text/cells, source location, provider/model metadata, confidence) produced by the EXTRACTION stage | `CatalogueRawObservation`, `catalogue.raw_observation.v1` contract, `RawObservationService` |

The execution boundary is correct — extraction runs strictly after the raw
stage — but the naming invites confusion.

## Decision for now

Docstrings and architecture docs distinguish the two; new code and docs say
"extracted evidence observation". No table/contract rename yet: it would touch
the persisted table, the versioned `catalogue.raw_observation.v1` JSON contract,
the stage service, persistence mappers and many tests — too broad to piggyback
on a correction task.

## When renaming

- Introduce `catalogue.extracted_evidence_observation.v1` as a successor
  contract version rather than mutating v1 in place.
- Rename table via migration (`catalogue_raw_observations` →
  `catalogue_extracted_evidence_observations`) once Alembic exists.
- Rename `RawObservationService.capture` and the stage adapter symbols in the
  same change; grep target: `raw_observation`.
