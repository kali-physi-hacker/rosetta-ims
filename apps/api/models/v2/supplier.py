"""Supplier related models."""
from .base import Base, Column, Integer, String, Float, ForeignKey, UniqueConstraint, relationship


class Supplier(Base):
    __tablename__ = "suppliers"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    code          = Column(String, unique=True, nullable=False)  # e.g. "ALF"
    name          = Column(String, nullable=False)
    contact_name  = Column(String)
    contact_email = Column(String)
    lead_time_days = Column(Integer)
    created_at    = Column(String, nullable=False)

    # ── Supplier-master fields (imported from the vet / non-vet / consolidated sheets) ──
    normalized_name    = Column(String)   # matching key (casefold, suffixes stripped)
    segment            = Column(String)   # 'vet' | 'non_vet' | 'unknown'
    type_of_brand      = Column(String)
    # commercial terms (consolidated sheet wins on overlaps)
    moq_value          = Column(String)
    moq_specific       = Column(String)
    credit_term        = Column(String)
    monthly_rebate     = Column(String)
    bulk_buy_structure = Column(String)
    # logistics
    delivery_time      = Column(String)
    delivery_charges   = Column(String)
    warehouse_pickup   = Column(String)
    order_days         = Column(String)   # e.g. "Mon,Wed,Thu"
    delivery_days      = Column(String)
    cut_off_time       = Column(String)
    holidays           = Column(String)
    # contacts
    key_contact        = Column(String)
    contact_phone      = Column(String)
    contact_mobile     = Column(String)
    bank_details       = Column(String)
    supply_agreement   = Column(String)
    other_details      = Column(String)
    # meta
    is_active          = Column(Integer, default=1)
    source             = Column(String)   # 'sheet_import' | 'manual'
    updated_at         = Column(String)
    raw_json           = Column(String)   # raw imported row, for audit

    product_suppliers = relationship("ProductSupplier", back_populates="supplier")
    aliases = relationship("SupplierAlias", back_populates="supplier", cascade="all, delete-orphan")
    brand_links = relationship("SupplierBrand", back_populates="supplier", cascade="all, delete-orphan")


class SupplierAlias(Base):
    """Alternate names/spellings/codes that resolve to a supplier (for catalogue matching)."""
    __tablename__ = "supplier_aliases"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    supplier_id      = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    alias            = Column(String, nullable=False)
    normalized_alias = Column(String, nullable=False, index=True)
    source           = Column(String)   # 'parenthetical' | 'manual' | 'learned' | 'name'
    created_at       = Column(String)

    supplier = relationship("Supplier", back_populates="aliases")
    __table_args__ = (UniqueConstraint("supplier_id", "normalized_alias", name="uq_supplier_alias"),)


class SupplierBrand(Base):
    """Brands a supplier carries — the strongest catalogue->supplier matching signal."""
    __tablename__ = "supplier_brands"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    supplier_id      = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    brand_name       = Column(String, nullable=False)
    normalized_brand = Column(String, nullable=False, index=True)
    is_fmcg          = Column(Integer)  # 1 / 0 / None
    created_at       = Column(String)

    supplier = relationship("Supplier", back_populates="brand_links")
    __table_args__ = (UniqueConstraint("supplier_id", "normalized_brand", name="uq_supplier_brand"),)


class SupplierStockEvent(Base):
    """One out-of-stock period for a (SKU x supplier) link. restock_at NULL = still out.
    Backs the OOS history + durations shown on the item page."""
    __tablename__ = "supplier_stock_events"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    product_supplier_id = Column(Integer, ForeignKey("product_suppliers.id"), nullable=False, index=True)
    out_at              = Column(String, nullable=False)   # YYYY-MM-DD it went out of stock
    restock_at          = Column(String, nullable=True)    # YYYY-MM-DD it came back (NULL = ongoing)
    note                = Column(String, nullable=True)
    created_by          = Column(String, nullable=True)
    created_at          = Column(String, nullable=False)

    product_supplier = relationship("ProductSupplier", back_populates="stock_events")