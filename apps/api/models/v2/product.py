"""Product, inventory and related models."""
from .base import Base, Column, Integer, String, Float, ForeignKey, UniqueConstraint, relationship


class CategoryRule(Base):
    __tablename__ = "category_rules"

    category          = Column(String, primary_key=True)
    gp_floor          = Column(Float, nullable=False)           # decimal e.g. 0.70
    storage_rule      = Column(String, nullable=False, default='any')  # 'clinic_only' | 'any'
    channel_restriction = Column(String, nullable=True)         # NULL | 'clinic'
    sku_digit         = Column(String, nullable=True)           # 1 leading digit for generated SKUs


class Product(Base):
    __tablename__ = "products"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    sku_code     = Column(String, unique=True, nullable=False, index=True)
    name         = Column(String, nullable=False)
    brand        = Column(String)
    category     = Column(String, nullable=False)
    subcategory  = Column(String, nullable=True)   # AI-detected functional/clinical class (e.g. "antibiotic")
    segment      = Column(String, nullable=True)   # 'vet' | 'non_vet' | NULL — veterinary vs retail SKU (derived from supplier segment)
    species      = Column(String, nullable=True)   # dog | cat | both | other (OCR)
    rrp          = Column(Float, nullable=True)     # recommended retail price, HKD (OCR)
    min_purchase_qty = Column(Integer, nullable=True)  # supplier MOQ per SKU (OCR) — number of packs you must order
    min_sellable_qty = Column(Integer, nullable=True)  # smallest sellable quantity in `uom` units — usually 1 (1 tablet)
    # Per-platform listing status, refreshed by the platform reconciliation.
    # NULL = not listed on that platform.
    shopify_status   = Column(String, nullable=True)   # active | archived | draft
    daysmart_status  = Column(String, nullable=True)   # active
    hktv_status      = Column(String, nullable=True)   # online | offline
    # Platform-recorded cost of goods (merchant-entered / computed on each platform).
    shopify_cost     = Column(Float, nullable=True)    # Shopify InventoryItem unitCost
    daysmart_cost    = Column(Float, nullable=True)    # DaySmart avg unit cost (balances API)
    hktv_cost        = Column(Float, nullable=True)    # HKTV product template Cost column
    uom          = Column(String)           # sell UOM: tablet, ml, g — the unit you sell one of
    pack_unit    = Column(String)           # buy UOM: box, bottle, strip — the supplier packaging unit
    storage_rule = Column(String, nullable=False, default='any')   # 'clinic_only' | 'any'
    status       = Column(String, nullable=False, default='ACTIVE') # ACTIVE | INACTIVE | DISCONTINUED
    hero_sku     = Column(Integer, nullable=False, default=0)
    notes        = Column(String)
    weight_g     = Column(Float, nullable=True)
    weight_unit  = Column(String, nullable=True)   # display/source unit: 'kg' (default) | 'lb' — grams is canonical
    last_manual_edit_at = Column(String, nullable=True)  # set only by human PATCH, never by sync
    last_manual_edit_by = Column(String, nullable=True)  # display_name of the user who made the edit
    created_at   = Column(String, nullable=False)
    updated_at   = Column(String, nullable=False)

    channels          = relationship("ProductChannel", back_populates="product")
    stock_levels      = relationship("StockLevel", back_populates="product")
    product_suppliers = relationship("ProductSupplier", back_populates="product")
    sales_velocity    = relationship("SalesVelocity", back_populates="product")
    competitor_prices = relationship("CompetitorPrice", back_populates="product")
    expiry_tracking   = relationship("ExpiryTracking", back_populates="product")
    stock_adjustments = relationship("StockAdjustment", back_populates="product")
    catalogue_items   = relationship("CatalogueItem", back_populates="matched_product")
    tag_links         = relationship("ProductTag", back_populates="product", cascade="all, delete-orphan")


