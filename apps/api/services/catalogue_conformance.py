"""Deterministic conformance of persisted catalogue evidence (STAGING layer).

This module is the only place where verbatim source evidence becomes
contract-conformed normalized rows. It consumes Staging-persisted extracted
evidence observations (``raw_cells`` with column names) together with the
resolved supplier-source contract, and maps each named cell through the
contract's declared source columns to typed normalized fields. Every field
points back at the supporting extracted evidence observation.

Boundaries this module enforces:

- Deterministic only. The supplier contract maps source columns to business
  fields; nothing here calls a model, guesses, invents, or normalizes values
  the contract did not declare. No re-reading of the source file — only
  persisted evidence.
- Header rows (cells that repeat the contract's declared source columns) are
  evidence, not rows, and are skipped from Staging.
- Observations that carry no structured cells cannot be conformed
  deterministically; they are staged for manual review rather than dropped.
  (With cell-producing extraction — spreadsheets/CSV and vision — this is an
  edge case, e.g. a plain-text source with no columns.)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from schemas.catalogue_pipeline.common import UnitOfMeasure
from schemas.catalogue_pipeline.enums import UnitCode
from schemas.catalogue_pipeline.supplier_contracts.common import SourceFieldRequirement
from services.catalogue_evidence_extraction import ExtractedEvidence


@dataclass(frozen=True)
class ContractExecutionIssue:
    """Machine-readable contract condition that must not be silently ignored."""

    issue_code: str
    message: str
    severity: str = "WARNING"
    field_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_code": self.issue_code,
            "severity": self.severity,
            "message": self.message,
            "field_key": self.field_key,
        }


@dataclass(frozen=True)
class ConformedRow:
    """One normalized row deterministically conformed from one evidence observation.

    ``provenance`` records HOW the row was produced ("contract_cells" for a
    deterministic contract-cell mapping, "unconformable" when the observation
    carried no structured cells to map). It is persisted onto the row's
    metadata.
    """

    observation_key: str
    raw_observation_id: UUID
    raw_fields: dict[str, Any]
    normalized_fields: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    issues: tuple[ContractExecutionIssue, ...] = ()


@dataclass(frozen=True)
class ConformanceOutcome:
    """Conformance results plus durable accounting.

    ``metadata`` carries machine-readable accounting (conformed / skipped
    header / skipped ineligible / unconformable counts) so the run record can
    distinguish "skipped as header row" from "was not a catalogue row" from
    "became a bulk term on the row above" from "could not be conformed".
    ``skipped_count`` is their total, and every ``skipped_*`` key sums to it:
    each observation either becomes an item or is counted under exactly one
    reason. That is the invariant the run's row accounting rests on.
    """

    items: tuple[ConformedRow, ...]
    warnings: tuple[str, ...] = ()
    skipped_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    issues: tuple[ContractExecutionIssue, ...] = ()


def conform_observations(
    observations: tuple[ExtractedEvidence, ...],
    raw_observation_ids: tuple[UUID, ...],
    runtime_contract,
) -> ConformanceOutcome:
    """Conform persisted observations into normalized rows using the contract."""

    if len(observations) != len(raw_observation_ids):
        raise ValueError("observations and evidence ids must align")

    document_issues = _document_issues(observations, runtime_contract)
    warnings: list[str] = [issue.message for issue in document_issues]
    items: list[ConformedRow] = []
    inheritable = tuple(
        f.field_key for f in runtime_contract.declaration.fields
        if getattr(f, "inherits_from_row_above", False)
    )
    carried: dict[str, str] = {}
    carried_page: Any = object()   # a name never carries across a page break
    parent_index: int | None = None  # the product a tier row would attach to
    skipped = 0        # total, and the number the run's accounting uses
    header_rows = 0
    furniture = 0
    unconformable = 0
    ineligible = 0
    tier_rows = 0
    band_rows = 0
    discontinued = 0
    for observation, raw_id in zip(observations, raw_observation_ids, strict=True):
        key = observation.observation_key
        if _has_cells(observation):
            fields = _fields_from_cells(observation, runtime_contract)
            if fields is None:
                # Header row — evidence, not a row.
                skipped += 1
                header_rows += 1
                continue
            # A "previous SKU" equal to the current one is a rename to itself
            # — a claim the source never made. It happens when a contract
            # aliases the bare code heading for BOTH fields (K.P.N.'s
            # pack_price_list prints 新產品編號 + 產品編號 on renumbering
            # pages, but ordinary pages print only 產品編號, and both fields
            # then read the same cell). PR-18 closing audit, finding 2.
            if fields.get("source:previous_supplier_sku") and (
                fields.get("source:previous_supplier_sku") == fields.get("source:supplier_sku")
            ):
                for key in [k for k in fields if "previous_supplier_sku" in k]:
                    fields.pop(key, None)
            # Quantity-ladder pricing resolves BEFORE row issues are computed,
            # so a ladder-satisfied cost also satisfies the REQUIRED check.
            _apply_quantity_ladder(fields, observation, runtime_contract)
            page = observation.source_location.page_number
            if page != carried_page:
                # The last product on a page owns nothing on the next one —
                # neither a name to carry nor a tier to collect.
                carried, carried_page, parent_index = {}, page, None
            if _is_discontinued(fields, runtime_contract):
                # The supplier has withdrawn it. Not a tier, not a product.
                skipped += 1
                discontinued += 1
                continue
            tier = _tier_row_term(fields, runtime_contract, items, parent_index, raw_id, observation)
            if tier is not None:
                # A priced line with no identity, beneath a product: a term on
                # that product, not a catalogue row of its own.
                previous, term, lifted_cost = tier
                merged = dict(items[previous].normalized_fields)
                merged["mbb_terms"] = [*merged.get("mbb_terms", []), term]
                if lifted_cost is not None and not merged.get("cost"):
                    # The price cell is merged down the block and the model
                    # reported it on this line rather than the first. It is the
                    # product's price, not the offer's.
                    merged["cost"] = lifted_cost
                items[previous] = replace(items[previous], normalized_fields=merged)
                skipped += 1
                tier_rows += 1
                continue
            band = _quantity_band_term(fields, runtime_contract, items, raw_id, observation)
            if band is not None:
                # The same code printed again at a deeper quantity band: a
                # discount on the product above, not a second product.
                base_index, term = band
                merged = dict(items[base_index].normalized_fields)
                merged["mbb_terms"] = [*merged.get("mbb_terms", []), term]
                items[base_index] = replace(items[base_index], normalized_fields=merged)
                skipped += 1
                band_rows += 1
                continue
            if _is_ineligible_row(fields, runtime_contract):
                # Carries neither an identity nor a price: a divider, a blank
                # spacer, a section title. Evidence, not an item — and counted
                # as skipped, like every other row that does not become one.
                skipped += 1
                ineligible += 1
                continue
            inherited = _carry_merged_cells(fields, inheritable, carried, runtime_contract)
            row_issues = (
                document_issues
                + _row_eligibility_issues(fields, runtime_contract)
                + _supplier_identity_issues(observation, runtime_contract)
                + _required_field_issues(fields, runtime_contract)
                + _validation_rule_issues(fields, runtime_contract)
                + _normalization_issues(fields, runtime_contract)
            )
            items.append(
                _item_from_fields(
                    observation,
                    raw_id,
                    fields,
                    runtime_contract,
                    provenance=_provenance("contract_cells", inherited_fields=inherited),
                    warnings=tuple(issue.message for issue in row_issues),
                    issues=row_issues,
                )
            )
            parent_index = len(items) - 1
            warnings.extend(f"{key}: {issue.message}" for issue in row_issues)
            continue

        # No structured cells to map through the contract.
        if _contract_is_tabular(runtime_contract):
            # Under a TABULAR contract, a cells-less text line is page
            # furniture (section banner, document title, effective date,
            # policy footnote) — evidence, not a catalogue row. It stays
            # persisted as an extracted observation but is skipped from
            # normalization, exactly like a header row. Blocking every
            # banner would bury reviewers in non-issues.
            skipped += 1
            furniture += 1
            continue
        # Non-tabular contract: a text line may genuinely be the catalogue
        # row. Never invent fields; stage it for manual review.
        unconformable += 1
        message = "no structured cells to conform; staged for manual review"
        warnings.append(f"{key}: {message}")
        items.append(
            _item_from_fields(
                observation,
                raw_id,
                {},
                runtime_contract,
                provenance=_provenance("unconformable"),
                warnings=(message,),
                issues=(
                    ContractExecutionIssue(
                        issue_code="CONTRACT_ROW_UNCONFORMABLE",
                        severity="BLOCKING",
                        message=message,
                    ),
                ),
            )
        )

    return ConformanceOutcome(
        items=tuple(items),
        warnings=tuple(warnings),
        skipped_count=skipped,
        metadata={
            "conformed_items": len(items),
            "skipped_header_rows": header_rows,
            "skipped_ineligible_rows": ineligible,
            # Named as a skip so every reason a row did not become an item
            # lives under one prefix and the set sums to skipped_count.
            "skipped_tier_rows": tier_rows,
            "skipped_quantity_band_rows": band_rows,
            "skipped_discontinued_rows": discontinued,
            "skipped_non_tabular_text": furniture,
            "unconformable_items": unconformable,
            "contract_issue_count": len(document_issues) + sum(len(item.issues) for item in items),
            "document_issues": [issue.as_dict() for issue in document_issues],
            "declared_row_eligibility_rules": list(runtime_contract.declaration.source_structure.row_eligibility_rules),
            "declared_skip_rules": list(runtime_contract.declaration.source_structure.skip_rules),
            # Declared per-FORMAT ambiguities become ONE durable run-scoped review
            # issue each (not one per row) — promoted by the validation stage.
            "known_ambiguity_issues": [
                {
                    "issue_code": ambiguity.issue_code,
                    "severity": "WARNING",
                    "message": ambiguity.condition,
                    "review_guidance": ambiguity.review_guidance,
                }
                for ambiguity in runtime_contract.declaration.known_ambiguities
            ],
            "degraded": unconformable > 0 or bool(document_issues) or any(item.issues for item in items),
        },
        issues=document_issues,
    )


def _contract_is_tabular(runtime_contract) -> bool:
    """True when the contract declares a cells-shaped source (tables/sheets)."""

    source_format = getattr(runtime_contract.declaration.source_structure, "source_format", None)
    value = getattr(source_format, "value", source_format)
    return value in {"PDF_TABLE", "SPREADSHEET", "CSV"}


def _provenance(interpreter: str, *, inherited_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    provenance: dict[str, Any] = {"interpreter": interpreter}
    if inherited_fields:
        # Says plainly that the supplier did not print this on THIS line — the
        # cell above spans it. Provenance, not a review gate: these rows are
        # real SKUs and should flow, they just did not carry their own name.
        provenance["inherited_from_row_above"] = list(inherited_fields)
    return provenance


def _fields_from_cells(observation: ExtractedEvidence, runtime_contract) -> dict[str, Any] | None:
    """Deterministically map named cells through the contract's source columns.

    Handles direct source columns, ``composed_from`` multi-column joins, and
    contract constants — the full deterministic mapping the supplier contract
    declares. Returns None when the observation is a header row (its cell values
    repeat the contract's declared source columns) — headers are evidence, not
    rows.
    """

    # Index this row's cells by every match-key of their column heading.
    cell_by_key: dict[str, str] = {}
    # Same index, every match rather than the first: a table may repeat one
    # heading across several columns, and then only position tells them apart.
    cells_by_key: dict[str, list[str]] = {}
    ordered_cells: list[tuple[list[str], str]] = []
    row_values: list[str] = []
    # Values printed under a column with NO heading at all. Some layouts leave
    # one column unlabeled (K.P.N. Trading prints pack sizes and NOW FRESH
    # product names that way) — heading text cannot address them, so a contract
    # may claim them via source_path="unlabeled_column" instead. Collected
    # separately: they never enter heading-keyed matching.
    unlabeled_values: list[str] = []
    for cell in observation.raw_cells:
        if cell.raw_value is None or not str(cell.raw_value).strip():
            continue
        if cell.column_name:
            row_values.append(str(cell.raw_value))
            ordered_cells.append((_column_keys(cell.column_name), str(cell.raw_value)))
            for key in _column_keys(cell.column_name):
                cell_by_key.setdefault(key, str(cell.raw_value))
                cells_by_key.setdefault(key, []).append(str(cell.raw_value))
        else:
            unlabeled_values.append(str(cell.raw_value))
    if not cell_by_key:
        return {}

    def _lookup(*column_names: str) -> str | None:
        for column_name in column_names:
            for key in _column_keys(column_name):
                if key in cell_by_key:
                    return cell_by_key[key]
        return None

    def _lookup_nth_in_family(occurrence: int, prefix: str) -> str | None:
        """The nth column whose heading BEGINS with this family, left to right.

        Heading text is not a stable key — the same three columns come back
        labelled by threshold, by percentage, or bare, depending on the page
        and the run. The family and the printed order are stable.
        """
        prefix_keys = _column_keys(prefix)
        matches = [
            value
            for keys, value in ordered_cells
            if any(key.startswith(pk) for key in keys for pk in prefix_keys)
        ]
        return matches[occurrence - 1] if len(matches) >= occurrence else None

    def _lookup_nth(occurrence: int, *column_names: str) -> str | None:
        """The nth column carrying this heading, left to right.

        A heading that appears once answers as itself only for occurrence 1 —
        asking for the third of one column is a mismatch, not the first.
        """
        for column_name in column_names:
            for key in _column_keys(column_name):
                matches = cells_by_key.get(key) or []
                if len(matches) >= occurrence:
                    return matches[occurrence - 1]
        return None

    def _lookup_field(contract_field) -> str | None:
        exact = tuple(
            name
            for name in (contract_field.source_column, contract_field.source_path)
            if name and name != _UNLABELED_COLUMN_SOURCE
        )
        occurrence = getattr(contract_field, "source_column_occurrence", None)
        if occurrence:
            # Where the source printed the distinguishing heading, it is
            # unambiguous and wins outright. The occurrence disambiguates only
            # the repeated fallback heading — asking it to also index the exact
            # one would lose tiers 2 and 3 on the pages that name them, since
            # that heading appears exactly once there.
            found = _lookup(*exact) or _lookup_nth(occurrence, *contract_field.aliases)
            prefix = getattr(contract_field, "source_column_prefix", None)
            if found is None and prefix:
                found = _lookup_nth_in_family(occurrence, prefix)
        else:
            found = _lookup(*exact, *contract_field.aliases)
        if found is None and contract_field.source_path == _UNLABELED_COLUMN_SOURCE:
            # Claim the row's unlabeled column — but ONLY when exactly one
            # non-empty unlabeled value exists. Two unlabeled values cannot be
            # told apart by anything a contract can declare, and guessing
            # between them would be inventing, so ambiguity resolves to
            # nothing rather than to a coin flip.
            if len(unlabeled_values) == 1:
                return unlabeled_values[0]
        return found

    def _lookup_composed(column_name: str) -> str | None:
        # A composed name may join printed COLUMNS with values that are not
        # columns at all — the section banner above the table, a field whose
        # own heading moved between runs. Every simple field is resolved before
        # composition runs, so any of them answers by its field key.
        resolved = fields.get(f"source:{column_name}")
        if _text(resolved) is not None:
            return _text(resolved)
        aliases: list[str] = []
        for candidate in runtime_contract.declaration.fields:
            if candidate.source_column == column_name or candidate.source_path == column_name:
                aliases.extend(candidate.aliases)
        for key in _column_keys(column_name):
            if key in cell_by_key:
                return cell_by_key[key]
        return _lookup(*aliases)

    # Header row: its cell VALUES repeat the contract's declared source columns.
    source_keys: set[str] = set()
    for contract_field in runtime_contract.declaration.fields:
        for source_name in filter(
            None, (contract_field.source_column, contract_field.source_path, *(contract_field.composed_from or ()))
        ):
            source_keys.update(_column_keys(source_name))
        for alias in contract_field.aliases:
            source_keys.update(_column_keys(alias))
    header_hits = sum(1 for value in row_values if any(key in source_keys for key in _column_keys(value)))
    if header_hits >= max(2, len(row_values) - 1):
        return None

    fields: dict[str, Any] = {}
    # The section banner is not a cell, so it is resolved before the column
    # loop — a composed product name may need it as one of its parts.
    section = _text((observation.source_metadata or {}).get("section"))
    if section is not None:
        for contract_field in runtime_contract.declaration.fields:
            if contract_field.source_path == _SECTION_HEADER_SOURCE:
                fields[f"source:{contract_field.field_key}"] = section
                target = _role_target(contract_field.role)
                if target:
                    fields.setdefault(target, section)
    page_brand = _text((observation.source_metadata or {}).get("page_brand_text"))
    if page_brand is not None:
        for contract_field in runtime_contract.declaration.fields:
            if contract_field.source_path == _PAGE_BRAND_SOURCE:
                fields[f"source:{contract_field.field_key}"] = page_brand
                target = _role_target(contract_field.role)
                if target:
                    fields.setdefault(target, page_brand)
    def _record(contract_field, value: Any) -> None:
        value = _mapped_value(contract_field, value)
        target = _role_target(contract_field.role) or f"additional:{contract_field.field_key}"
        # Preserve every declaration by its stable contract field key even
        # when multiple fields share one semantic role (for example pack
        # size and units per case are both PACKAGING).
        fields[f"source:{contract_field.field_key}"] = value
        fields.setdefault(target, value)

    # Two passes, so a composed value may name any other field regardless of
    # declaration order. Composing from field keys rather than raw headings is
    # what lets a run that renames a column still resolve through that field's
    # aliases — but it only works if the parts are resolved first.
    composed: list[Any] = []
    for contract_field in runtime_contract.declaration.fields:
        if contract_field.composed_from:
            composed.append(contract_field)
        value = _lookup_field(contract_field)
        if value is None and not contract_field.composed_from and contract_field.constant_value is not None:
            value = contract_field.constant_value
        if value is not None:
            _record(contract_field, value)
    for contract_field in composed:
        if _text(fields.get(f"source:{contract_field.field_key}")) is not None:
            continue
        parts = [part for part in (_lookup_composed(name) for name in contract_field.composed_from) if part]
        value = " ".join(parts) if parts else contract_field.constant_value
        if value is not None:
            _record(contract_field, value)
    if observation.confidence is not None:
        fields.setdefault("confidence", str(observation.confidence))
    return fields


def _item_from_fields(
    observation: ExtractedEvidence,
    raw_observation_id: UUID,
    fields: dict[str, Any],
    runtime_contract,
    *,
    provenance: dict[str, Any] | None = None,
    warnings: tuple[str, ...] = (),
    issues: tuple[ContractExecutionIssue, ...] = (),
) -> ConformedRow:
    additional_fields = {
        key.removeprefix("source:"): value
        for key, value in fields.items()
        if key.startswith("source:")
    }
    raw_fields = {
        "supplier_sku": _text(fields.get("supplier_sku")),
        "product_name": _text(fields.get("description")),
        "original_product_name": _text(fields.get("original_description")),
        "brand": _text(fields.get("brand")),
        "category": _text(fields.get("category")),
        "cost": _raw_money_text(fields.get("cost_price")),
        "rrp": _raw_money_text(fields.get("rrp")),
        "packaging": _text(fields.get("pack_size") or fields.get("uom")),
        "mbb_text": _text(fields.get("bulk_buy_tiers")),
        "barcode": _text(fields.get("barcode")),
        "variant": _text(fields.get("variant")),
        "species": _text(fields.get("species")),
        "segment": _text(fields.get("segment")),
        "effective_date": _text(fields.get("effective_date")),
        "content_measure": _text(fields.get("content_measure")),
        "row_eligibility": _text(fields.get("row_eligibility")),
        "additional_fields": additional_fields,
        "source_row_label": observation.observation_key,
    }
    evidence = {
        "raw_observation_id": str(raw_observation_id),
        "field_path": "/raw_cells" if _has_cells(observation) else "/raw_text",
        "confidence": _confidence_text(fields.get("confidence"), observation.confidence),
    }
    normalized: dict[str, Any] = {"mbb_terms": []}
    # normalized product_name = the contract's composed join, English only (bilingual
    # source cells keep their Latin portion). raw_fields keeps the verbatim join.
    # No recomposition beyond the contract: no brand/size, no reordering, no dedup.
    product_name = _english_text(fields.get("description")) or _text(fields.get("description"))
    if product_name is not None:
        normalized["product_name"] = {"value": product_name, "evidence": evidence}
    for source_key, normalized_key in (
        ("supplier_sku", "supplier_sku"),
        ("brand", "brand"),
        ("category", "category"),
        ("barcode", "barcode"),
        ("variant", "variant"),
        ("species", "species"),
        ("segment", "segment"),
    ):
        value = _text(fields.get(source_key))
        if value is not None:
            normalized[normalized_key] = {"value": value, "evidence": evidence}
    effective_date = _date_proposal(fields.get("effective_date"), evidence)
    if effective_date is not None:
        normalized["effective_date"] = effective_date

    cost = _cost_proposal(fields.get("cost_price"), runtime_contract, evidence, fields)
    struck_offer_term = None
    if cost is None:
        # A declared struck-price render ('$739 HK$1056.0') is the one two-
        # amount cell that is not a guess: the larger is the regular price,
        # the smaller the printed Special Offer.
        struck = _struck_price_offer(fields, runtime_contract, evidence)
        if struck is not None:
            cost, struck_offer_term = struck
    ladder = fields.get("__quantity_ladder__")
    if ladder and cost is not None and ladder.get("per_unit_basis"):
        # The rung heading literally says "(per unit)" — the price is per
        # single unit (dose), never per the packing text's slash-unit.
        cost = {**cost, "price_basis": UnitOfMeasure(code=UnitCode.UNIT).model_dump(mode="json")}
    if cost is not None:
        normalized["cost"] = cost
    rrp = _money_proposal(fields.get("rrp"), runtime_contract, evidence)
    if rrp is not None:
        normalized["rrp"] = rrp
    packaging = _packaging_proposal(fields, runtime_contract, evidence)
    if ladder and ladder.get("minimum_order") and packaging is not None and cost is not None:
        # The lowest rung's bound is the minimum order ("50 doses or above"
        # means you cannot order fewer) — a bound of 1 was dropped upstream
        # because an increment of one is not a constraint.
        packaging = {
            **packaging,
            "minimum_order_quantity": {
                "amount": ladder["minimum_order"],
                "uom": cost["price_basis"],
            },
        }
    if packaging is not None:
        normalized["packaging"] = packaging
    normalized["mbb_terms"] = (
        _tier_price_terms(fields, runtime_contract, evidence, cost)
        + _page_promotion_terms(observation, runtime_contract, evidence)
        + ([struck_offer_term] if struck_offer_term else [])
        + _quantity_ladder_terms(ladder, cost, evidence)
    )

    return ConformedRow(
        observation_key=observation.observation_key,
        raw_observation_id=raw_observation_id,
        raw_fields=raw_fields,
        normalized_fields=normalized,
        provenance=dict(provenance or {}),
        warnings=warnings,
        issues=issues,
    )


def _money_proposal(value: Any, runtime_contract, evidence: dict[str, Any]) -> dict[str, Any] | None:
    amount = _decimal_or_none(value)
    if amount is None:
        return None
    return {
        "amount": str(amount),
        "currency": runtime_contract.declaration.pricing.currency,
        "evidence": evidence,
    }


def _date_proposal(value: Any, evidence: dict[str, Any]) -> dict[str, Any] | None:
    parsed = _date_or_none(value)
    if parsed is None:
        return None
    return {"value": parsed.isoformat(), "evidence": evidence}


#: "$100.00 / bag $1,200.00 / box" — the unit price and the case price in one
#: cell, which is how the vision pass renders the intravenous tables on some
#: pages and as two columns on others. The FIRST pair is the per-unit one, on
#: every page and on the golden sheet, so both the amount and the basis are
#: taken from it. Reading the last pair instead would price a bag as a box.
_PRICE_WITH_BASIS = re.compile(r"^\s*([^/]+?)\s*/\s*([^/$]+?)\s*(?:\$|$)")


def _first_price_and_basis(value: Any) -> tuple[str, str] | None:
    match = _PRICE_WITH_BASIS.match(str(value or ""))
    return (match.group(1), match.group(2).strip()) if match else None


def _cost_amount(value: Any, pricing, *, non_negative: bool = False) -> Decimal | None:
    """The money on a cost cell, allowing for a basis printed after the amount.

    _decimal_value deliberately refuses a numeric suffix so a genuine fraction
    ("3/8") never half-parses — a good default this does not weaken. But United
    Italian print "$52.00 / 100’s": a perfectly clear amount whose basis happens
    to BE a count. Where a contract has DECLARED that the text after the slash
    is the basis, the money is the half before it.

    Used by both the cost proposal and the issue check, because a price the
    pipeline can read and a price it reports as unreadable must never be
    decided by two different pieces of code.
    """
    reader = _decimal_or_none if non_negative else _decimal_value
    amount = reader(value)
    if amount is None and pricing.price_basis_is_suffix_of_price:
        head = _first_price_and_basis(value)
        if head:
            amount = reader(head[0])
    return amount


def _normalization_issues(
    fields: dict[str, Any],
    runtime_contract,
) -> tuple[ContractExecutionIssue, ...]:
    declaration = runtime_contract.declaration
    issues: list[ContractExecutionIssue] = []
    raw_cost = fields.get("cost_price")
    if _matches_null_marker(raw_cost, declaration.pricing.null_cost_markers):
        issues.append(
            ContractExecutionIssue(
                issue_code="CONTRACT_NULL_COST_REQUIRES_REVIEW",
                severity="WARNING",
                field_key=declaration.pricing.cost_source_field,
                message="The supplier marked the cost as unavailable; no numeric cost was proposed.",
            )
        )
    elif (
        _cost_amount(raw_cost, declaration.pricing, non_negative=True) is not None
        and _declared_cost_basis(runtime_contract, fields) is None
    ):
        issues.append(
            ContractExecutionIssue(
                issue_code="CONTRACT_PRICE_BASIS_UNRESOLVED",
                severity="BLOCKING",
                field_key=_cost_issue_field_key(runtime_contract, fields),
                message="A supplier price was observed, but its price basis is unresolved.",
            )
        )
    elif _text(raw_cost) is not None and (
        (_cost := _cost_amount(raw_cost, declaration.pricing)) is None or _cost < 0
    ):
        issues.append(
            ContractExecutionIssue(
                issue_code="CONTRACT_COST_UNPARSEABLE",
                severity="BLOCKING",
                field_key=_cost_issue_field_key(runtime_contract, fields),
                message=f"Supplier cost '{raw_cost}' could not be normalized as a monetary amount.",
            )
        )

    raw_date = _text(fields.get("effective_date"))
    if raw_date and _date_or_none(raw_date) is None:
        issues.append(
            ContractExecutionIssue(
                issue_code="CONTRACT_EFFECTIVE_DATE_UNPARSEABLE",
                severity="WARNING",
                field_key="effective_date",
                message=f"Effective date '{raw_date}' could not be normalized without guessing.",
            )
        )

    raw_mbb = _text(fields.get("bulk_buy_tiers"))
    if raw_mbb:
        guidance = declaration.mbb.requires_validation_issue_when
        issues.append(
            ContractExecutionIssue(
                issue_code="CONTRACT_MBB_REQUIRES_REVIEW",
                severity="WARNING",
                field_key="mbb_text",
                message=(
                    "Promotion or MBB text was preserved but not normalized because its scope, "
                    "condition, or benefit is not fully proven."
                    + (f" Review condition: {' '.join(guidance)}" if guidance else "")
                ),
            )
        )
    return tuple(issues)


# One comparison per rule: `<field> <op> <field-or-number>`. Deliberately not a
# general expression language — anything beyond this grammar is surfaced as
# unsupported rather than guessed at.
_RULE_EXPRESSION = re.compile(
    r"^\s*([a-z_][a-z0-9_]*)\s*(<=|>=|==|!=|<|>)\s*([a-z_][a-z0-9_]*|-?\d+(?:\.\d+)?)\s*$"
)
_RULE_OPERATORS = {
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
}


def _rule_operand(token: str, fields: dict[str, Any]) -> Decimal | None:
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return Decimal(token)
    return _decimal_value(fields.get(token))


def _validation_rule_issues(
    fields: dict[str, Any],
    runtime_contract,
) -> tuple[ContractExecutionIssue, ...]:
    """Execute declared comparison rules deterministically; never evaluate code.

    A rule fires only when BOTH operands are present and numeric — absent or
    unparseable values are the province of the null-marker/required-field
    checks, not silent rule failures.
    """

    issues: list[ContractExecutionIssue] = []
    for rule in runtime_contract.declaration.validation_rules:
        if not rule.source_expression:
            continue
        parsed = _RULE_EXPRESSION.match(rule.source_expression)
        if parsed is None:
            issues.append(
                ContractExecutionIssue(
                    issue_code="CONTRACT_VALIDATION_RULE_UNSUPPORTED",
                    severity="WARNING",
                    field_key=rule.rule_id,
                    message=f"Contract validation rule '{rule.rule_id}' has no deterministic executor.",
                )
            )
            continue
        left = _rule_operand(parsed.group(1), fields)
        right = _rule_operand(parsed.group(3), fields)
        failed = (
            left is not None
            and right is not None
            and not _RULE_OPERATORS[parsed.group(2)](left, right)
        )
        if failed:
            issues.append(
                ContractExecutionIssue(
                    issue_code=rule.issue_code,
                    severity=rule.severity.value,
                    field_key=rule.rule_id,
                    message=rule.review_guidance,
                )
            )
    return tuple(issues)


def _row_eligibility_issues(
    fields: dict[str, Any],
    runtime_contract,
) -> tuple[ContractExecutionIssue, ...]:
    """Deterministic projection of the contract's prose eligibility rules.

    The declared rules are business prose and cannot be executed literally;
    what IS checkable is whether the row carried ANY source-mapped contract
    field at all. A row that mapped nothing (constants aside) is very unlikely
    to be a catalogue item — flag it against the declared rule for review.
    """

    rules = runtime_contract.declaration.source_structure.row_eligibility_rules
    if not rules:
        return ()
    if any(_text(value) is not None for key, value in fields.items() if key.startswith("source:")):
        return ()
    return (
        ContractExecutionIssue(
            issue_code="CONTRACT_ROW_NOT_ELIGIBLE",
            severity="WARNING",
            message=f"Row mapped no contract source fields; declared eligibility: {rules[0]}",
        ),
    )


def _supplier_identity_issues(
    observation: ExtractedEvidence,
    runtime_contract,
) -> tuple[ContractExecutionIssue, ...]:
    """Deterministic check against the page's captured supplier identity text.

    Extraction stamps ``supplier_identity_text`` (verbatim letterhead/footer
    text) onto every observation from a page where it was visible — see
    catalogue_evidence_extraction.py's VISION_EVIDENCE_PROMPT. This is
    intentionally a POSITIVE-evidence-only check: if the page carries no
    identity text at all (older evidence, or a page where none was printed),
    nothing is asserted either way — the row is neither confirmed nor
    rejected. It only fires when the page's captured text explicitly names
    something other than this contract's own declared supplier, which is a
    real, checkable mismatch, not a guess.
    """

    identity_text = (observation.source_metadata or {}).get("supplier_identity_text")
    if not identity_text:
        return ()
    supplier = runtime_contract.declaration.supplier
    # Every name this supplier prints, not only the one we file it under. A
    # company that renamed still sends documents on the old letterhead, and
    # reading those as another company's pages blocks a whole catalogue.
    own_names = [
        name
        for name in (supplier.supplier_name, supplier.supplier_code, *supplier.also_trades_as)
        if name
    ]
    if not own_names:
        return ()
    if any(_identity_names_overlap(identity_text, name) for name in own_names):
        return ()
    return (
        ContractExecutionIssue(
            issue_code="CONTRACT_SUPPLIER_IDENTITY_MISMATCH",
            severity="BLOCKING",
            message=(
                f"Page identity text {identity_text!r} does not name this contract's "
                f"declared supplier ({supplier.supplier_name!r}) — this row is likely from "
                "a different supplier's pages in a multi-supplier source."
            ),
        ),
    )


#: Corporate boilerplate that never identifies a company on its own. Short
#: tokens (< 4 chars) are excluded separately — "Pet Shop Ltd" must not vouch
#: for "Kangaroo Pet Nutrition" on the strength of 'pet'.
_IDENTITY_STOPWORDS = frozenset({
    "and", "co", "company", "corp", "corporation", "enterprise", "enterprises",
    "group", "holding", "holdings", "inc", "incorporated", "international",
    "limited", "ltd", "the",
    # What the company SELLS is not who it is. Half this trade is called
    # "<something> Pet Nutrition" — 'nutrition' alone let "Hill's Pet
    # Nutrition" vouch for "Kangaroo Pet Nutrition", which is the exact
    # confusion this check exists to catch.
    "distribution", "distributors", "medical", "nutrition", "pharma",
    "pharmaceutical", "pharmaceuticals", "products", "supplies", "supply",
    "trading", "veterinary",
})


def _identity_names_overlap(left: str, right: str) -> bool:
    """Match printed supplier-identity text against a contract's declared name/code.

    Deliberately its OWN normalization, not _names_overlap()/_fold() (used for
    column-heading matching everywhere else) — abbreviated company names vary
    in punctuation across extraction passes in ways column headings don't
    (e.g. "K.P.N. Trading" declared vs. "K.P.N Trading Ltd." as printed/OCR'd,
    same company, one missing period). Stripping periods here would be too
    aggressive for column-heading matching (headers occasionally rely on
    exact punctuation) but is exactly right for a company name.

    Three ways to match, each covering a real print-vs-declaration drift:
    folded containment ("K.P.N Trading Ltd." printed, one period dropped);
    COMPACT containment with every separator removed ("KPN Trading" printed
    against "K.P.N. Trading" declared); and a shared distinctive token — the
    Vetapet letterhead prints "C. VETAPET & COMPANY" while the operations
    record is "Vetapet Vet", so neither fold contains the other, but
    'vetapet' names the company on both sides. Distinctive means four or more
    characters and not corporate boilerplate; the looseness this buys fails
    SAFE — a missed mismatch suppresses an issue, never blocks a row — while
    the routing case the check exists for (K.P.N. pages versus Kangaroo Pet
    Nutrition pages) still fires on every form either company prints.
    """

    def _identity_fold(value: str) -> str:
        return re.sub(r"[.\s/|'&,()-]+", " ", value).strip().lower()

    def _compact(value: str) -> str:
        return re.sub(r"[^0-9a-z⺀-鿿豈-﫿]+", "", value.lower())

    def _tokens(value: str) -> set[str]:
        return {
            token
            for token in _identity_fold(value).split()
            if len(token) >= 4 and token not in _IDENTITY_STOPWORDS and not token.isdigit()
        }

    for left_key in (_identity_fold(left), _english_fold(left)):
        for right_key in (_identity_fold(right), _english_fold(right)):
            if left_key and right_key and (left_key in right_key or right_key in left_key):
                return True
    left_compact, right_compact = _compact(left), _compact(right)
    if left_compact and right_compact and (left_compact in right_compact or right_compact in left_compact):
        return True
    return bool(_tokens(left) & _tokens(right))


def _carry_merged_cells(
    fields: dict[str, Any],
    inheritable: tuple[str, ...],
    carried: dict[str, str],
    runtime_contract,
) -> tuple[str, ...]:
    """Fill fields the source left blank because the printed cell is merged.

    A supplier that lists size variants under one heading — "ALOVEEN Shampoo"
    naming the 250ml line, the 1L line below carrying only its own code, pack
    and price — is not omitting the name; the cell spans both rows. Those are
    separate SKUs and have to be stocked as such, so the value carries down.

    Only a row with its own identity inherits. The price-only lines beneath a
    product are quantity tiers, and a tier that acquired a name would look
    exactly like a product that does not exist.

    Returns the field keys that were inherited, so provenance can say so.
    """
    continued = _complete_continuations(fields, runtime_contract, carried)
    if not inheritable:
        return continued
    identity_fields = runtime_contract.declaration.source_structure.row_identity_fields
    has_identity = any(_text(fields.get(f"source:{key}")) is not None for key in identity_fields)
    inherited: list[str] = []
    for key in inheritable:
        own = _text(fields.get(f"source:{key}"))
        if own is not None:
            carried[key] = own
            continue
        if not has_identity or key not in carried:
            continue
        contract_field = next(
            (f for f in runtime_contract.declaration.fields if f.field_key == key), None
        )
        fields[f"source:{key}"] = carried[key]
        target = _role_target(contract_field.role) if contract_field else None
        if target:
            fields.setdefault(target, carried[key])
        inherited.append(key)
    return tuple(inherited) + continued


def _complete_continuations(
    fields: dict[str, Any],
    runtime_contract,
    carried: dict[str, str],
) -> tuple[str, ...]:
    """Complete a value that continues the row above instead of standing alone.

    A supplier names a family once and then lists only what varies:

        273310  Classic Collar size 7.5cm
        273320  size 10.0cm
        273325  size 12.5cm

    Those are separate SKUs at separate prices, and "size 10.0cm" does not say
    what they are. The text before the row above's own match supplies the rest.

    Nothing is invented. If the row above does not match the same pattern there
    is no prefix to derive, and the value stays exactly as printed.
    """
    completed: list[str] = []
    for contract_field in runtime_contract.declaration.fields:
        pattern = getattr(contract_field, "continues_row_above_when_matching", None)
        if not pattern:
            continue
        key = contract_field.field_key
        value = _text(fields.get(f"source:{key}"))
        if value is None:
            continue
        expression = re.compile(pattern, re.IGNORECASE)
        if not expression.match(value):
            carried[key] = value
            continue
        # The declared pattern is anchored, because a continuation is only a
        # continuation when it BEGINS that way. Locating the same marker inside
        # the row above needs the unanchored form: "Classic Collar size 7.5cm"
        # carries "size" in the middle, which is exactly the split point.
        previous = carried.get(key)
        loose = re.compile(pattern.lstrip("^"), re.IGNORECASE)
        match = loose.search(previous) if previous else None
        if match is None or match.start() == 0:
            continue
        completed_value = f"{previous[:match.start()].strip()} {value}"
        fields[f"source:{key}"] = completed_value
        target = _role_target(contract_field.role)
        if target:
            fields[target] = completed_value
        # The next fragment derives from the completed name, not the fragment.
        carried[key] = completed_value
        completed.append(key)
    return tuple(completed)


# "11+1", "9 + 3" — buy the first number, receive the second free.
_FREE_GOODS = re.compile(r"(\d+)\s*\+\s*(\d+)")


def _tier_row_term(
    fields: dict[str, Any],
    runtime_contract,
    items: list[ConformedRow],
    parent_index: int | None,
    raw_observation_id: UUID,
    observation: ExtractedEvidence,
) -> tuple[int, dict[str, Any], dict[str, Any] | None] | None:
    """A bulk tier the supplier printed as its own row, or None.

    Alfamedic prints the ladder beneath the product:

        ALO250  ALOVEEN Shampoo   1 bot     58.0
        (none)                    10 bots   56.0
        (none)                    40 bots   54.0

    Both halves are on the page — buy 10, pay 56.00 each — so the term is
    fully determined and nothing is inferred. Returns the index of the product
    it belongs to and the term to hang on it.

    The identity cell is merged across the tier lines, and a vision model
    renders that two ways on the same document — sometimes leaving the cell
    blank, sometimes repeating the product's code down the block:

        ALO250  ALOVEEN Shampoo  10 bots  56.0     (repeated)
        (none)                   10 bots  56.0     (blank)

    Both are the same printed table, so both are tiers. Accepting only the
    blank form left the repeated form to become duplicate products — 174 extra
    candidates on one live run, the same SKU three times at three prices.

    Every other condition is load-bearing. The row must be cheaper than what it
    discounts (a tier at the same price is not a discount, and would corrupt
    every downstream cost); it must ask for more than one; and it must directly
    follow that product on the SAME page, because the last row of page 20 has
    nothing to do with the first row of page 21.
    """
    declared = next(
        (f for f in runtime_contract.declaration.fields
         if getattr(f.role, "value", f.role) == "MBB_TIER_ROW"),
        None,
    )
    if declared is None or parent_index is None:
        return None
    identity_fields = runtime_contract.declaration.source_structure.row_identity_fields
    parent = items[parent_index] if parent_index < len(items) else None
    if parent is None:
        return None
    own_identity = [_text(fields.get(f"source:{key}")) for key in identity_fields]
    parent_identity = [
        _text((parent.raw_fields.get("additional_fields") or {}).get(key)) for key in identity_fields
    ]
    if any(v is not None for v in own_identity) and own_identity != parent_identity:
        # A different code is a different product — even a nameless size
        # variant. Swallowing one would lose a stocked SKU.
        return None

    raw_quantity = fields.get(f"source:{declared.tier_quantity_field}")
    price = _decimal_or_none(fields.get(f"source:{declared.tier_price_field}"))
    # "11+1 bots" is buy eleven, take twelve. The benefit is a free unit rather
    # than a cheaper one, so the price column is empty on purpose — reading
    # that as a missing price sent 17 real offers to review as defective rows.
    free_goods = _FREE_GOODS.search(str(raw_quantity or ""))
    quantity = _leading_decimal(raw_quantity)
    if quantity is None or quantity <= 1:
        return None
    if free_goods is None and (price is None or price <= 0):
        return None

    base = _decimal_or_none((parent.normalized_fields.get("cost") or {}).get("amount"))
    if free_goods is None and base is not None and price >= base:
        return None

    pricing = runtime_contract.declaration.pricing
    basis = pricing.price_basis
    if basis is None or basis.code is None:
        return None
    evidence = {
        "raw_observation_id": str(raw_observation_id),
        "field_path": "/raw_cells",
        "confidence": _confidence_text(fields.get("confidence"), observation.confidence),
    }
    if free_goods is not None:
        benefit = {
            "benefit_type": "free_quantity",
            "quantity": {"amount": str(Decimal(free_goods.group(2))), "uom": basis.model_dump(mode="json")},
        }
    else:
        benefit = {
            "benefit_type": "discounted_unit_price",
            "discounted_price": {
                "amount": str(price),
                "currency": pricing.currency,
                "price_basis": basis.model_dump(mode="json"),
            },
        }
    term = {
        "mbb_term_id": str(_stable_term_uuid(evidence, declared.field_key)),
        # The quantity is of THIS product, unlike Hill's order-value tiers.
        "scope": "SUPPLIER_SKU",
        "condition": {
            "condition_type": "minimum_quantity",
            "quantity": {"amount": str(quantity), "uom": basis.model_dump(mode="json")},
        },
        "benefit": benefit,
        "description": declared.description,
        "evidence": evidence,
    }
    lifted_cost = None
    if free_goods is not None and price is not None and price > 0:
        lifted_cost = _cost_proposal(
            fields.get(f"source:{declared.tier_price_field}"), runtime_contract, evidence, fields
        )
    return parent_index, term, lifted_cost


# The first number after a declared ladder prefix is the rung's lower bound:
# 'PRICE: 50 doses or above (per unit)' -> 50; 'PRICE PER ORDER QTY: 1-19' -> 1.
_LADDER_LOWER_BOUND = re.compile(r"(\d+(?:\.\d+)?)")


def _quantity_ladder_terms(
    ladder: dict[str, Any] | None,
    cost: dict[str, Any] | None,
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    """Deeper ladder rungs as minimum-quantity discounted-price terms."""
    if not ladder or cost is None:
        return []
    base = _decimal_or_none(ladder.get("base_amount"))
    terms: list[dict[str, Any]] = []
    for qty, amount in ladder.get("tiers") or []:
        rate = _decimal_or_none(amount)
        if rate is None or (base is not None and rate >= base):
            # A deeper rung that is not cheaper is not a discount.
            continue
        terms.append({
            "mbb_term_id": str(_stable_term_uuid(evidence, f"quantity_ladder:{qty}")),
            "scope": "SUPPLIER_SKU",
            "condition": {
                "condition_type": "minimum_quantity",
                "quantity": {"amount": qty, "uom": cost["price_basis"]},
            },
            "benefit": {
                "benefit_type": "discounted_unit_price",
                "discounted_price": {
                    "amount": str(rate),
                    "currency": cost["currency"],
                    "price_basis": cost["price_basis"],
                },
            },
            "description": None,
            "evidence": evidence,
        })
    return terms


def _apply_quantity_ladder(
    fields: dict[str, Any],
    observation: ExtractedEvidence,
    runtime_contract,
) -> None:
    """Resolve quantity-banded price COLUMNS into the row's cost slot.

    Vetapet's indent-order sections price whole rows as a ladder — 'PRICE:
    50 doses or above', 'PRICE: 100 doses or above' — with no unconditional
    price anywhere. Ruling 2026-08-17: the LOWEST filled rung IS the base
    cost and its lower bound is the minimum order; deeper rungs are deals.

    Only contracts that declare mbb.quantity_ladder_heading_prefixes
    participate, and only when the ordinary cost field resolved nothing —
    a printed plain price always wins over ladder interpretation. The rung
    chosen and the deeper rungs are stashed for _item_from_fields, which
    pins the basis, injects the minimum order, and emits the terms.
    """
    prefixes = [p.casefold() for p in runtime_contract.declaration.mbb.quantity_ladder_heading_prefixes]
    if not prefixes:
        return
    cost_field = runtime_contract.declaration.pricing.cost_source_field
    if not cost_field or _text(fields.get(f"source:{cost_field}")) is not None:
        return
    rungs: list[tuple[Decimal, Decimal, str, bool]] = []
    for cell in observation.raw_cells:
        heading = (cell.column_name or "").strip()
        folded = heading.casefold()
        match_prefix = next((p for p in prefixes if folded.startswith(p)), None)
        if match_prefix is None:
            continue
        remainder = heading[len(match_prefix):]
        bound = _LADDER_LOWER_BOUND.search(remainder)
        amount = _decimal_value(cell.raw_value)
        if bound is None or amount is None or amount <= 0:
            continue
        rungs.append((Decimal(bound.group(1)), amount, str(cell.raw_value), "per unit" in folded))
    if not rungs:
        return
    rungs.sort(key=lambda r: r[0])
    base_qty, base_amount, base_raw, per_unit = rungs[0]
    fields[f"source:{cost_field}"] = base_raw
    fields.setdefault("cost_price", base_raw)
    fields["__quantity_ladder__"] = {
        "minimum_order": str(base_qty) if base_qty > 1 else None,
        "per_unit_basis": per_unit,
        "base_amount": str(base_amount),
        "tiers": [(str(qty), str(amount)) for qty, amount, _raw, _pu in rungs[1:]],
    }


def _struck_price_offer(
    fields: dict[str, Any],
    runtime_contract,
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The regular price plus a Special-Offer term from a struck-price cell, or None.

    Vet pages print 'Special Offer: N% off' bands with the original price
    struck through and a badge price beside it; the extracted cell then
    carries both amounts unmarked ('$739 HK$1056.0'). For every contract that
    has NOT declared this render, such a cell keeps refusing to parse — two
    unmarked prices are a guess — and the row dead-letters for a person.

    A contract that HAS declared it (mbb.struck_price_offer_source_field) has
    stated which field prints that render, and then nothing is guessed: a
    discount's offer price is necessarily the smaller amount, so the larger is
    the row's regular cost and the smaller becomes an unconditional
    (minimum quantity 1) discounted-price term. Anything but exactly two
    distinct amounts still refuses — equal amounts are no discount, and three
    are a misread.
    """
    offer_field = runtime_contract.declaration.mbb.struck_price_offer_source_field
    if not offer_field:
        return None
    raw = _source_field_value(fields, offer_field)
    amounts = _money_amounts(raw)
    if len(amounts) != 2 or amounts[0] == amounts[1]:
        return None
    offer, original = min(amounts), max(amounts)
    if offer <= 0:
        return None
    cost = _cost_proposal(str(original), runtime_contract, evidence, fields)
    if cost is None:
        return None
    term = {
        "mbb_term_id": str(_stable_term_uuid(evidence, f"{offer_field}:struck_offer")),
        "scope": "SUPPLIER_SKU",
        # Quantity one: the printed offer asks nothing beyond buying it. The
        # existing condition vocabulary states that exactly.
        "condition": {
            "condition_type": "minimum_quantity",
            "quantity": {"amount": "1", "uom": cost["price_basis"]},
        },
        "benefit": {
            "benefit_type": "discounted_unit_price",
            "discounted_price": {
                "amount": str(offer),
                "currency": cost["currency"],
                "price_basis": cost["price_basis"],
            },
        },
        "description": raw,
        "evidence": evidence,
    }
    return cost, term


# The one band notation a real page has proven (Topizole TOP250): a closed
# 'N-M unit' range. Open ranges ('21+') stay unfolded until a page prints one.
_QUANTITY_BAND = re.compile(r"^\s*(\d+)\s*[-–—]\s*(\d+)\s+(.+?)\s*$")


def _quantity_band_term(
    fields: dict[str, Any],
    runtime_contract,
    items: list[ConformedRow],
    raw_observation_id: UUID,
    observation: ExtractedEvidence,
) -> tuple[int, dict[str, Any]] | None:
    """A deeper quantity band printed as its own same-code row, or None.

    Vetapet prints the ladder as full rows that repeat the code:

        TOP250  Topizole 5mg/ml ...  1-10 bottles   82.0
        TOP250  Topizole 5mg/ml ...  11-20 bottles  78.0

    Both halves are on the page — buy eleven, pay 78.00 per bottle — so the
    deeper row is a fully determined term on the first row's product, not a
    second candidate for the same SKU (whose later publication would supersede
    the base price with the deep-band price).

    Only contracts that declare mbb.quantity_band_source_field participate,
    and every guard refuses toward "two candidates for a person" rather than
    folding on a maybe: the earlier row must carry a parseable band of the
    same unit noun, the deeper range must start above the earlier range's
    end, both prices must resolve on the same basis, and the deeper price
    must be cheaper.
    """
    band_field = runtime_contract.declaration.mbb.quantity_band_source_field
    if not band_field:
        return None
    own_text = _text(fields.get(f"source:{band_field}"))
    own_band = _QUANTITY_BAND.match(own_text or "")
    if own_band is None:
        return None
    sku = _text(fields.get("supplier_sku"))
    if sku is None:
        return None
    base_index = next(
        (i for i in range(len(items) - 1, -1, -1) if items[i].raw_fields.get("supplier_sku") == sku),
        None,
    )
    if base_index is None:
        return None
    base = items[base_index]
    base_text = _text((base.raw_fields.get("additional_fields") or {}).get(band_field))
    base_band = _QUANTITY_BAND.match(base_text or "")
    if base_band is None:
        # A repeated code without band semantics is a duplicate to review,
        # not a ladder to fold.
        return None
    own_start = int(own_band.group(1))
    own_unit = own_band.group(3).strip().casefold()
    base_end = int(base_band.group(2))
    base_unit = base_band.group(3).strip().casefold()
    if own_unit != base_unit or own_start <= base_end:
        # A different noun or an overlapping/reordered range is a misread
        # page, and folding a misread would erase a stocked SKU.
        return None
    evidence = {
        "raw_observation_id": str(raw_observation_id),
        "field_path": "/raw_cells",
        "confidence": _confidence_text(fields.get("confidence"), observation.confidence),
    }
    cost = _cost_proposal(fields.get("cost_price"), runtime_contract, evidence, fields)
    base_cost = base.normalized_fields.get("cost") or {}
    base_amount = _decimal_or_none(base_cost.get("amount"))
    if cost is None or base_amount is None:
        return None
    if cost["price_basis"].get("code") != (base_cost.get("price_basis") or {}).get("code"):
        return None
    if Decimal(cost["amount"]) >= base_amount:
        # A band that is not cheaper is not a discount — leave both rows
        # standing for review rather than assert one.
        return None
    term = {
        "mbb_term_id": str(_stable_term_uuid(evidence, f"{band_field}:band:{own_start}")),
        # The quantity is of THIS product, same shapes as the tier-ROW path.
        "scope": "SUPPLIER_SKU",
        "condition": {
            "condition_type": "minimum_quantity",
            "quantity": {"amount": str(own_start), "uom": cost["price_basis"]},
        },
        "benefit": {
            "benefit_type": "discounted_unit_price",
            "discounted_price": {
                "amount": cost["amount"],
                "currency": cost["currency"],
                "price_basis": cost["price_basis"],
            },
        },
        "description": own_text,
        "evidence": evidence,
    }
    return base_index, term


def _is_discontinued(fields: dict[str, Any], runtime_contract) -> bool:
    """True when the supplier has marked this line as no longer sold.

    Alfamedic writes DISCON wherever there is room — in the product name
    ("Gentamycin 5% DISCON"), in the packing column, as the order code itself,
    and in the price column — so every mapped value is checked rather than one
    nominated field. Whole-word and case-insensitive: the live catalogue writes
    both DISCON and Discon.

    A withdrawn line has no price to find and no decision a reviewer can make,
    so queueing it only teaches people to skim the queue.
    """
    markers = runtime_contract.declaration.source_structure.discontinued_markers
    if not markers:
        return False
    pattern = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(m) for m in markers), re.IGNORECASE)
    return any(
        pattern.search(str(value))
        for key, value in fields.items()
        if key.startswith("source:") and value is not None
    )


