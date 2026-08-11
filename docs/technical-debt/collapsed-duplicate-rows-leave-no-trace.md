# A collapsed duplicate leaves no trace of what absorbed it

Found while auditing the dead-letter lane (DEV-202) on 11 Aug 2026. Not a bug —
a gap in the record that only matters once someone asks a question nobody has
had to ask yet.

## What happens

A supplier catalogue lists one product several times, once per order quantity:

```
ME5705  Metacoxx Oral Suspension 0.5mg/mL   15ml/ bot   1 bot    78.0
ME5705  Metacoxx Oral Suspension 0.5mg/mL   15ml/ bot   6 bots   77.0
ME5705  Metacoxx Oral Suspension 0.5mg/mL   15ml/ bot   12 bots  76.0
```

Extraction records all three, correctly — the prompt is explicit that repeated
rows are preserved at their separate source locations. Staging then produces a
single normalized row, with the tiers becoming MBB terms. Also correct.

On the Alfamedic catalogue that is **186 of 1,744 product rows**, collapsed into
the 1,558 that reach staging.

## The gap

`catalogue_normalized_row_evidence` links a normalized row to the evidence it
was built from. The 186 collapsed rows are linked to **nothing**. They are not
recorded as merged into the row that survived; they simply have no link.

So "did this run drop anything?" cannot be answered from the data. The count is
visible — RAW product rows minus normalized rows — but not the reason, and a
collapse and a silent drop look identical from the outside. Establishing that
these particular 186 were duplicates took a manual pass comparing supplier codes
between the linked and unlinked sets.

`reconcile()` now reports the number and the golden test asserts no code appears
only among the unlinked, so a genuine drop fails loudly. That closes the safety
question. It does not close the provenance one.

## Why it is not obviously worth fixing

Linking every collapsed row to its survivor would make the merge auditable and
would let the review desk show "this price came from three printed rows". It
would also grow the link table by roughly 12% on a catalogue like this one, for
a fact nobody has yet needed.

The counter-argument is DEV-209. Operations verifies the served numbers against
the sheet, and the first question on a disputed MBB tier is "which printed line
did this come from". Today the answer is "one of these three, we did not keep
track".

## The decision this needs

Whether a collapsed duplicate is:

* **discarded**, as now — the surviving row is the record, and provenance stops
  at the row that made it through;
* **linked**, so the merge is auditable and the desk can show every printed line
  behind a price;
* **linked only when it contributed a term** — the tiers that became MBB rows
  are traceable, the exact repeats are not.

This belongs with SPIKE-204, which is already settling how product identity
flattens and what a price is quoted against. It is the same question one layer
down: what counts as one product, and what is merely another way of printing it.
