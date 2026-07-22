"""
Models v2 - Organized model structure with separation of concerns.

This package contains all SQLAlchemy models organized by domain:
- account: User authentication and access control
- audit: Audit logging  
- catalogue: Catalogue imports, ingestion runs, and items
- configuration: Transformation engine configuration
- product: Products, inventory, and related entities
- supplier: Suppliers and their relationships
"""

# Account models
from .account import User, AccessAcknowledgement

# Audit models
from .audit import AuditLog

# Supplier models
from .supplier import Supplier, SupplierAlias, SupplierBrand, SupplierStockEvent

# Product models
from .product import (
    CategoryRule,
    Product,
    ProductSupplier,
    MbbTerm,
    ProductChannel,
    StockLevel,
    SalesVelocity,
    ExpiryTracking,
    CompetitorPrice,
    StockAdjustment,
    Tag,
    ProductTag,
    Collection
)

# Catalogue models - including the new CatalogueIngestionRun
from .catalogue import (
    CatalogueImport,
    CatalogueIngestionRun,  # NEW: Tracks individual ingestion attempts
    CatalogueCostStaging,
    CatalogueItem,
    CatalogueAuditEvent,
    ReparseBatch,
    ReparseChange
)

# Configuration models
from .configuration import (
    Transformation,
    ConfigVersion,
    TransformationValue
)

# Export all models
__all__ = [
    # Account
    'User',
    'AccessAcknowledgement',
    # Audit
    'AuditLog',
    # Supplier
    'Supplier',
    'SupplierAlias',
    'SupplierBrand',
    'SupplierStockEvent',
    # Product
    'CategoryRule',
    'Product',
    'ProductSupplier',
    'MbbTerm',
    'ProductChannel',
    'StockLevel',
    'SalesVelocity',
    'ExpiryTracking',
    'CompetitorPrice',
    'StockAdjustment',
    'Tag',
    'ProductTag',
    'Collection',
    # Catalogue  
    'CatalogueImport',
    'CatalogueIngestionRun',
    'CatalogueCostStaging',
    'CatalogueItem',
    'CatalogueAuditEvent',
    'ReparseBatch',
    'ReparseChange',
    # Configuration
    'Transformation',
    'ConfigVersion',
    'TransformationValue'
]