def _is_ineligible_row(fields: dict[str, Any], runtime_contract) -> bool:
    """True when the row is part of the document, not part of the catalogue.

    A supplier price list is not only items: it has section titles, blank
    spacer rows and continuation lines, and a vision model returns them as
    table rows because that is what they look like. Blocking them puts furniture
    in a reviewer's queue — 38 of 52 blocked rows on one live Alfamedic run
    were exactly this, which is how a queue stops being read.

    The test is deliberately narrow. A row without its order code but WITH a
    price is not furniture; it is an item whose code we failed to read, and it
    still goes to review.
    """
    identity_fields = runtime_contract.declaration.source_structure.row_identity_fields
    if not identity_fields:
        return False
    if any(_text(fields.get(f"source:{key}")) is not None for key in identity_fields):
        return False
    # "With a price" means with a price under ANY declared price column — a
    # mixed-layout contract prices per layout, and a case-priced row without
    # its code is still an item, not furniture.
    if _source_price_fields(runtime_contract):
        return _winning_price_field(runtime_contract, fields) is None
    price_field = runtime_contract.declaration.pricing.cost_source_field
    return price_field is None or _text(fields.get(f"source:{price_field}")) is None


def _required_field_issues(
    fields: dict[str, Any],
    runtime_contract,
) -> tuple[ContractExecutionIssue, ...]:
    issues: list[ContractExecutionIssue] = []
    for contract_field in runtime_contract.declaration.fields:
        if contract_field.requirement != SourceFieldRequirement.REQUIRED:
            continue
        if _text(fields.get(f"source:{contract_field.field_key}")) is None:
            issues.append(
                ContractExecutionIssue(
                    issue_code="CONTRACT_REQUIRED_FIELD_MISSING",
                    severity="BLOCKING",
                    field_key=contract_field.field_key,
                    message=f"Required contract field '{contract_field.field_key}' is missing from the source row.",
                )
            )
    # A mixed-layout contract declares one price column PER layout, each
    # OPTIONAL — no single column can be REQUIRED when only one of them prints
    # on any given row. The requirement is one-of: a row where NONE resolved
    # has no printed price this contract can read, and holds under the primary
    # column's name.
    price_fields = _source_price_fields(runtime_contract)
    if (
        len(price_fields) > 1
        and not any(f.requirement == SourceFieldRequirement.REQUIRED for f in price_fields)
        and all(_text(fields.get(f"source:{f.field_key}")) is None for f in price_fields)
    ):
        primary = runtime_contract.declaration.pricing.cost_source_field or price_fields[0].field_key
        issues.append(
            ContractExecutionIssue(
                issue_code="CONTRACT_REQUIRED_FIELD_MISSING",
                severity="BLOCKING",
                field_key=primary,
                message=(
                    f"Required contract field '{primary}' is missing from the source row — "
                    "none of the declared price columns is present."
                ),
            )
        )
    return tuple(issues)


