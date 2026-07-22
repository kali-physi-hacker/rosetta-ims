# Supplier-Source Catalogue Contracts

CIS-103B adds a separate supplier-source contract layer for incoming supplier catalogue formats. These contracts describe the shape and semantics of source documents before Rosetta creates shared `RawObservationV1` and `StagingCatalogueItemV1` payloads.

The Python models in `apps/api/schemas/catalogue_pipeline/supplier_contracts/` are authoritative. JSON fixtures and JSON Schemas are generated/review artifacts. The legacy files in `apps/api/catalogue_contracts/*.yaml` remain runtime extraction configuration only; they are not authoritative contracts and were not renamed or wired into the new layer.

## Contract Architecture

```
Supplier source contract
  supplier + document format + version
  e.g. hills.price_list.v1
        |
        v
Shared Raw Observation contract
        |
        v
Shared Staging Catalogue Item contract
        |
        v
Shared Mastering Candidate / Validation Issue / Serving contracts
```

| Layer | Contract source of truth | Purpose |
|---|---|---|
| Supplier-specific source contract | `SupplierSourceContractV1` declarations registered by supplier-format ID | Defines expected document structure, source fields, price/packaging/MBB semantics, validation rules, and known ambiguities. |
| Raw Observation | `catalogue.raw_observation.v1` | Preserves extracted source evidence and exact location. |
| Staging Item | `catalogue.staging_item.v1` | Separates raw source strings from proposed typed interpretations. |
| Mastering/HITL | `catalogue.mastering_candidate.v1` plus `catalogue.validation_issue.v1` | Proposes canonical/supplier-commercial resolution and records decisions needed from BizOps. |
| Serving | `catalogue.serving_item.v1` | Exposes only approved information to consumer-facing views or APIs. |

A supplier-source contract identity is per supplier plus document format plus major version, not merely per supplier. One supplier can have `supplier.price_list.v1`, `supplier.promotion_sheet.v1`, and later `supplier.price_list.v2`.

## Runtime Boundary

CIS-103B does not replace `services/catalogue_contract.py`, parse files at upload time, alter `/catalogues/import`, or select contracts in production. The new registry rejects unknown supplier formats and has a separate `get_supported_supplier_source_contract()` path that only returns `SUPPORTED` declarations. Only Hill's and Alfamedic are currently `SUPPORTED`; the remaining declarations still need row fixtures, supplier-id reconciliation, or per-section parser rules.

Future integration should:

1. Select a supplier-source contract by explicit `contract_id` and `contract_version`.
2. Validate the declaration through `SupplierSourceContractV1`.
3. Use its source-field and validation rules to produce `RawObservationV1`.
4. Interpret proposed business fields into `StagingCatalogueItemV1`.
5. Create `ValidationIssueV1` records instead of guessing unresolved price, packaging, or MBB semantics.

## Evidence Rules

Set `support_status` conservatively:

| Status | Meaning |
|---|---|
| `SUPPORTED` | Real source samples and business rules are sufficient for production interpretation once runtime integration exists. |
| `PARTIALLY_VERIFIED` | Parser behavior, tests, and/or docs support important semantics, but source samples or business confirmation are missing. |
| `UNVERIFIED` | Contract identity is known, but evidence is too thin to apply semantics automatically. |
| `DEPRECATED` | Historical format retained for validation or audit only. |

Legacy YAML alone is insufficient for `SUPPORTED`. A format needs raw source samples, representative extracted rows, confirmed column/header semantics, price basis, packaging/order semantics, and MBB/promotion rules where applicable.

## Coverage Audit

The prompt referenced documentation for about 24 suppliers, but this clean checkout does not contain that full inventory. The local seed file lists nine starter suppliers, `supplier_import.py` can import larger external supplier sheets, and the domain dictionary cites four legacy YAML-backed supplier data-contract files. The table below reflects repository evidence plus the source samples supplied locally for CIS-103B follow-up.

