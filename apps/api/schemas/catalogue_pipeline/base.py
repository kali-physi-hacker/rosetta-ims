"""Base model and registry helpers for catalogue pipeline contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator


class ContractModel(BaseModel):
    """Base class for every CIS-103 Pydantic contract model."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    contract_id: ClassVar[str | None] = None

    @field_validator("*", mode="after")
    @classmethod
    def _datetimes_must_be_timezone_aware(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")
        return value


ContractType = TypeVar("ContractType", bound=type[ContractModel])
_CONTRACT_REGISTRY: dict[str, type[ContractModel]] = {}


def register_contract(model: ContractType) -> ContractType:
    """Register a public contract model by its stable contract identifier."""

    contract_id = getattr(model, "contract_id", None)
    if not contract_id:
        raise ValueError(f"{model.__name__} does not define contract_id")
    if contract_id in _CONTRACT_REGISTRY:
        raise ValueError(f"duplicate catalogue pipeline contract id: {contract_id}")
    _CONTRACT_REGISTRY[contract_id] = model
    return model


def get_contract_model(contract_id: str) -> type[ContractModel]:
    """Return the Pydantic model for a known contract identifier."""

    try:
        return _CONTRACT_REGISTRY[contract_id]
    except KeyError as exc:
        known = ", ".join(sorted(_CONTRACT_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown catalogue pipeline contract id '{contract_id}'. Known ids: {known}") from exc


def iter_contract_models() -> tuple[type[ContractModel], ...]:
    """Return public contract models in deterministic contract-id order."""

    return tuple(_CONTRACT_REGISTRY[key] for key in sorted(_CONTRACT_REGISTRY))


def registry_snapshot() -> dict[str, type[ContractModel]]:
    """Expose a copy of the public registry for tests and schema export."""

    return dict(_CONTRACT_REGISTRY)