@dataclass(frozen=True)
class AddableRequiredColumn:
    """A column the contract REQUIRES that this observation has no cell for.

    ``column_name`` is the heading a human-supplied cell must carry for
    ``_fields_from_cells`` to resolve it — the field's declared source column,
    falling back to its first alias. Fields read from page structure rather
    than a named heading (section banners, page brand marks, the unlabeled
    column) are never listed: a cell cannot stand in for those.
    """

    field_key: str
    column_name: str


def addable_required_columns(
    runtime_contract, raw_cells: list[dict]
) -> tuple[AddableRequiredColumn, ...]:
    """REQUIRED fields no cell of this observation can resolve.

    The HITL evidence panel offers exactly these for ADDING a value: a row
    dead-lettered as CONTRACT_REQUIRED_FIELD_MISSING because the scan never
    produced the cell is otherwise unfixable — corrections replace existing
    cells by name, and here there is no cell to replace. Presence is judged
    the way corrections match (any cell carrying the column name, whatever
    its value): an existing empty cell is edited in place, never added twice.
    """

    observed: set[str] = set()
    for cell in raw_cells or []:
        name = cell.get("column_name") if isinstance(cell, dict) else None
        if name:
            observed.update(_column_keys(str(name)))

    structural = {_SECTION_HEADER_SOURCE, _UNLABELED_COLUMN_SOURCE, _PAGE_BRAND_SOURCE}
    price_fields = _source_price_fields(runtime_contract)
    primary_price = runtime_contract.declaration.pricing.cost_source_field
    # A mixed-layout contract requires its price as ONE-OF several optional
    # columns. When no cell matches any of them, the primary column is the
    # honest one to offer for adding — it carries the primary declared basis,
    # and the audit trail names whoever typed the value.
    price_group_missing = len(price_fields) > 1 and not any(
        key in observed
        for f in price_fields
        for name in (f.source_column, f.source_path, *f.aliases)
        if name and name not in structural
        for key in _column_keys(name)
    )
    out: list[AddableRequiredColumn] = []
    offered: set[str] = set()
    for contract_field in runtime_contract.declaration.fields:
        required = contract_field.requirement == SourceFieldRequirement.REQUIRED
        if not required and not (price_group_missing and contract_field.field_key == primary_price):
            continue
        names = [
            name
            for name in (contract_field.source_column, contract_field.source_path, *contract_field.aliases)
            if name and name not in structural
        ]
        if not names:
            continue
        if any(key in observed for name in names for key in _column_keys(name)):
            continue
        if names[0] in offered:
            continue
        offered.add(names[0])
        out.append(AddableRequiredColumn(field_key=contract_field.field_key, column_name=names[0]))
    return tuple(out)