| Supplier | Document format | Evidence available | Legacy YAML exists | Proposed contract ID | Implementation status | Confidence/gap |
|---|---|---|---|---|---|---|
| Alfamedic | PDF price list | Real source catalogue sample; legacy YAML; parser behavior; existing test extraction fixture; business/domain documentation | Yes | `alfamedic.price_list.v1` | `SUPPORTED` | Real sample confirms headers and Price/Unit semantics; MBB tier semantics still need later runtime parsing evidence. |
| Hill's | PDF price list | Real source catalogue sample; legacy YAML; parser behavior; existing test extraction fixture; business/domain documentation | Yes | `hills.price_list.v1` | `SUPPORTED` | Real sample confirms Gross Wholesale, Product Code, Size, Order Multiple, and effective-date layout; supplier code remains unasserted. |
| C. Vetapet & Company / Vetapet Vet | Mixed PDF catalogue/price list | Real source catalogue sample; legacy YAML; parser behavior; existing test extraction fixture | Yes | `vetapet.vet_price_list.v1` | `PARTIALLY_VERIFIED` | Supplied PDF contains several table layouts (`UNIT PRICE`, `WHOLESALE/RETAIL/TERMS`, Chinese wholesale/retail); split or per-section parser rules needed. |
| C. Vetapet & Company / Vetapet Non-Vet | PDF price list section | Real source catalogue sample; legacy YAML; parser load behavior | Yes | `vetapet.non_vet_price_list.v1` | `PARTIALLY_VERIFIED` | Source confirms wholesale/retail labels, but price basis and representative non-vet row fixtures remain missing. |
| Kangaroo Pet Nutrition Ltd / KPN | Mixed PDF catalogue | Real source catalogue sample; user-supplied supplier label | No | `kangaroo.mixed_price_catalogue.v1` | `PARTIALLY_VERIFIED` | Numeric supplier id missing; multiple table layouts need row fixtures before runtime selection. |
| Kangaroo Pet Nutrition Ltd / KPN | Purina Pro Plan Veterinary Diets product list | Real source catalogue sample; user-supplied supplier label | No | `kangaroo.purina_proplan_veterinary_diets.v1` | `PARTIALLY_VERIFIED` | Numeric supplier id missing; wet-can retail basis varies and needs row fixtures. |
| Kangaroo Pet Nutrition Ltd / KPN | Earthz Pet image-only price sheet | Real source catalogue sample; visual inspection | No | `kangaroo.earthz_pet_price_sheet.v1` | `UNVERIFIED` | No text layer; needs OCR/vision fixtures, bounding boxes, and price-basis confirmation. |
| Arrowana Int'l Ltd | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |
| Asia Vet Medical Limited | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |
| Blue Pet Co | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |
| BuggyBix | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |
| Caesars | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |
| Etta International | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |
| Happypaws Int'l Ltd | Unknown | Missing | No | TBD | Missing | Need document type/version, source samples, parser fixtures, and business rules. |

## Adding A Supplier Format

1. Add or confirm the supplier identity without inventing a supplier code.
2. Gather evidence: raw catalogue sample, extracted row fixture, business/domain rule, and any relevant parser behavior.
3. Add a declaration under `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/`.
4. Use `SupplierSourceContractV1` components for fields, source structure, pricing, packaging, MBB, validation, and ambiguity rules.
5. Register the declaration by stable identity such as `supplier_slug.price_list.v1`.
6. Add valid and invalid fixtures under `apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/`.
7. Export schemas:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --with-requirements requirements.txt \
  python scripts/export_supplier_source_contract_schemas.py
```

8. Check schemas:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --with-requirements requirements.txt \
  python scripts/export_supplier_source_contract_schemas.py --check
```

Do not add a permissive generic fallback. If the supplier or document version is unknown, the registry must fail and route the item to explicit review or a later integration path.
