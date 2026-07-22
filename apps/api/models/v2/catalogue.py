"""Catalogue domain models including ingestion runs."""
from .base import Base, Column, Integer, String, Float, ForeignKey, relationship


class CatalogueImport(Base):
    __tablename__ = "catalogue_imports"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    filename    = Column(String, nullable=False)
    format      = Column(String)   # 'pdf' | 'xlsx' | 'jpeg' | 'gdoc'
    imported_at = Column(String, nullable=False)
    status      = Column(String, nullable=False, default='pending')
    item_count  = Column(Integer)

    # ── Supplier detection / resolution (stage-1 supplier confirm) ──
    detected_supplier_name = Column(String)   # what the AI read off the document
    detected_brands        = Column(String)   # comma-joined brands detected
    supplier_confidence    = Column(Float)     # resolver confidence for the matched supplier
    supplier_source        = Column(String)   # 'user' | 'ai' | None
    supplier_status        = Column(String)   # 'confirmed' | 'needs_review'

    # ── Re-parse (RP-1.2): storage key of the persisted upload, for future re-OCR from source ──
    source_ref             = Column(String, nullable=True)

    items = relationship("CatalogueItem", back_populates="catalogue_import")
    ingestion_runs = relationship("CatalogueIngestionRun", back_populates="source_asset")


class CatalogueIngestionRun(Base):
    """One attempt to process a Catalogue Source Asset (CatalogueImport).
    Stores metadata about what happened during that specific ingestion workflow run.
    Does not store extracted catalogue data directly - those are in CatalogueItem records."""
    __tablename__ = "catalogue_ingestion_runs"

    id                      = Column(Integer, primary_key=True, autoincrement=True)
    source_asset_id         = Column(Integer, ForeignKey("catalogue_imports.id"), nullable=False)
    supplier_id             = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    
    # Extraction profile and version tracking
    extraction_profile_id   = Column(String, nullable=True)   # profile identifier used
    extraction_profile_version = Column(String, nullable=True)  # exact version snapshot
    extractor_name          = Column(String, nullable=True)   # e.g. 'claude-haiku', 'rule-based-excel'
    extractor_version       = Column(String, nullable=True)   # e.g. '4.5-20251001', 'v2.3'
    
    # Parent run relationship for retries/reprocessing
    parent_run_id           = Column(Integer, ForeignKey("catalogue_ingestion_runs.id"), nullable=True)
    
    # Run lifecycle
    status                  = Column(String, nullable=False, default='pending')  # pending | running | completed | failed | cancelled
    started_at              = Column(String, nullable=False)   # ISO datetime when run started
    completed_at            = Column(String, nullable=True)    # ISO datetime when run finished (success or failure)
    
    # Operational metrics
    items_extracted         = Column(Integer, nullable=True)   # number of items successfully extracted
    extraction_duration_ms  = Column(Integer, nullable=True)   # milliseconds taken for extraction
    confidence_metrics      = Column(String, nullable=True)    # JSON: confidence distribution, averages, etc.
    
    # Error tracking
    error_type              = Column(String, nullable=True)    # e.g. 'extraction_failure', 'timeout', 'validation_error'
    error_message           = Column(String, nullable=True)    # human-readable error description
    error_details           = Column(String, nullable=True)    # JSON: stack trace, detailed diagnostics
    
    # Metadata
    created_at              = Column(String, nullable=False)   # ISO datetime of record creation
    created_by              = Column(String, nullable=True)    # user/system that initiated the run
    
    # Relationships
    source_asset = relationship("CatalogueImport", back_populates="ingestion_runs")
    parent_run = relationship("CatalogueIngestionRun", 
                            remote_side=[id],
                            backref="child_runs")
    items = relationship("CatalogueItem", back_populates="ingestion_run")


class CatalogueCostStaging(Base):
    __tablename__ = "catalogue_cost_staging"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    import_id          = Column(Integer, ForeignKey("catalogue_imports.id"), nullable=True)
    supplier_id        = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    raw_supplier_sku   = Column(String)
    matched_product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    match_confidence   = Column(Float)             # 0.0–1.0
    extracted_cost     = Column(Float)
    status             = Column(String, nullable=False, default='pending')  # pending|approved|rejected
    reviewed_by        = Column(String)
    reviewed_at        = Column(String)
    created_at         = Column(String, nullable=False)