class ProductSupplier(Base):
    __tablename__ = "product_suppliers"
    __table_args__ = (UniqueConstraint('product_id', 'supplier_id'),)

    id              = Column(Integer, primary_key=True, autoincrement=True)
    product_id      = Column(Integer, ForeignKey("products.id"), nullable=False)
    supplier_id     = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    supplier_sku    = Column(String)
    barcode         = Column(String)
    basic_cost      = Column(Float)          # the wholesale cost — the single cost all margin math runs on
    units_per_pack       = Column(Integer, nullable=True)  # IMS-live value (locked once uom_verified_at is set)
    units_per_pack_sheet = Column(Integer, nullable=True)  # shadow: last value seen from Sheet sync
    pack_source     = Column(String, nullable=False, default='sheet')   # sheet|manual|catalogue
    uom_verified_at = Column(String, nullable=True)   # IMS-stamped date UOM/pack size was manually confirmed
    uom_verified_by = Column(String, nullable=True)   # name/initials of person who confirmed the pack size
    basic_cost_sheet = Column(Float, nullable=True)   # shadow: last basic_cost value seen from Sheet sync
    is_primary      = Column(Integer, nullable=False, default=0)
    cost_source     = Column(String, nullable=False, default='manual')   # sheet|manual|po_issued|invoice_matched|catalogue
    cost_source_ref = Column(String, nullable=True)   # e.g. "catalogue_import:42" or "invoice:17"
    cost_updated_at = Column(String, nullable=True)   # ISO datetime of last cost change specifically
    updated_at      = Column(String, nullable=False)
    # ── Supplier stock status (out-of-stock tracking) — per (SKU x supplier) link ──
    stock_status        = Column(String, nullable=False, default='in_stock')  # in_stock | out_of_stock
    reported_out_at     = Column(String, nullable=True)   # YYYY-MM-DD this supplier went OOS for this SKU
    expected_restock_at = Column(String, nullable=True)   # YYYY-MM-DD expected back in stock
    stock_confirmed_by  = Column(String, nullable=True)   # who set/confirmed the status
    stock_note          = Column(String, nullable=True)   # e.g. "seasonal supply gap"
    stock_updated_at    = Column(String, nullable=True)   # ISO datetime the status last changed
    # ── Ordering terms — order multiple / MOQ.
    order_increment_qty  = Column(Integer, nullable=True)   # order in multiples of this many sell-units
    order_increment_uom  = Column(String, nullable=True)    # sell-unit the qty counts
    minimum_order_qty    = Column(Integer, nullable=True)   # smallest orderable quantity, in sell-units
    minimum_order_uom    = Column(String, nullable=True)    # sell-unit for minimum_order_qty
    minimum_order_source = Column(String, nullable=True)    # provenance of minimum_order_qty (app-level enum)
    pricing_note         = Column(String, nullable=True)    # free-text audit note

    product  = relationship("Product", back_populates="product_suppliers")
    supplier = relationship("Supplier", back_populates="product_suppliers")
    mbb_term_list = relationship("MbbTerm", back_populates="product_supplier",
                                 cascade="all, delete-orphan", order_by="MbbTerm.sort_order")
    stock_events = relationship("SupplierStockEvent", back_populates="product_supplier",
                                cascade="all, delete-orphan", order_by="SupplierStockEvent.out_at")


class MbbTerm(Base):
    """One Max-Bulk-Buy term on a (SKU x supplier) link."""
    __tablename__ = "mbb_terms"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    product_supplier_id = Column(Integer, ForeignKey("product_suppliers.id"), nullable=False, index=True)
    kind                = Column(String, nullable=False)   # buy_x_get_y | spend_discount | tier | flat_unit_cost
    min_qty             = Column(Integer, nullable=True)   # units to unlock — the "buy X"
    min_spend           = Column(Float, nullable=True)     # HK$ to unlock
    free_qty            = Column(Integer, nullable=True)   # buy_x_get_y: get Y free
    discount_pct        = Column(Float, nullable=True)     # spend_discount: fraction off, e.g. 0.10
    unit_cost           = Column(Float, nullable=True)     # tier / flat_unit_cost: explicit per-SELL-unit cost
    note                = Column(String, nullable=True)    # human label
    sort_order          = Column(Integer, nullable=False, default=0)
    created_at          = Column(String, nullable=False)
    updated_at          = Column(String, nullable=True)

    product_supplier = relationship("ProductSupplier", back_populates="mbb_term_list")


class ProductChannel(Base):
    __tablename__ = "product_channels"
    __table_args__ = (UniqueConstraint('product_id', 'channel'),)

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    product_id          = Column(Integer, ForeignKey("products.id"), nullable=False)
    channel             = Column(String, nullable=False)   # 'clinic' | 'shopify' | 'hktv'
    is_active           = Column(Integer, nullable=False, default=1)
    selling_price       = Column(Float)
    has_dispensing_fee  = Column(Integer, nullable=False, default=0)
    channel_fee_pct     = Column(Float, nullable=True)     # e.g. 0.08 = 8% HKTV platform fee
    units_per_listing   = Column(Integer, nullable=True)   # how many sell-units per HKTV listing
    updated_at          = Column(String, nullable=False)

    product = relationship("Product", back_populates="channels")


