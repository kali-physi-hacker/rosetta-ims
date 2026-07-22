"""Configuration and transformation engine models."""
from .base import Base, Column, Integer, String, Float, ForeignKey, relationship


class Transformation(Base):
    """Registry of every configurable transformation (margin / cost / WOC …). Descriptive
    metadata for the config UI + the engine; the editable content lives in TransformationValue."""
    __tablename__ = "transformations"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    key          = Column(String, unique=True, nullable=False, index=True)  # e.g. unit_cost, net_margin
    name         = Column(String, nullable=False)
    description  = Column(String, nullable=True)
    category     = Column(String, nullable=False)   # cost | margin | inventory | classification
    output_field = Column(String, nullable=True)    # the field this produces
    input_vars   = Column(String, nullable=True)    # JSON list of variable names the formula may use
    kind         = Column(String, nullable=False)   # formula | parameter | table
    sort_order   = Column(Integer, nullable=False, default=0)


class ConfigVersion(Base):
    """One immutable snapshot of the whole transformation config. Exactly one row has
    is_active=1. Editing clones the active values into a new version; rollback flips is_active."""
    __tablename__ = "config_versions"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    created_at        = Column(String, nullable=False)
    created_by        = Column(String, nullable=True)
    note              = Column(String, nullable=True)
    parent_version_id = Column(Integer, ForeignKey("config_versions.id"), nullable=True)
    is_active         = Column(Integer, nullable=False, default=0)


class TransformationValue(Base):
    """The editable content of one transformation within one config version."""
    __tablename__ = "transformation_values"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    config_version_id  = Column(Integer, ForeignKey("config_versions.id"), nullable=False, index=True)
    transformation_key = Column(String, nullable=False, index=True)
    value_kind         = Column(String, nullable=False)   # formula | scalar | table
    formula_text       = Column(String, nullable=True)
    scalar_value       = Column(Float, nullable=True)
    table_json         = Column(String, nullable=True)