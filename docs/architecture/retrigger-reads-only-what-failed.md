# A retrigger reads only the observations that failed

DEV-203 asked for the evidence-filtering fork to be decided and the reasoning
recorded before anything was built. This is that record, written 12 Aug 2026.

## The fork

`load_stored_evidence` feeds a re-parse ALL of the source run's observations.
"Re-drive the 384 rows that failed" therefore had two possible shapes:

1. **Filter the evidence** — the retrigger run processes only the observations
   behind the selected rows, and produces rows, candidates and (after review)
   publications for exactly those.
2. **Re-drive everything, report on the selection** — a full re-parse plus
   bookkeeping that says which rows were the point.

## Decision: filter

Three reasons, in order of weight.

**Option 2 re-creates the problem this ticket exists to solve.** A full
re-parse of the Alfamedic run mints fresh PENDING candidates for ~1,150 rows a
person already reviewed in the parent. The review desk fills with work nobody
needs to do — which is the DLQ audit's "inventing work" failure, produced
deliberately this time. Selectivity exists to protect human decisions; a
mechanism that re-opens all of them to fix 9 rows protects nothing.

**The "split catalogue" worry dissolved on inspection.** The original ticket
feared that publishing only the selected rows leaves one catalogue spread
across two runs with no answer to "which is live". But serving publications are
per-product with `is_current` — supersession already works product-by-product,
which the SUPERSEDED lane work proved when a re-upload superseded 36 of an
older run's publications. The serving layer never had a per-run notion of
"live" to split.

**Cost.** Re-running conformance, persistence and validation over 1,555 rows to
target 9 is waste at Alfamedic's size and prohibitive at 11k products.

## How the selection travels

The selection is a set of **raw observation UUIDs** — the evidence rows behind
the dead-lettered normalized rows, found through
`catalogue_normalized_row_evidence`. It is stamped into the retrigger run's
`metrics` (`retrigger_observations`) at request time, because the flow that
consumes it runs in another process, later, and must see exactly what was
chosen. `load_stored_evidence` filters to the selection when present; a
selection matching nothing on the source run refuses loudly.

## What the queue does afterwards

**Lanes never follow the chain.** A row the parent could not read remains true
of the parent; lanes are per-run history and the invariant tests depend on
that.

**The queue follows by default.** `dead_letters(run)` is the actionable view:

* an observation whose latest retrigger produced a candidate **leaves the
  queue** — it is in review, where a person can see it;
* an observation whose retrigger produced no row at all also leaves — it was
  absorbed as a duplicate tier of a row that now parses, which is a term
  captured, not a row stuck;
* an observation that failed again appears **once**, carrying `attempts` and
  the latest codes — one entry with a count, never one entry per try.

`dead_letters(run, follow_retriggers=False)` keeps the historical per-run view.

Only a retrigger that **ran** counts: children still queued, running, failed or
cancelled contribute nothing, because reading "selection with no rows yet" as
"absorbed" would empty the queue the moment the 202 came back — hours before
the worker touches a row, and forever if it then fails. Silence is not success.

A plain FULL re-parse child is also ignored by the followed queue on purpose:
it re-reads everything, so its failures are its own run's story, and its
successes are reviewed on its own desk. Only selective retriggers speak for
the parent's queue.

## What a retrigger may never select

Selection is drawn from the followed queue, so these are unselectable by
construction, and an explicit id naming one is refused with the reason:

| state | why it is out of reach |
| --- | --- |
| candidate REJECTED | a person said no; re-driving would resurrect a decision |
| issue DISMISSED / ACCEPTED_AS_IS / CONFIRMED / CORRECTED | a person resolved it; only OPEN blocking issues dead-letter a row |
| already in review / published / superseded | nothing is stuck |
| cleared by an earlier retrigger | already fixed; the queue follows the chain |

## Identity is re-minted; matching crosses on the key

A retrigger child re-captures its own copies of the selected observations, and
observation UUIDs are minted per run — the same printed row gets a fresh
identity every time. A child's links therefore never carry the source's UUIDs,
and every match between a selection and a later run's outcome crosses on
`source_object_key` ("page:1:obs:<hash>:<n>"), which is derived from content
and position and is the one name a row keeps across runs. The selection stored
on a retrigger run is always in SOURCE-run UUIDs; the service translates
through the key when the run being retriggered is itself a re-parse.

The flow's terminal metrics write replaces the run's JSON wholesale, keeping
only named keys — the retrigger keys are on that list
(`_PRESERVED_METRIC_KEYS`), because losing them makes every retrigger
invisible the moment it completes. That was found the hard way: the first
implementation's chain detection returned nothing because completion had
erased the selection.

## Two refusals the final audit added

**A retrigger is not a retrigger target.** The endpoint used to accept one,
and the mechanics even worked — selection translated back to the origin's
evidence, the child read stored rows, nothing touched the provider. What broke
was the ledger: the grandchild hung off the retrigger child, one level below
where the followed queue ever looks, so the origin kept reporting rows as
stuck after they had cleared (probed live: the origin's queue held 390 while
the child's view had correctly dropped to 3). The service now refuses the
child by name and points at the run it re-drove. Nothing is lost: selection
reads the followed queue, so retriggering the origin re-drives exactly the
rows still failing. This is also what keeps `retrigger_children` honest —
every retrigger of a run is a direct sibling, and a chain cannot form.

**One outstanding retrigger at a time.** The queue rightly ignores a child
that has not finished, so a second request made during that window selects
the very same rows — and when both children complete, every cleared row puts
TWO identical pending candidates in front of a person (probed live: all five
cleared SKUs doubled). A retrigger is refused while an earlier one is queued
or running; a FAILED child reopens the road, and the request ordinal counts
the attempt that died.
