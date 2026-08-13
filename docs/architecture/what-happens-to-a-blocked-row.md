# What happens today to a row that raises BLOCKING

DEV-202 asks for this audit before any design, on the grounds that the design
follows from it. This is the trace, read off the code on 10 Aug 2026.

## The path

1. **Conformance raises the issue.** `services/catalogue_conformance.py` emits
   `severity="BLOCKING"` from five places — an unreadable required header, an
   unresolvable price basis, a required contract field that will not resolve,
   and two validation rules. The issue is persisted to
   `catalogue_validation_issues` with `resolution_status="OPEN"` and
   `publish_blocking=1`, linked to the row by `catalogue_item_uuid`.

2. **The row is written to STAGING anyway.** `catalogue_normalized_rows` gets
   its row. Nothing about that record says it is blocked.

3. **Mastering refuses it, one row at a time.**
   `orchestration/catalogue_tasks.py::prepare_eligible_candidates_task` calls
   `prepare_candidate`, which hits `_raise_if_open_blocking` and raises
   `BlockingValidationIssues`. The task catches it per row:

   ```python
   except stages.BlockingValidationIssues:
       warnings.append(f"interpreted claim {staging_id} has open blocking validation issues")
   ```

4. **No candidate is created.** With no mastering candidate the row cannot be
   reviewed, cannot be applied, and cannot be published. That part is correct.

5. **The only trace is a sentence.** The warning strings are JSON-dumped into
   `catalogue_ingestion_runs.error_summary` as `{"warnings": [...]}` and counted
   in `metrics.warnings_count`. The run finishes `completed_with_warnings`.

## Where that leaves the row

| | |
|---|---|
| Lost? | **No.** It is in staging with its issues attached. |
| Published? | **No.** Correct — it never reaches serving. |
| Marked? | **No.** Nothing on the row records that it was blocked. The state is inferred from the *absence* of a mastering candidate. |
| Enumerable? | **Barely.** One run at a time, by reading free text out of `error_summary`, keyed by an internal staging UUID. |
| Addressable? | **No.** There is no handle DEV-203 can select on to re-drive "the rows that failed under this issue code". |

The count is derivable — RAW rows minus rows that reached a candidate. The
identity is not, and identity is what makes a shortfall fixable rather than
merely known.

## What that implies for the design

**Do not add a table.** `catalogue_validation_issues` already stores everything
about *why* a row failed: stage, issue code, severity, resolution status,
`publish_blocking`, field path, raw and expected values, review guidance,
resolver, resolved-at. Nothing about that is missing.

**Add the lane, as a classification over rows that already exist.** Every
normalized row in a run resolves to exactly one of:

* **published** — its candidate has a current serving publication
* **awaiting review** — it has a candidate, not yet published
* **dead-lettered** — it has no candidate and at least one open blocking issue

A row in none of those, or in more than one, is the bug the invariant exists to
catch. That is checkable as a query today; it does not need new storage.

**Keep machine failure separate from human judgement.** A blocked row is the
pipeline saying it could not interpret the page. A rejected row is a person
saying no. They live in different tables — `catalogue_validation_issues` and
`catalogue_review_decisions` — and conflating them would let "fix the rule and
re-drive" silently re-open something a human already answered.

**Two findings from the golden suite are already tenants.** `E81110E` could not
publish because cost per sellable unit was not derivable; the test had to catch
the exception and stash it in a local dict, because there was nowhere else for
it to go. That dict is a dead-letter lane living in a test file. And the loop
had to step over the failure explicitly, since one refused row otherwise aborts
every row behind it.