def _document_issues(
    observations: tuple[ExtractedEvidence, ...],
    runtime_contract,
) -> tuple[ContractExecutionIssue, ...]:
    structure = runtime_contract.declaration.source_structure
    observed_columns = {
        key
        for observation in observations
        for cell in observation.raw_cells
        if cell.column_name
        for key in _column_keys(cell.column_name)
    }
    issues: list[ContractExecutionIssue] = []
    for required_header in structure.required_headers:
        accepted_names = [required_header]
        for contract_field in runtime_contract.declaration.fields:
            declared_names = [contract_field.source_column, contract_field.source_path, *contract_field.composed_from]
            if any(declared and _names_overlap(declared, required_header) for declared in declared_names):
                accepted_names.extend(contract_field.aliases)
        if not any(key in observed_columns for name in accepted_names for key in _column_keys(name)):
            issues.append(
                ContractExecutionIssue(
                    issue_code="CONTRACT_REQUIRED_HEADER_MISSING",
                    severity="BLOCKING",
                    field_key=required_header,
                    message=f"Required source header '{required_header}' was not observed.",
                )
            )

    observed_sheets = {
        observation.source_location.sheet_name
        for observation in observations
        if observation.source_location.sheet_name
    }
    if structure.expected_sheet_names and observed_sheets:
        expected_keys = {key for name in structure.expected_sheet_names for key in _column_keys(name)}
        for sheet in sorted(observed_sheets):
            if not any(key in expected_keys for key in _column_keys(sheet)):
                issues.append(
                    ContractExecutionIssue(
                        issue_code="CONTRACT_UNEXPECTED_SHEET",
                        severity="WARNING",
                        field_key="sheet_name",
                        message=f"Source sheet '{sheet}' is not declared by the supplier contract.",
                    )
                )
    return tuple(issues)


