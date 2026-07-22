"""Runtime adapter for Pydantic supplier-source contracts.

The authoritative declarations live in
`schemas.catalogue_pipeline.supplier_contracts`. This adapter selects
production-supported declarations and applies only explicitly modelled source
semantics needed by the current ingestion/reparse runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from schemas.catalogue_pipeline.enums import UnitCode
from schemas.catalogue_pipeline.supplier_contracts import (
    SupplierContractSupportStatus,
    SupplierSourceContractV1,
    iter_supplier_source_contracts,
)
from schemas.catalogue_pipeline.supplier_contracts.common import SourceFieldContract, SourceFieldRole


_ROLE_TARGETS = {
    SourceFieldRole.SUPPLIER_SKU: "supplier_sku",
    SourceFieldRole.PRODUCT_NAME: "description",
    SourceFieldRole.BRAND: "brand",
    SourceFieldRole.CATEGORY: "category",
    SourceFieldRole.SOURCE_PRICE: "cost_price",
    SourceFieldRole.RRP: "rrp",
    SourceFieldRole.PACKAGING: "pack_size",
    SourceFieldRole.BARCODE: "barcode",
    SourceFieldRole.VARIANT: "variant",
    SourceFieldRole.SPECIES: "species",
    SourceFieldRole.SEGMENT: "segment",
    SourceFieldRole.ORDER_INCREMENT: "order_increment_qty",
}

_DIRECT_ITEM_FIELDS = {
    "supplier_sku",
    "description",
    "pack_size",
    "brand",
    "barcode",
    "variant",
    "cost_price",
    "rrp",
    "species",
    "segment",
    "category",
    "order_increment_qty",
    "weight_grams",
}

_MASS_TO_GRAMS = {
    "kg": Decimal("1000"),
    "g": Decimal("1"),
    "lb": Decimal("453.592"),
    "lbs": Decimal("453.592"),
    "pound": Decimal("453.592"),
    "pounds": Decimal("453.592"),
    "oz": Decimal("28.3495"),
}


@dataclass(frozen=True)
class SupplierSourceRuntimeContract:
    """Production runtime view over one supported supplier-source declaration."""

    declaration: SupplierSourceContractV1

    @property
    def slug(self) -> str:
        return self.declaration.contract_id

    @property
    def version(self) -> str:
        return self.declaration.contract_version

    @property
    def supplier(self) -> str:
        return self.declaration.supplier.supplier_name

    @property
    def supplier_id(self) -> int | None:
        return self.declaration.supplier.supplier_id

    def display_name(self) -> str:
        return f"{self.slug}@{self.version}"

    def expected_columns(self) -> set[str]:
        """Return source headers/paths used by this declaration."""

        columns: set[str] = set(self.declaration.source_structure.required_headers)
        columns.update(self.declaration.source_structure.optional_headers)
        for field in self.declaration.fields:
            if field.source_column:
                columns.add(field.source_column)
            if field.source_path:
                columns.add(field.source_path)
            columns.update(field.composed_from)
        return columns

    def prompt_section(self) -> str:
        """Supplier-specific extraction guidance derived from Pydantic declarations."""

        lines = [
            "",
            f"=== SUPPLIER SOURCE CONTRACT: {self.display_name()} ===",
            f"Supplier: {self.supplier}",
            f"Format: {self.declaration.format_name}",
            "Use this typed source contract when extracting rows. Do not invent values for unresolved semantics.",
        ]
        if self.declaration.source_structure.required_headers:
            lines.append("Required source headers: " + "; ".join(self.declaration.source_structure.required_headers))
        if self.declaration.source_structure.row_eligibility_rules:
            lines.append("Row eligibility: " + " ".join(self.declaration.source_structure.row_eligibility_rules))

        for field in self.declaration.fields:
            target = _target_for_field(field)
            source = _source_description(field)
            if target:
                lines.append(f'- Output `{target}` from {source}. Role: {field.role.value}.')
            else:
                lines.append(f"- Preserve source field `{field.field_key}` from {source}. Role: {field.role.value}.")
            if field.constant_value is not None:
                lines.append(f'  Constant value: "{field.constant_value}".')

        pricing = self.declaration.pricing
        price_basis = pricing.price_basis.code.value if pricing.price_basis and pricing.price_basis.code else "unresolved"
        lines.append(f"Cost field key: {pricing.cost_source_field}; currency: {pricing.currency}; price basis: {price_basis}.")
        if pricing.rrp_source_field:
            lines.append(f"RRP field key: {pricing.rrp_source_field}. Never swap supplier cost and RRP.")
        else:
            lines.append("This source contract has no RRP source field; leave `rrp` null.")
        for marker in pricing.null_cost_markers:
            lines.append(f'Cost marker "{marker}" means the cost is unavailable and needs review.')

        for rule in self.declaration.packaging.interpretation_rules:
            lines.append(f"Packaging rule: {rule}")
        for ambiguity in self.declaration.known_ambiguities:
            lines.append(f"Known ambiguity: {ambiguity.condition} Decision needed: {ambiguity.review_guidance}")

        return "\n".join(lines)

    def apply(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """Apply explicit source-contract semantics and return validation flags."""

        flags: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                flags.append(
                    {
                        "index": index,
                        "sku": None,
                        "rule": "SOURCE_ROW_NOT_OBJECT",
                        "detail": "Extracted row is not a JSON object.",
                    }
                )
                continue
            self.apply_to_item(item)
            flags.extend(self._validation_flags(index, item))
        return items, flags

    def apply_to_item(self, item: dict) -> None:
        """Apply non-ambiguous source-contract semantics to one extracted item."""

        for field in self.declaration.fields:
            target = _target_for_field(field)
            if target and item.get(target) in (None, "") and field.field_key in item:
                item[target] = item.get(field.field_key)
            if target and field.constant_value is not None:
                item[target] = field.constant_value

        self._apply_null_cost_markers(item)
        self._apply_price_basis_compatibility(item)
        self._apply_missing_rrp_semantics(item)
        self._apply_order_increment(item)
        self._apply_content_weight(item)

    def _apply_null_cost_markers(self, item: dict) -> None:
        value = item.get("cost_price")
        if not isinstance(value, str):
            return
        lowered = value.lower()
        if any(marker.lower() in lowered for marker in self.declaration.pricing.null_cost_markers):
            item["cost_price"] = None

    def _apply_price_basis_compatibility(self, item: dict) -> None:
        """Adapt approved per-sellable-unit prices to the current flat runtime field."""

        basis = self.declaration.pricing.price_basis
        if basis and basis.code in {UnitCode.PIECE, UnitCode.UNIT}:
            item["units_per_pack"] = 1

    def _apply_missing_rrp_semantics(self, item: dict) -> None:
        if self.declaration.pricing.rrp_source_field is None:
            item["rrp"] = None

    def _apply_order_increment(self, item: dict) -> None:
        source_field_key = self.declaration.packaging.order_increment_source_field
        if not source_field_key:
            return
        source_field = self._field_by_key(source_field_key)
        if source_field is None:
            return
        target = _target_for_field(source_field)
        raw_value = item.get(target) if target else item.get(source_field.field_key)
        parsed = _leading_int(raw_value)
        if parsed is not None:
            item["order_increment_qty"] = parsed

    def _apply_content_weight(self, item: dict) -> None:
        packaging = self.declaration.packaging
        if packaging.content_measure_source_field != packaging.packaging_source_field:
            return
        grams = _grams_from_text(item.get("pack_size"))
        if grams is not None:
            item["weight_grams"] = grams

    def _validation_flags(self, index: int, item: dict) -> list[dict[str, Any]]:
        flags: list[dict[str, Any]] = []
        for rule in self.declaration.validation_rules:
            failed, detail = _rule_failure(rule.rule_id, item)
            if failed:
                flags.append(
                    {
                        "index": index,
                        "sku": item.get("supplier_sku"),
                        "rule": rule.issue_code,
                        "detail": detail or rule.review_guidance,
                    }
                )
        return flags

    def _field_by_key(self, field_key: str) -> SourceFieldContract | None:
        for field in self.declaration.fields:
            if field.field_key == field_key:
                return field
        return None


def load_contract(supplier_id: int | None) -> SupplierSourceRuntimeContract | None:
    """Return the supported source contract for a supplier ID, if exactly one exists."""

    if supplier_id is None:
        return None
    supplier_id = int(supplier_id)
    matches = [
        registration.declaration
        for registration in iter_supplier_source_contracts()
        if registration.support_status == SupplierContractSupportStatus.SUPPORTED
        and registration.declaration.supplier.supplier_id == supplier_id
    ]
    if not matches:
        return None
    if len(matches) > 1:
        ids = ", ".join(sorted(item.contract_id for item in matches))
        raise ValueError(f"multiple supported supplier source contracts for supplier_id={supplier_id}: {ids}")
    return SupplierSourceRuntimeContract(matches[0])


def _target_for_field(field: SourceFieldContract) -> str | None:
    if field.field_key in _DIRECT_ITEM_FIELDS:
        return field.field_key
    return _ROLE_TARGETS.get(field.role)


def _source_description(field: SourceFieldContract) -> str:
    parts: list[str] = []
    if field.source_column:
        parts.append(f'source column "{field.source_column}"')
    if field.source_path:
        parts.append(f'source path "{field.source_path}"')
    if field.composed_from:
        parts.append("joined columns " + ", ".join(f'"{item}"' for item in field.composed_from))
    if field.constant_value is not None:
        parts.append("contract constant")
    return " and ".join(parts) or f"field key `{field.field_key}`"


def _leading_int(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    parsed = int(match.group())
    return parsed if parsed > 0 else None


def _grams_from_text(value: Any) -> float | None:
    if not value:
        return None
    text = str(value).strip().lower().rsplit("/", 1)[-1]
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|lbs?|pounds?|oz)\b", text)
    if not match:
        return None
    amount = Decimal(match.group(1))
    grams = amount * _MASS_TO_GRAMS[match.group(2)]
    return float(round(grams))


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace("HK$", "").replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _rule_failure(rule_id: str, item: dict) -> tuple[bool, str]:
    if rule_id.endswith("cost_below_rrp"):
        cost = _decimal(item.get("cost_price"))
        rrp = _decimal(item.get("rrp"))
        if cost is None or rrp is None:
            return False, ""
        return cost >= rrp, f"cost_price({item.get('cost_price')}) is not below rrp({item.get('rrp')})."

    if rule_id.endswith(("order_multiple_positive", "order_increment_positive")):
        quantity = _decimal(item.get("order_increment_qty"))
        if quantity is None:
            return False, ""
        return quantity <= 0, f"order_increment_qty({item.get('order_increment_qty')}) is not positive."

    if rule_id.endswith("cost_positive_when_present"):
        cost = _decimal(item.get("cost_price"))
        if cost is None:
            return False, ""
        return cost <= 0, f"cost_price({item.get('cost_price')}) is not positive."

    return False, ""