class StockLevel(Base):
    __tablename__ = "stock_levels"
    __table_args__ = (UniqueConstraint('product_id', 'location'),)

    id          = Column(Integer, primary_key=True, autoincrement=True)
    product_id  = Column(Integer, ForeignKey("products.id"), nullable=False)
    location    = Column(String, nullable=False)   # 'clinic' | 'warehouse'
    qty         = Column(Float, nullable=False, default=0)
    as_of_date  = Column(String)                   # YYYY-MM-DD of source export
    source      = Column(String, nullable=False, default='import')  # 'import' | 'manual_adjustment'
    updated_at  = Column(String, nullable=False)

    product = relationship("Product", back_populates="stock_levels")


class SalesVelocity(Base):
    __tablename__ = "sales_velocity"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    product_id    = Column(Integer, ForeignKey("products.id"), nullable=False)
    weekly_demand = Column(Float, nullable=False, default=0)   # combined across channels
    weekly_demand_clinic  = Column(Float)
    weekly_demand_hktv    = Column(Float)
    weekly_demand_shopify = Column(Float)
    trend_json    = Column(String)   # JSON [["YYYY-MM", units], ...]
    period_days   = Column(Integer, nullable=False, default=28)
    calculated_at = Column(String, nullable=False)
    source        = Column(String)   # 'shopify' | 'daysmart' | 'hktv' | 'algo_multichannel' | 'combined'

    product = relationship("Product", back_populates="sales_velocity")


class ExpiryTracking(Base):
    __tablename__ = "expiry_tracking"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    product_id  = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_ref   = Column(String)
    expiry_date = Column(String, nullable=False)   # YYYY-MM-DD
    qty         = Column(Float)
    location    = Column(String)
    created_at  = Column(String, nullable=False)

    product = relationship("Product", back_populates="expiry_tracking")


class CompetitorPrice(Base):
    __tablename__ = "competitor_prices"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    product_id      = Column(Integer, ForeignKey("products.id"), nullable=False)
    competitor_name = Column(String, nullable=False)
    channel         = Column(String)   # 'hktv' | 'shopify' | 'general'
    price           = Column(Float)    # last successfully-scraped selling price (HKD)
    url             = Column(String)   # competitor product-page link
    platform        = Column(String)   # detected: shopify | opencart | woocommerce | hktvmall | generic
    in_stock        = Column(Integer)  # 1 | 0 | NULL
    title           = Column(String)   # product title seen on the competitor page
    last_checked    = Column(String)   # YYYY-MM-DD of the last scrape attempt
    last_status     = Column(String)   # 'ok' | 'no price found' | 'error: ...'
    notes           = Column(String)
    created_at      = Column(String, nullable=False)
    updated_at      = Column(String, nullable=False)

    product = relationship("Product", back_populates="competitor_prices")


class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    product_id  = Column(Integer, ForeignKey("products.id"), nullable=False)
    location    = Column(String, nullable=False)
    delta       = Column(Float, nullable=False)   # positive = increase
    reason      = Column(String, nullable=False)
    adjusted_by = Column(String)
    adjusted_at = Column(String, nullable=False)

    product = relationship("Product", back_populates="stock_adjustments")


class Tag(Base):
    """A free-form (Shopify-style) product tag."""
    __tablename__ = "tags"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    slug       = Column(String, unique=True, nullable=False, index=True)
    label      = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

    product_links = relationship("ProductTag", back_populates="tag", cascade="all, delete-orphan")


class ProductTag(Base):
    """Many-to-many product↔tag, with provenance (AI vs human)."""
    __tablename__ = "product_tags"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    tag_id     = Column(Integer, ForeignKey("tags.id"), nullable=False, index=True)
    source     = Column(String, nullable=False, default='ai')   # ai | manual
    confidence = Column(Float, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('product_id', 'tag_id', name='uq_product_tag'),)

    tag     = relationship("Tag", back_populates="product_links")
    product = relationship("Product", back_populates="tag_links")


class Collection(Base):
    """A Shopify-style smart collection: a saved rule (JSON) that dynamically
    selects products. Membership is evaluated on the fly, never stored."""
    __tablename__ = "collections"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    name         = Column(String, nullable=False)
    slug         = Column(String, unique=True, nullable=False, index=True)
    description  = Column(String, nullable=True)
    rule_json    = Column(String, nullable=False)   # {"match":"all|any","conditions":[...]}
    is_smart     = Column(Integer, nullable=False, default=1)
    ai_generated = Column(Integer, nullable=False, default=0)
    created_by   = Column(String, nullable=True)
    created_at   = Column(String, nullable=False)
    updated_at   = Column(String, nullable=False)