def _names_overlap(left: str, right: str) -> bool:
    """Match a required header to a more descriptive declared bilingual header."""

    for left_key in _column_keys(left):
        for right_key in _column_keys(right):
            if left_key == right_key or left_key in right_key or right_key in left_key:
                return True
    return False


def _tier_price_terms(
    fields: dict[str, Any],
    runtime_contract,
    evidence: dict[str, Any],
    cost: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Typed MBB terms from priced tier columns.

    A supplier that prints "unit price if you spend at least X" against every
    row has stated a complete term — condition and benefit are both determined,
    so there is nothing to guess and nothing for a reviewer to interpret. That
    is why these do not go through the MBB_TEXT path, which exists to preserve
    prose whose shape nobody has proven.

    The threshold comes from the contract, not the column heading: on the live
    Hill's run the heading kept "MOV $1,200" on 36 of 174 priced rows and was
    truncated to "Net Invoice Price*" on the rest, so reading it would have
    produced terms for a fifth of the catalogue and silence for the others.
    """
    pricing = runtime_contract.declaration.pricing
    basis = pricing.price_basis
    if basis is None or basis.code is None:
        return []
    gross = _decimal_or_none((cost or {}).get("amount"))

    tiers = [
        field
        for field in runtime_contract.declaration.fields
        if getattr(field.role, "value", field.role) == "MBB_TIER_PRICE"
        and (field.tier_minimum_spend is not None or field.tier_quantity_field)
    ]
    terms: list[dict[str, Any]] = []
    for tier in sorted(tiers, key=lambda f: (f.tier_order or 0, str(f.tier_minimum_spend))):
        if tier.tier_minimum_spend is not None:
            amount = _decimal_or_none(fields.get(f"source:{tier.field_key}"))
            if amount is None or amount <= 0:
                continue
            # A tier that is not cheaper than the gross price is not a discount. It
            # happens on rows the supplier prints the same number across, and a term
            # asserting "spend 4,500 to pay the same" would be noise in every
            # downstream cost calculation.
            if gross is not None and amount >= gross:
                continue
            terms.append({
                "mbb_term_id": str(_stable_term_uuid(evidence, tier.field_key)),
                # SUPPLIER_ORDER, not SUPPLIER_SKU: the condition is a minimum
                # value across the whole order, even though the discounted price it
                # unlocks belongs to this row. The scope vocabulary already had the
                # word for this.
                "scope": "SUPPLIER_ORDER",
                "condition": {
                    "condition_type": "minimum_spend",
                    "spend": {"amount": str(tier.tier_minimum_spend), "currency": pricing.currency},
                },
                "benefit": {
                    "benefit_type": "discounted_unit_price",
                    "discounted_price": {
                        "amount": str(amount),
                        "currency": pricing.currency,
                        "price_basis": basis.model_dump(mode="json"),
                    },
                },
                "description": tier.description,
                "evidence": evidence,
            })
            continue

        # Quantity-conditioned same-row tier: a case/bulk price printed as an
        # extra column ON the product's own row (K.P.N. Trading's '批發價 每箱
        # (平均每包價)'), unlocked by buying the quantity another declared field
        # states (units per case). Both halves are printed on the row, so the
        # term is as fully determined as Hill's spend ladder.
        quantity = _counted_quantity(fields.get(f"source:{tier.tier_quantity_field}"))
        if quantity is None or quantity <= 1:
            continue
        amounts = _money_amounts(fields.get(f"source:{tier.field_key}"))
        if not amounts:
            continue
        # A compound cell prints the case TOTAL and its printed per-unit average
        # in one string ('$1094/箱 ($182/包)'). The per-unit rate is necessarily
        # the smaller amount when a bundle costs more than one unit — taking
        # min() selects the printed per-unit figure without computing anything.
        # A cell carrying ONLY a bundle total (no printed per-unit rate) yields
        # an "amount" at or above the gross unit price and is refused below —
        # deriving the rate by division would invent a value the source never
        # printed, which this module does not do.
        amount = min(amounts)
        if amount <= 0:
            continue
        if gross is not None and amount >= gross:
            continue
        terms.append({
            "mbb_term_id": str(_stable_term_uuid(evidence, tier.field_key)),
            # The quantity is of THIS product — same scope and shapes as the
            # tier-ROW path, because it is the same kind of term printed in a
            # different place on the page.
            "scope": "SUPPLIER_SKU",
            "condition": {
                "condition_type": "minimum_quantity",
                "quantity": {"amount": str(quantity), "uom": basis.model_dump(mode="json")},
            },
            "benefit": {
                "benefit_type": "discounted_unit_price",
                "discounted_price": {
                    "amount": str(amount),
                    "currency": pricing.currency,
                    "price_basis": basis.model_dump(mode="json"),
                },
            },
            "description": tier.description,
            "evidence": evidence,
        })
    return terms


# The two page-banner promotion notations a contract may declare. Strict on
# purpose: a banner that matches neither stays evidence, never a guessed term.
_PAGE_PROMO_SPEND_PERCENT = re.compile(
    r"mix\s+over\s+\$?\s*([\d,]+(?:\.\d+)?)\s*[,，]?\s*(\d+(?:\.\d+)?)\s*%\s*off",
    re.IGNORECASE,
)
_PAGE_PROMO_ZHE_TIER = re.compile(r"(\d+)\s*件\s*(\d{1,2}(?:\.\d)?)\s*折")


def _page_promotion_terms(
    observation: ExtractedEvidence,
    runtime_contract,
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    """Order-scoped terms from the page's promotion banner.

    Extraction stamps ``page_promotion_text`` (verbatim banner) onto every
    observation of the page; a contract that DECLARES the banner's notation in
    ``mbb.page_promotion_shapes`` gets it parsed into typed terms on each row.
    Undeclared contracts and unrecognised banners produce nothing — the text
    remains page evidence, which is exactly what it was before this existed.

    折 arithmetic: 9折 means "pay 90%%", so the discount is (10 - 折) × 10
    percent — 9折 → 10%% off, 8.5折 → 15%% off.
    """
    shapes = tuple(runtime_contract.declaration.mbb.page_promotion_shapes)
    if not shapes:
        return []
    text = _text((observation.source_metadata or {}).get("page_promotion_text"))
    if not text:
        return []
    currency = runtime_contract.declaration.pricing.currency
    terms: list[dict[str, Any]] = []

    def _term(index: int, condition: dict[str, Any], percentage: str) -> dict[str, Any]:
        seed = f"{evidence.get('raw_observation_id')}:page-promo:{index}"
        return {
            "mbb_term_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"rosetta:mbb-page-promo:{seed}")),
            # The banner speaks about the ORDER ("mix over…", "mix 12 items"),
            # not this row alone — the same scope word Hill's spend ladder uses.
            "scope": "SUPPLIER_ORDER",
            "condition": condition,
            "benefit": {"benefit_type": "percentage_discount", "percentage": percentage},
            "description": text,
            "evidence": evidence,
        }

    index = 0
    if "mix_over_spend_percent" in shapes:
        for match in _PAGE_PROMO_SPEND_PERCENT.finditer(text):
            amount = match.group(1).replace(",", "")
            terms.append(_term(
                index,
                {"condition_type": "minimum_spend", "spend": {"amount": amount, "currency": currency}},
                match.group(2),
            ))
            index += 1
    if "mixed_quantity_percent_tiers" in shapes:
        for match in _PAGE_PROMO_ZHE_TIER.finditer(text):
            # 折 permits one decimal digit, so the percentage is always an
            # integer; quantize keeps it out of Decimal's 1E+1 rendering.
            percent = Decimal(100) - Decimal(match.group(2)) * 10
            if percent <= 0 or percent >= 100:
                continue
            terms.append(_term(
                index,
                {"condition_type": "minimum_quantity",
                 "quantity": {"amount": match.group(1), "uom": {"code": "UNIT", "label": None}}},
                str(percent.quantize(Decimal("1"))),
            ))
            index += 1
    return terms


def _stable_term_uuid(evidence: dict[str, Any], field_key: str) -> uuid.UUID:
    """Deterministic per observation + tier, so a re-parse reuses identities."""
    seed = f"{evidence.get('raw_observation_id')}:{field_key}"
    return uuid.uuid5(uuid.NAMESPACE_URL, f"rosetta:mbb-tier:{seed}")


def _mapped_value(contract_field, value: Any) -> Any:
    """Translate a source's own spelling into ours, where the contract declares it.

    For a column whose values are an enumerated set the supplier writes in its
    own words — AVM prints "Canine + Feline" where we file "both". Declared
    spellings only, matched case-insensitively on the trimmed text.

    Lenient on purpose: a value the map does not cover passes through verbatim.
    An unrecognised species is still evidence and a reviewer can read it, which
    is not true of a price basis — there an unmapped unit must hold the row,
    and that is enforced separately by the pricing semantics.
    """
    mapping = getattr(contract_field, "value_map", None)
    if not mapping:
        return value
    text = _text(value)
    if text is None:
        return value
    needle = text.strip().casefold()
    for spelling, ours in mapping.items():
        if str(spelling).strip().casefold() == needle:
            return ours
    return value


def _read_purchase_unit(fields: dict[str, Any], semantics) -> str | None:
    """The per-row purchase unit, from the declared field or the packing text.

    Only for contracts that opted into per-row units (purchase_uom_source_field
    declared). When that field is absent or unreadable on a row, the packing
    text is the same fact written elsewhere — Vetapet's PARASITE CONTROL table
    has no ORDER UNIT column, but '3 tubes / pack' names the unit after the
    slash exactly the way Alfamedic's rows do. A packing text whose slash-unit
    the vocabulary does not know ('35 cm/length') still resolves to nothing.
    """
    if not semantics.purchase_uom_source_field:
        return None
    read = _purchase_unit_from_text(_source_field_value(fields, semantics.purchase_uom_source_field))
    if read:
        return read
    if semantics.packaging_source_field and semantics.packaging_source_field != semantics.purchase_uom_source_field:
        return _purchase_unit_from_text(_source_field_value(fields, semantics.packaging_source_field))
    return None


def _source_price_fields(runtime_contract) -> list:
    return [
        contract_field
        for contract_field in getattr(runtime_contract.declaration, "fields", ()) or ()
        if getattr(contract_field.role, "value", contract_field.role) == "SOURCE_PRICE"
    ]


def _winning_price_field(runtime_contract, fields: dict[str, Any] | None):
    """The SOURCE_PRICE field whose value filled the cost slot.

    Every SOURCE_PRICE field funnels into `cost_price` via setdefault in
    declaration order, so the first declared field with a value IS the one
    whose amount the row carries — and, for mixed-layout documents, the one
    whose declared basis the amount is printed on.
    """
    if fields is None:
        return None
    for contract_field in _source_price_fields(runtime_contract):
        if _text(fields.get(f"source:{contract_field.field_key}")) is not None:
            return contract_field
    return None


#: A price basis printed as a plain quantity — "100’s", "50’s", "1,000's".
#: The possessive form is REQUIRED, not optional: it is how this source writes
#: a count on every one of them, and demanding it keeps a bare number out. A
#: suture gauge ("2\"", "0 (1.5) 45cm DS19") and a genuine fraction ("3/8") sit
#: in the same position and must never be mistaken for a quantity of goods.
_BARE_COUNT = re.compile(r"\d[\d,]*\s*[’']\s*s", re.IGNORECASE)


def _row_stated_cost_basis(runtime_contract, fields: dict[str, Any] | None):
    """The basis THIS ROW states, when the contract says a column names it.

    Royal Canin's webshop prints one price beside a unit column reading UNIT or
    INNER BOX, so the basis is a property of the row, not of the column or the
    document. Only declared spellings resolve: an unmapped or missing unit
    yields nothing, and the row is held as CONTRACT_PRICE_BASIS_UNRESOLVED
    rather than being priced on a guess.
    """
    pricing = runtime_contract.declaration.pricing
    source_field = pricing.price_basis_source_field
    if not source_field:
        return None
    # Declared fields are addressable by their own key under the `source:`
    # prefix, whatever role they carry — the basis column is a plain OTHER
    # field, so its value never lands in a role slot.
    raw = _text((fields or {}).get(f"source:{source_field}"))
    if raw is None:
        return None
    mapped = None
    needle = raw.strip().casefold()
    for spelling, code in (pricing.price_basis_value_map or {}).items():
        if str(spelling).strip().casefold() == needle:
            mapped = code
            break
    if mapped is None and pricing.price_basis_is_suffix_of_price:
        # United Italian print the basis INSIDE the price — "$46.00 / bag",
        # "$205.00 / box". Only reached when the whole value did not match, so a
        # source whose basis column already answers outright is untouched; and
        # the declared map still governs, so this widens WHERE we look, never
        # WHAT we accept.
        head = _first_price_and_basis(raw)
        suffix = (head[1] if head else "").casefold()
        for spelling, code in (pricing.price_basis_value_map or {}).items():
            if str(spelling).strip().casefold() == suffix:
                mapped = code
                raw = suffix
                break
        if mapped is None and pricing.price_basis_count_unit:
            # "$78.00 / 100's" — a quantity, and no vessel named. The contract
            # says what a bare count stands for; nothing is inferred here.
            if _BARE_COUNT.fullmatch(suffix):
                mapped = pricing.price_basis_count_unit
                raw = suffix
    if mapped is None:
        return None
    # A label is only permitted alongside OTHER; for a known code the source's
    # own spelling is already preserved on the row's evidence.
    if str(mapped).upper() == UnitCode.OTHER.value:
        return UnitOfMeasure(code=UnitCode.OTHER, label=raw.strip())
    return UnitOfMeasure(code=mapped)


def _declared_cost_basis(runtime_contract, fields: dict[str, Any] | None):
    """The basis the row's cost amount is declared on.

    Most specific first: the basis this ROW states, then the winning price
    column's own basis, then the contract-level one.
    """
    stated = _row_stated_cost_basis(runtime_contract, fields)
    if stated is not None:
        return stated
    winner = _winning_price_field(runtime_contract, fields)
    if winner is not None and winner.price_basis is not None:
        return winner.price_basis
    return runtime_contract.declaration.pricing.price_basis


def _cost_proposal(value: Any, runtime_contract, evidence: dict[str, Any], fields: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if _matches_null_marker(value, runtime_contract.declaration.pricing.null_cost_markers):
        return None
    pricing = runtime_contract.declaration.pricing
    amount = _cost_amount(value, pricing, non_negative=True)
    basis = _declared_cost_basis(runtime_contract, fields)
    packaging = runtime_contract.declaration.packaging
    if fields is not None and packaging.price_basis_follows_purchase_unit:
        # $1,390 is per BOTTLE, not per piece. Leaving it as a fixed basis
        # divides every per-unit cost by the wrong denominator.
        read_unit = _read_purchase_unit(fields, packaging)
        if read_unit:
            basis = UnitOfMeasure(code=UnitCode(read_unit))
    if amount is None or basis is None or basis.code is None:
        return None
    return {
        "amount": str(amount),
        # Currency is a CONTRACT declaration, never a conformance default.
        "currency": pricing.currency,
        "price_basis": basis.model_dump(mode="json"),
        "evidence": evidence,
    }


def _cost_issue_field_key(runtime_contract, fields: dict[str, Any] | None) -> str | None:
    """Name the price column the row actually carried, falling back to the
    primary — so a held row's issue points at the cell a person would fix."""
    winner = _winning_price_field(runtime_contract, fields)
    if winner is not None:
        return winner.field_key
    return runtime_contract.declaration.pricing.cost_source_field


def _matches_null_marker(value: Any, markers: list[str]) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return any(marker.strip().lower() in lowered for marker in markers if marker.strip())


# What a supplier writes after the slash in its packing text, and the unit that
# is. Only units the vocabulary already knows appear here: a purchase unit that
# is wrong silently rebases every per-unit cost derived from it, so an unknown
# word is left null for a human rather than mapped to something close.
_PURCHASE_UNIT_WORDS = {
    "box": "BOX", "boxes": "BOX",
    "bot": "BOTTLE", "bots": "BOTTLE", "bottle": "BOTTLE", "bottles": "BOTTLE",
    "pack": "PACK", "packs": "PACK", "pkt": "PACK",
    "tube": "TUBE", "tubes": "TUBE",
    "bag": "BAG", "bags": "BAG",
    "vial": "VIAL", "vials": "VIAL",
    "can": "CAN", "cans": "CAN",
    "sachet": "SACHET", "sachets": "SACHET",
    "case": "CASE", "carton": "CARTON", "ctn": "CARTON",
    "pc": "PIECE", "pcs": "PIECE", "piece": "PIECE", "pieces": "PIECE",
}


def _purchase_unit_from_text(value: Any) -> str | None:
    """The purchase unit named after the slash, or as bare unit text.

    Alfamedic writes '30ml/ bot' (the unit after the slash); Vetapet's vet
    section prints a dedicated ORDER UNIT column with bare '1 box' / '1 pc'.
    Both forms name the unit explicitly, so both are read. Units vary row by
    row — box, bot, tube, pot, reel — so this cannot be a single declared
    value. 'pot', 'reel', 'roll' and 'set' are deliberately absent from the
    vocabulary: the enum has no honest home for them, and OTHER would assert
    knowledge nobody has — an unknown word resolves to None for a human, and
    unit-less text ('30ml', '250 g') never names a purchase unit at all.
    """
    text = _text(value)
    if not text:
        return None
    candidate = text.rsplit("/", 1)[1] if "/" in text else text
    word = re.sub(r"[^a-z]+", " ", candidate.lower()).strip()
    return _PURCHASE_UNIT_WORDS.get(word) or _PURCHASE_UNIT_WORDS.get(word.split(" ")[0] if word else "")


# Countable things only. A measure — ml, g, oz — printed before the slash is how
# MUCH is in the container, not what you sell one of: "10ml/ bot" sells a bottle.
# That reading belongs to content_measure, which has its own source field, so
# admitting measures here would assert that a supplier sells millilitres.
_SELLABLE_UNIT_WORDS = {
    **_PURCHASE_UNIT_WORDS,
    "tab": "TABLET", "tabs": "TABLET", "tablet": "TABLET", "tablets": "TABLET",
    "cap": "CAPSULE", "caps": "CAPSULE", "capsule": "CAPSULE", "capsules": "CAPSULE",
    "test": "TEST", "tests": "TEST",
    "strip": "STRIP", "strips": "STRIP",
    "pouch": "POUCH", "pouches": "POUCH",
}


def _sellable_unit_from_text(value: Any) -> str | None:
    """The countable unit named BEFORE the slash: '100 tabs/ box' -> TABLET.

    Countables only, on purpose. A measure names the CONTENT of the unit, not
    the unit — '30ml/ bot' says how much is in the bottle — and the same sheet
    prints '100ml/ bot' on rows sold by the bottle, so reading measures here
    would assert a sellable unit the row never named. A measure returns None.

    The mirror of ``_purchase_unit_from_text``. Conformance already reads the
    count on this side of the slash — ``sellable_units_per_purchase_unit`` comes
    from the leading number of exactly this text — and then discards the noun
    standing next to it, so a row that plainly says "100 tabs/ box" resolves 100
    and BOX and leaves the sellable unit null. That gap reaches the sheet export
    as "100 / BOX", missing the word a human wrote.

    Only a unit the vocabulary actually knows is returned. 'set', 'pot' and
    'reel' stay absent for the same reason they are absent from the purchase
    vocabulary: the enum has no honest home for them, and guessing OTHER would
    assert knowledge nobody has.
    """
    text = _text(value)
    if not text:
        return None
    head = text.rsplit("/", 1)[0] if "/" in text else text
    # Drop the leading count — "100 tabs" is the same unit as "tabs".
    head = re.sub(r"^\s*\d+(?:\.\d+)?\s*", "", head.lower())
    word = re.sub(r"[^a-z]+", " ", head).strip()
    if not word:
        return None
    return _SELLABLE_UNIT_WORDS.get(word) or _SELLABLE_UNIT_WORDS.get(word.split(" ")[0])


def _packaging_proposal(fields: dict[str, Any], runtime_contract, evidence: dict[str, Any]) -> dict[str, Any] | None:
    semantics = runtime_contract.declaration.packaging
    source_text = _source_field_value(fields, semantics.packaging_source_field) or _text(
        fields.get("pack_size") or fields.get("uom")
    )
    if not source_text and not any(
        (
            semantics.purchase_uom,
            semantics.price_basis,
            semantics.sellable_unit_uom,
            semantics.break_pack_allowed is not None,
        )
    ):
        return None
    proposal: dict[str, Any] = {"source_text": source_text, "evidence": evidence}
    for attribute in ("purchase_uom", "price_basis", "sellable_unit_uom"):
        uom = getattr(semantics, attribute)
        if uom is not None:
            proposal[attribute] = uom.model_dump(mode="json")
    # A per-row purchase unit overrides the declared one; a row whose unit the
    # vocabulary does not know keeps whatever the contract declared.
    read_unit = _read_purchase_unit(fields, semantics)
    if read_unit:
        proposal["purchase_uom"] = {"code": read_unit, "label": None}
        if semantics.price_basis_follows_purchase_unit:
            proposal["price_basis"] = {"code": read_unit, "label": None}
    if semantics.break_pack_allowed is not None:
        proposal["break_pack_allowed"] = semantics.break_pack_allowed
    content_source = _source_field_value(fields, semantics.content_measure_source_field)
    content = _content_measure(content_source) if semantics.content_measure_source_field else None
    if content:
        amount, uom = content
        proposal["content_amount"] = str(amount)
        proposal["content_uom"] = (
            semantics.content_measure_uom.model_dump(mode="json")
            if semantics.content_measure_uom is not None
            else {"code": uom}
        )
    sellable_source = _source_field_value(fields, semantics.sellable_units_per_purchase_unit_source_field)
    sellable_count = _leading_decimal(sellable_source)
    # A count is a count of units per PURCHASE — without a resolved purchase
    # unit the claim dangles ("10 per what?") and, worse, a bare measure like
    # '10 mL (8 mL when constituted)' reads its content volume as a sellable
    # count and makes the publication guard refuse the row. Alfamedic's
    # '30ml/ bot' keeps its 30: that row resolves BOTTLE.
    # A leading number followed by a UNIT of measure is content, not a count:
    # "30ml Liquid" is one bottle holding thirty millilitres, not thirty things.
    # Only for contracts that declared their column means a count — a supplier
    # selling by the measure keeps the old reading.
    if (
        sellable_count is not None
        and semantics.sellable_count_excludes_measures
        and _content_measure(sellable_source)
    ):
        sellable_count = None
    if sellable_count is not None and (read_unit or semantics.purchase_uom is not None):
        proposal["sellable_units_per_purchase_unit"] = str(sellable_count)
    # '30ml/ bot' names a MEASURE, not a countable — the count above is the
    # Alfamedic trade, but the measure itself is printed and belongs in the
    # packaging as content ("30 ML / BOTTLE"). Captured only when no declared
    # content source exists (the schema forbids one field serving as declared
    # proof of both, and this is a reading of the page, not a declaration).
    if "content_amount" not in proposal and semantics.content_measure_source_field is None:
        implicit_content = _content_measure(sellable_source)
        if implicit_content:
            content_amount, content_uom = implicit_content
            proposal["content_amount"] = str(content_amount)
            proposal["content_uom"] = {"code": content_uom}
    # The count and its noun are printed together. Read the noun from the same
    # text rather than leaving it null whenever the contract has not declared
    # one — a declared value still wins, because the contract is the statement
    # of intent and this is only a reading of the page.
    if semantics.sellable_unit_uom is None:
        read_sellable = _sellable_unit_from_text(sellable_source)
        if read_sellable:
            proposal["sellable_unit_uom"] = {"code": read_sellable, "label": None}
        elif "content_amount" in proposal:
            # "30ml/ bot" names a MEASURE, so no countable was read — but the row
            # still sells something, and what it sells is the thing you buy: a
            # bottle. Naming it here is what lets a cost per unit exist at all;
            # left null, the 30 stands in as if the row sold millilitres.
            # Only fires alongside a captured content measure, so the packaging
            # phrase is untouched — the content noun already wins that slot.
            purchase = proposal.get("purchase_uom")
            if purchase and purchase.get("code"):
                proposal["sellable_unit_uom"] = {"code": purchase["code"], "label": None}
    quantity_uom = semantics.sellable_unit_uom or semantics.price_basis
    order_increment = _leading_decimal(_source_field_value(fields, semantics.order_increment_source_field))
    if order_increment is not None and quantity_uom is not None and quantity_uom.code is not None:
        proposal["order_increment"] = {
            "amount": str(order_increment),
            "uom": quantity_uom.model_dump(mode="json"),
        }
    minimum_order = _leading_decimal(_source_field_value(fields, semantics.minimum_order_source_field))
    if minimum_order is not None and quantity_uom is not None and quantity_uom.code is not None:
        proposal["minimum_order_quantity"] = {
            "amount": str(minimum_order),
            "uom": quantity_uom.model_dump(mode="json"),
        }
    return proposal


def _content_measure(text: str | None) -> tuple[Decimal, str] | None:
    if not text:
        return None
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(ml|g|kg|l|oz|lb|lbs)\b", text, re.IGNORECASE)
    if not match:
        return None
    amount = Decimal(match.group(1))
    raw_uom = match.group(2).upper()
    uom = {
        "ML": UnitCode.ML.value,
        "G": UnitCode.G.value,
        "KG": UnitCode.KG.value,
        "L": UnitCode.L.value,
        "OZ": UnitCode.OZ.value,
        "LB": UnitCode.LB.value,
        "LBS": UnitCode.LB.value,
    }[raw_uom]
    return amount, uom


def _source_field_value(fields: dict[str, Any], field_key: str | None) -> str | None:
    if not field_key:
        return None
    return _text(fields.get(f"source:{field_key}"))


# A count wearing its counter noun. K.P.N.'s case column merges the content
# size with the case count ('3lb 6包' = six 3-lb bags per case): the leading
# number is the SIZE, and reading it as the quantity would price "buy 3" —
# or "buy 12" on '12lb 2包' — where the page says buy 6 (or 2).
_COUNTED_QUANTITY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:包|罐|盒|支|袋|樽|瓶|pcs?|packs?|tins?|cans?|bags?|bottles?)\b",
    re.IGNORECASE,
)


