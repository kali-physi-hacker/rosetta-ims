# Catalogue data contracts

A **data contract** tells the parser exactly how ONE supplier's catalogue is structured — which
column is cost vs RRP, whether the price is per-unit or per-pack, where the order multiple / species /
brand live — instead of the AI re-guessing the schema on every import.

Root problem it fixes: extraction today is a single generic Claude prompt for **every** supplier, so the
same Hill's product came out `17.6 / 25.0` in one import and `25.0 / 17.6` (cost/RRP swapped) in the next,
and a case-of-24 landed in `units_per_pack`. A per-supplier contract makes each import deterministic.

## Ingestion flow (as requested)

```
upload a catalogue for supplier S
   └─ contract exists for S?
        ├─ YES → parse with the contract (guided extraction + deterministic map + validate)
        └─ NO  → fall back to today's generic AI extraction (unchanged)
```

Contracts are **additive and opt-in**. A supplier with no contract behaves exactly as today.

## What a contract declares

- `columns:` — source column → canonical field (`supplier_sku`, `description`, `pack_size`, `brand`).
- `pricing:` — `basis` (`per_unit` | `per_pack`), which column is `basic_cost`, `rrp`, and how
  `units_per_pack` is set (const or parsed). This is where cost/RRP swaps and per-unit-vs-per-case are
  settled explicitly.
- `ordering:` — `order_increment_qty` (the catalogue's order multiple / carton) — never feeds the cost divisor.
- `document:` — non-column facts: where `species`, `segment`, `category` come from (section header,
  product name, or const).
- `validate:` — row-level rules (e.g. `basic_cost < rrp`) that **reject or flag** a bad row at ingestion,
  instead of it being discovered months later.

## Status

`hills.yaml` and `alfamedic.yaml` are **drafts for review** — not yet wired into ingestion. Once the
mappings + the flagged decisions (see each file's `⚑`) are approved, the implementation is a Red-Zone
change to `services/extraction_service.py` + `routers/catalogues.py`, spec'd via BMAD. Re-parse then
re-applies an updated contract to backfill existing rows.

Runtime format (YAML file vs DB config) is an implementation choice; these drafts use YAML for readability.
