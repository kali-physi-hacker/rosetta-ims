"""Base model and registry for purchase-order contracts (DEV-305).

Deliberately self-contained: this package copies the CIS-103 *discipline*
(versioned contract ids, frozen models, no floats, tz-aware timestamps) but
imports nothing from ``schemas.catalogue_pipeline``. The PO contracts are
pinned paper for the Daysmart sync — a catalogue base-class edit must never
be able to change what "a purchase order" validates as.
"""

from __future__ import annotations

from typing import ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base class for every purchase-order contract model.

    Frozen: parsed contract values are evidence, never mutated in place.
    ``extra="forbid"`` guards *our* construction sites — tolerance for
    Daysmart adding fields lives in the parse layer, on the raw dict,
    before a model is ever built.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: ClassVar[str | None] = None


ContractType = TypeVar("ContractType", bound=type[ContractModel])
_CONTRACT_REGISTRY: dict[str, type[ContractModel]] = {}


def register_contract(model: ContractType) -> ContractType:
    """Register a public contract model by its stable contract identifier."""
    contract_id = getattr(model, "contract_id", None)
    if not contract_id:
        raise ValueError(f"{model.__name__} does not define contract_id")
    if contract_id in _CONTRACT_REGISTRY:
        raise ValueError(f"duplicate purchase-order contract id: {contract_id}")
    _CONTRACT_REGISTRY[contract_id] = model
    return model


def get_contract_model(contract_id: str) -> type[ContractModel]:
    """Return the model for a known contract identifier."""
    try:
        return _CONTRACT_REGISTRY[contract_id]
    except KeyError as exc:
        known = ", ".join(sorted(_CONTRACT_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown purchase-order contract id '{contract_id}'. Known ids: {known}") from exc