def _counted_quantity(value: Any) -> Decimal | None:
    """The quantity in a case-configuration cell.

    Prefers the number attached to a counter noun over the leading number,
    because packing cells print measure-then-count. A clean cell ('6包',
    '24') still reads through the leading-number fallback.
    """
    if value is None:
        return None
    match = _COUNTED_QUANTITY.search(str(value))
    if match:
        try:
            return Decimal(match.group(1))
        except InvalidOperation:
            return None
    return _leading_decimal(value)


def _leading_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)", str(value))
    if not match:
        return None
    parsed = Decimal(match.group(1))
    return parsed if parsed > 0 else None


def _date_or_none(value: Any) -> date | None:
    text = _text(value)
    if text is None:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _confidence_text(field_value: Any, observation_confidence: Decimal | None) -> str | None:
    for candidate in (field_value, observation_confidence):
        if candidate is None or candidate == "":
            continue
        try:
            confidence = Decimal(str(candidate))
        except (InvalidOperation, ValueError):
            continue
        if Decimal("0") <= confidence <= Decimal("1"):
            return str(confidence)
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    decimal = _decimal_value(value)
    return decimal if decimal is not None and decimal >= 0 else None


# A price cell sometimes carries its unit or its own heading alongside the
# number — both printed verbatim by the source, neither part of the amount:
#   'HK$219.0/pcs'  -> 219.0 per piece (the suffix names the basis)
#   '批發價$392'     -> the column heading leaked into the cell text
# The suffix must be non-numeric so a genuine fraction ('3/8') never
# half-parses, and a cell carrying TWO amounts ('$739 HK$1056.0', a struck
# price beside an offer price) still refuses to parse — picking one would be
# a guess about which was crossed out.
_PRICE_UNIT_SUFFIX = re.compile(r"^(\d[\d.]*)\s*/\s*[^\d\s]+$")
_PRICE_HEADING_PREFIXES = ("批發價", "零售價", "建議零售價")
# A case price sometimes prints its own derived per-unit average beside it —
# Kangaroo Pet Nutrition writes '$340 (@28.3)' where 28.3 is exactly 340/12.
# The '@' marker makes it unambiguous annotation, not a second price, so it is
# dropped; a cell with two UNMARKED amounts still refuses to parse.
_PRICE_AVERAGE_ANNOTATION = re.compile(r"\s*\(\s*@\s*[\d.,]+\s*\)\s*$")


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower() in {"by quote", "quote", "n/a", "na"}:
        return None
    text = _PRICE_AVERAGE_ANNOTATION.sub("", str(value).strip())
    for heading in _PRICE_HEADING_PREFIXES:
        if text.startswith(heading):
            text = text[len(heading):].strip()
            break
    text = text.replace(",", "").replace("HK$", "").replace("HKD", "").replace("$", "").strip()
    suffixed = _PRICE_UNIT_SUFFIX.match(text)
    if suffixed:
        text = suffixed.group(1)
    try:
        decimal = Decimal(text)
    except (InvalidOperation, ValueError):
        # "130.0 (Price Reduced)" is a price with a printed remark, and Alfamedic
        # prints that shape on nine rows of one catalogue. Accept ONLY an amount
        # followed by a single parenthesised note — the note is dropped, never
        # read. "10% discount" and "1 box (12 pcs)" still refuse: the head must
        # itself be a parseable amount and nothing else. The note must carry NO
        # digits: a numeric note is a second amount ('$1094/箱 ($182/包)' prints
        # a case total beside its per-can average), and choosing between two
        # printed amounts is a guess this parser refuses to make.
        match = re.fullmatch(r"\s*([^()]+?)\s*\(([^()]+)\)\s*", str(value))
        if match and not re.search(r"\d", match.group(2)):
            return _decimal_value(match.group(1))
        return None
    return decimal


