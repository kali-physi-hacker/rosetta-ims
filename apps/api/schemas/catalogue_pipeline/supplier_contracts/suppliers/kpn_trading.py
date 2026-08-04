"""K.P.N. Trading supplier-source contract declarations."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas.catalogue_pipeline.enums import SourceFormat
from schemas.catalogue_pipeline.supplier_contracts.common import (
    SUPPLIER_SOURCE_SCHEMA_VERSION,
    AmbiguityRule,
    MbbSourceSemantics,
    PackagingSourceSemantics,
    PricingSourceSemantics,
    SemanticResolutionStatus,
    SourceFieldContract,
    SourceFieldRequirement,
    SourceFieldRole,
    SourceStructure,
    SourceTableRegion,
    SupplierContractSupportStatus,
    SupplierDocumentType,
    SupplierSourceContractV1,
    SupplierSourceEvidenceType,
    SupplierSourceReference,
)
from schemas.catalogue_pipeline.supplier_contracts.registry import register_supplier_source_contract

from ._shared import evidence, pipeline_mapping


_DECLARATION_CREATED_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)
_DECLARATION_CREATED_BY = "catalogue-contract-integration"

_KPN_TRADING_SUPPLIER = SupplierSourceReference(
    supplier_id=15,
    supplier_name="K.P.N. Trading",
    supplier_code="KPNTRADI",
)

_KPN_TRADING_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf",
        (
            "The sample contains sections explicitly identified as K.P.N. Trading "
            "with Stella & Chewy's, Canidae, and NOW FRESH catalogue tables."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "docs/technical-debt/kpn-kangaroo-supplier-source-contracts.md",
        "The production supplier identity is supplier ID 15 with code KPNTRADI.",
    ),
]


KPN_TRADING_CATALOGUE_BUNDLE_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kpn_trading.catalogue_bundle.v1",
        contract_version="v1",
        supplier=_KPN_TRADING_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="K.P.N. Trading catalogue bundle",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KPN_TRADING_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kpn_trading_identified_sections",
                    selector=(
                        "Catalogue sections attributed to supplier ID 15 or explicitly "
                        "marked K.P.N. Trading / KPNTRADI"
                    ),
                    notes=(
                        "The sample includes Stella & Chewy's, Canidae, and NOW FRESH, "
                        "but a valid catalogue may contain any subset of brands."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號",
                "產品名稱",
                "批發價",
                "SKU#",
                "Product Description",
                "Size",
                "Unit Per Case",
                "建議零售價",
                "Wholesale Price Per Unit",
                "Wholesale Price Per Case",
                "Retail Price Per Unit",
                "Retail Price Per Case",
                "OLD SKU#",
                "NEW SKU#",
                "Last update",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 15, or the enclosing source "
                    "section must explicitly identify K.P.N. Trading / KPNTRADI."
                ),
                "Never select this contract from page number or brand presence alone.",
                "Rows require a product code, product description, and printed wholesale price.",
            ],
            source_location_expectations=[
                "source document and page",
                "supplier identity marker or ingestion supplier identity",
                "brand/section heading",
                "table row",
                "source column",
            ],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品編號 / SKU#",
                aliases=["產品編號", "SKU#", "Product Code"],
                description="Current product code printed on the eligible K.P.N. Trading row.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="previous_supplier_sku",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="OLD SKU#",
                aliases=["Old SKU", "Previous SKU"],
                description="Previous Canidae supplier code when the source prints an SKU transition.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="replacement_supplier_sku",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="NEW SKU#",
                aliases=["New SKU", "Replacement SKU"],
                description="Replacement Canidae supplier code when separately printed.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                # OPTIONAL for the same reason as Kangaroo: the brand is a
                # banner, not a column, and a REQUIRED field that cannot resolve
                # blocks every row in the catalogue.
                requirement=SourceFieldRequirement.OPTIONAL,
                # The one source_path the engine resolves — the banner printed
                # across the table.
                source_path="section_header",
                description="Printed row or section brand; observed brands are examples, not routing criteria.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品名稱 / Product Description",
                aliases=["產品名稱", "產品內容", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝 / Size",
                aliases=["重量", "包裝", "Size"],
                description="Printed content size or packaging text.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="原箱包數 / Unit Per Case",
                aliases=["原箱包數", "每箱包數", "Unit Per Case", "Per Case"],
                description="Printed case configuration; it is not an ordering constraint by itself.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價 / Wholesale Price",
                aliases=[
                    "批發價",
                    "每包批發價",
                    "每箱批發價",
                    "Wholesale Price Per Unit",
                    "Wholesale Price Per Case",
                ],
                description="Wholesale amount preserved with its exact printed source heading.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價 / Retail Price",
                aliases=[
                    "建議零售價",
                    "Retail Price Per Unit",
                    "Retail Price Per Case",
                ],
                description="Recommended retail amount preserved with its printed price basis.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="availability",
                role=SourceFieldRole.ROW_ELIGIBILITY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="row availability or discontinued marker",
                description="Availability or discontinued state printed for Canidae rows.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date / last-update label",
                description="Document- or section-level effective or last-update date.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed spend, order-discount, or promotional terms.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=None,
            price_basis_status=SemanticResolutionStatus.UNRESOLVED,
            notes=(
                "The bundle mixes per-unit, per-pack, and per-case wholesale columns. "
                "The source heading must be retained and the price basis left unresolved "
                "until the detected table layout is routed to a layout-specific contract."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat content size as a measure, not a sellable-unit count.",
                "Treat units per case as case configuration only.",
                "Do not infer purchase UOM, order increment, or break-pack permission from case configuration.",
            ],
            unresolved_semantics=[
                "Purchase UOM varies by table layout.",
                "Sellable units per purchase unit are not established at bundle level.",
                "Break-pack permission is not established by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["spend threshold", "order discount", "buy quantity"],
            benefit_patterns=["percentage discount", "free quantity"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or mix-and-match rules are not explicit."
            ],
            notes="Promotion text remains evidence until a layout-specific rule proves its scope and benefit.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KPN_TRADING_BUNDLE_PRICE_BASIS_VARIES",
                condition="K.P.N. Trading catalogue layouts mix unit, pack, and case price bases.",
                review_guidance=(
                    "Segment the source by brand and table layout before interpreting wholesale or RRP amounts."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="KPN_TRADING_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 15 or an "
                    "explicit K.P.N. Trading / KPNTRADI source marker; never from page position or brand."
                ),
                blocks_supported_status=True,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "previous_supplier_sku",
            "replacement_supplier_sku",
            "brand",
            "description",
            "pack_size",
            "units_per_case",
            "wholesale_price",
            "rrp",
            "availability",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_content_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "observed_brands": "Stella & Chewy's, Canidae, NOW FRESH",
        },
    )
)