class CatalogueItem(Base):
    __tablename__ = "catalogue_items"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    import_id          = Column(Integer, ForeignKey("catalogue_imports.id"), nullable=False)
    ingestion_run_id   = Column(Integer, ForeignKey("catalogue_ingestion_runs.id"), nullable=True)
    supplier_id        = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    raw_description    = Column(String)   # product name shown for review (English after translation)
    original_description = Column(String, nullable=True)  # source text as printed, when translated from another language
    supplier_sku       = Column(String)
    barcode            = Column(String)
    cost_price         = Column(Float)
    uom                = Column(String)
    units_per_pack     = Column(Integer, nullable=True)  # how many sell-units per purchasable pack
    min_sellable_qty   = Column(Integer, nullable=True)  # smallest sellable quantity in `uom` units
    brand              = Column(String, nullable=True)   # extracted brand (e.g. "Zoetis", "Dechra")
    variant            = Column(String, nullable=True)   # size/volume/flavour distinguishing sibling variants
    pack_size          = Column(String, nullable=True)   # raw pack-size string (e.g. "100 tabs/ box")
    max_bulk_buy_cost  = Column(Float, nullable=True)    # deepest-discount per-unit cost across all bulk tiers
    max_bulk_buy_min_qty = Column(Integer, nullable=True)  # qty needed to hit max_bulk_buy_cost
    bulk_buy_tiers     = Column(String)   # human-readable tier string
    confidence_score   = Column(Float)    # 0.0–1.0
    confidence_detail  = Column(String)   # JSON: per-field confidence
    review_status      = Column(String, nullable=False, default='pending')
    # Skip bucket — a pending item the reviewer sets aside for later
    skipped            = Column(Integer, nullable=False, default=0)
    skipped_at         = Column(String, nullable=True)
    skipped_by         = Column(String, nullable=True)
    matched_product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    assigned_sku       = Column(String)
    reviewed_by        = Column(String)
    reviewed_at        = Column(String)
    created_at         = Column(String, nullable=False)
    ai_tags            = Column(String, nullable=True)   # JSON array of suggested free-form tags
    ai_category        = Column(String, nullable=True)   # AI-suggested SKU category
    ai_subcategory     = Column(String, nullable=True)   # AI-detected functional/clinical class
    # Additional OCR-extracted fields (v7 OCR-marked columns)
    species            = Column(String, nullable=True)   # dog | cat | both | other
    weight_grams       = Column(Float, nullable=True)    # net weight per sell-unit (canonical, grams)
    weight_unit        = Column(String, nullable=True)   # display/source unit: 'kg' (default) | 'lb'
    rrp                = Column(Float, nullable=True)     # recommended retail price (HKD)
    min_purchase_qty   = Column(Integer, nullable=True)  # supplier MOQ per SKU
    bulk_tiers         = Column(String, nullable=True)   # JSON: [{min_qty, unit_cost}]
    # ── Re-parse versioning (RP-1.1)
    parser_version     = Column(String, nullable=True)
    reparsed_at        = Column(String, nullable=True)
    reparse_source     = Column(String, nullable=True)   # 'text' | 'source'

    catalogue_import  = relationship("CatalogueImport", back_populates="items")
    ingestion_run     = relationship("CatalogueIngestionRun", back_populates="items")
    matched_product   = relationship("Product", back_populates="catalogue_items")


class CatalogueAuditEvent(Base):
    """Append-only trail of every human decision taken during catalogue onboarding."""
    __tablename__ = "catalogue_audit"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    item_id      = Column(Integer, ForeignKey("catalogue_items.id"), nullable=True, index=True)
    import_id    = Column(Integer, ForeignKey("catalogue_imports.id"), nullable=True, index=True)
    product_id   = Column(Integer, ForeignKey("products.id"), nullable=True, index=True)
    sku_code     = Column(String, nullable=True, index=True)   # denormalised for cheap lookup-by-SKU
    action       = Column(String, nullable=False)              # confirm_match|assign_new|edit|reject|supplier_confirm
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=True)
    username     = Column(String, nullable=True)               # snapshot — survives user rename/delete
    display_name = Column(String, nullable=True)
    details      = Column(String, nullable=True)               # JSON: before/after, reason, match target, etc.
    created_at   = Column(String, nullable=False, index=True)


class ReparseBatch(Base):
    """RP-2.1 — one re-parse run over a scope (a SKU / import / supplier)."""
    __tablename__ = "reparse_batch"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    scope_type     = Column(String, nullable=False)                    # 'item' | 'import' | 'supplier'
    scope_ref      = Column(String, nullable=False)                    # sku_code / import_id / supplier_id
    parser_version = Column(String, nullable=True)                     # version this run derived with
    mode           = Column(String, nullable=False, default='text')    # 'text' | 'source'
    status         = Column(String, nullable=False, default='open')    # 'open' | 'applied' | 'discarded'
    item_count     = Column(Integer, nullable=True)
    changed_count  = Column(Integer, nullable=True)
    created_at     = Column(String, nullable=False)
    created_by     = Column(String, nullable=True)

    changes = relationship("ReparseChange", back_populates="batch")


class ReparseChange(Base):
    """RP-2.1 — one field diff (old -> new) for one catalogue item within a batch."""
    __tablename__ = "reparse_change"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    batch_id          = Column(Integer, ForeignKey("reparse_batch.id"), nullable=False, index=True)
    catalogue_item_id = Column(Integer, ForeignKey("catalogue_items.id"), nullable=False)
    product_id        = Column(Integer, ForeignKey("products.id"), nullable=True)   # set once committed
    field             = Column(String, nullable=False)
    old_value         = Column(String, nullable=True)
    new_value         = Column(String, nullable=True)
    affects_cost      = Column(Integer, nullable=False, default=0)     # 1 if it moves effective unit cost
    eff_cost_before   = Column(Float, nullable=True)
    eff_cost_after    = Column(Float, nullable=True)
    status            = Column(String, nullable=False, default='pending')   # pending|confirmed|rejected|stale
    confirmed_by      = Column(String, nullable=True)
    confirmed_at      = Column(String, nullable=True)

    batch = relationship("ReparseBatch", back_populates="changes")