def _raw_money_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _money_amounts(value: Any) -> list[Decimal]:
    """Every monetary amount printed in one (possibly compound) cell, in order.

    K.P.N. Trading prints a case price and its per-unit average in one cell —
    '$1094/箱\\n($182/包)' — which no single-value parse can read. Each numeric
    token IS printed verbatim, so extracting them is reading, not computing.
    Commas are thousands separators ('1,094'); currency markers and CJK unit
    suffixes are not part of the number.
    """
    if value is None or value == "":
        return []
    amounts: list[Decimal] = []
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", str(value)):
        try:
            amounts.append(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return amounts


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fold(value: str) -> str:
    # Separator-insensitive: treat "/", "|" and whitespace runs as one space so a
    # contract column "Product Range / 產品系列" folds the same as an OCR rendering
    # "Product Range 產品系列".
    return re.sub(r"[\s/|]+", " ", value).strip().lower()


def _english_fold(value: str) -> str:
    # The Latin/ASCII portion only. Bilingual headers keep a stable English name
    # while their CJK rendering varies across OCR passes (the vision provider vs
    # the contract text), so the English part is the reliable match key.
    ascii_only = re.sub(r"[^\x00-\x7f]+", " ", value)
    ascii_only = re.sub(r"[/|*()]+", " ", ascii_only)
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


# A contract declares this as source_path when a field's value is the banner
# printed across the table rather than any column in it.
_SECTION_HEADER_SOURCE = "section_header"

# A contract declares this as source_path when a field's value sits in a column
# the source prints with NO heading at all — heading text cannot address it.
# Resolves only when the row carries exactly one non-empty unlabeled value;
# more than one is unresolvable ambiguity, not a guess to make.
_UNLABELED_COLUMN_SOURCE = "unlabeled_column"

# A contract declares this as source_path when a field's value is the brand
# whose logo or name heads the PAGE — Vetapet prints the maker's wordmark
# ('zoetis') top-right opposite its own letterhead, while the text above each
# table is a category. Page-scoped like supplier_identity_text, and absent on
# envelopes captured before the field existed.
_PAGE_BRAND_SOURCE = "page_brand"


# Spaces between CJK characters carry no meaning — Vetapet's retail section
# letter-spaces its headings in print ('批 發 價' for 批發價), so the verbatim
# transcription and a contract's compact declaration must fold to one key.
_CJK_INTERNAL_SPACE = re.compile(r"(?<=[⺀-鿿豈-﫿])\s+(?=[⺀-鿿豈-﫿])")


# An English fold whose only Latin content is a currency tag (plus digits and
# punctuation) is not a distinguishing name. Kangaroo Pet Nutrition prints
# '批發價 (HKD)' AND '建議零售價 (HKD) 每罐' on one page — both English-fold to
# bare 'hkd', which silently matched a contract's COST to the RRP column.
_CURRENCY_ONLY_ENGLISH = re.compile(r"(?:\b(?:hkd|hk|usd|rmb|cny)\b|[\d\s.,()*/-])*", re.IGNORECASE)


def _column_keys(value: str) -> list[str]:
    keys = [_fold(value)]
    english = _english_fold(value)
    if english and english not in keys and not _CURRENCY_ONLY_ENGLISH.fullmatch(english):
        keys.append(english)
    squashed = _CJK_INTERNAL_SPACE.sub("", keys[0])
    if squashed not in keys:
        keys.append(squashed)
    return keys


def _english_text(value: Any) -> str | None:
    """The Latin/ASCII portion of a (possibly bilingual) value.

    Bilingual source cells render as "中文 English"; keep only the English and
    tidy whitespace. Returns None when there is no Latin text — including when
    only punctuation survives the strip: a pure-Chinese name like
    '無穀幼犬糧 (新包裝)' leaves '( )', which is residue, not a name, and
    returning it would hand downstream a product with no usable name where the
    caller's verbatim fallback was standing by.
    """
    if value is None:
        return None
    ascii_only = re.sub(r"[^\x00-\x7f]+", " ", str(value))
    stripped = re.sub(r"\s+", " ", ascii_only).strip(" -|/*·,")
    if not re.search(r"[A-Za-z0-9]", stripped):
        return None
    return stripped


def _has_cells(observation: ExtractedEvidence) -> bool:
    return any(cell.raw_value is not None and str(cell.raw_value).strip() for cell in observation.raw_cells)


def _role_target(role) -> str | None:
    return _ROLE_TARGETS.get(getattr(role, "value", role))


_ROLE_TARGETS = {
    "SUPPLIER_SKU": "supplier_sku",
    "PRODUCT_NAME": "description",
    "BRAND": "brand",
    "CATEGORY": "category",
    "SOURCE_PRICE": "cost_price",
    "RRP": "rrp",
    "PACKAGING": "pack_size",
    "BARCODE": "barcode",
    "VARIANT": "variant",
    "SPECIES": "species",
    "SEGMENT": "segment",
    "MBB_TEXT": "bulk_buy_tiers",
    "EFFECTIVE_DATE": "effective_date",
    "ORDER_INCREMENT": "order_increment_qty",
    "CONTENT_MEASURE": "content_measure",
    "ROW_ELIGIBILITY": "row_eligibility",
}


__all__ = [
    "ConformanceOutcome",
    "ConformedRow",
    "ContractExecutionIssue",
    "conform_observations",
]
