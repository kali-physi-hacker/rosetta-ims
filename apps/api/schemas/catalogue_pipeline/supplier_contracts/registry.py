"""Registry for supplier-specific source contract declarations."""

from __future__ import annotations

from dataclasses import dataclass

from .common import SupplierContractSupportStatus, SupplierDocumentType, SupplierSourceContractV1


@dataclass(frozen=True)
class SupplierSourceContractRegistration:
    """Registry metadata for one supplier-format contract declaration."""

    contract_id: str
    contract_version: str
    supplier_code_or_id: str
    document_type: SupplierDocumentType
    support_status: SupplierContractSupportStatus
    declaration: SupplierSourceContractV1
    model: type[SupplierSourceContractV1] = SupplierSourceContractV1


class SupplierSourceContractRegistry:
    """Deterministic registry keyed by supplier-format contract ID and version."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[str, str], SupplierSourceContractRegistration] = {}

    def register(self, declaration: SupplierSourceContractV1) -> SupplierSourceContractV1:
        """Register a supplier-format declaration, rejecting duplicate IDs."""

        key = (declaration.contract_id, declaration.contract_version)
        if key in self._registrations:
            raise ValueError(f"duplicate supplier source contract registration: {declaration.contract_id}@{declaration.contract_version}")
        supplier_code_or_id = declaration.supplier.supplier_code or str(declaration.supplier.supplier_id)
        self._registrations[key] = SupplierSourceContractRegistration(
            contract_id=declaration.contract_id,
            contract_version=declaration.contract_version,
            supplier_code_or_id=supplier_code_or_id,
            document_type=declaration.document_type,
            support_status=declaration.support_status,
            declaration=declaration,
        )
        return declaration

    def get(self, contract_id: str, contract_version: str = "v1") -> SupplierSourceContractRegistration:
        """Return a known registration or fail with a useful error."""

        key = (contract_id, contract_version)
        try:
            return self._registrations[key]
        except KeyError as exc:
            known = ", ".join(f"{cid}@{ver}" for cid, ver in sorted(self._registrations)) or "(none)"
            raise ValueError(
                f"Unknown supplier source contract '{contract_id}@{contract_version}'. Known contracts: {known}"
            ) from exc

    def get_supported(self, contract_id: str, contract_version: str = "v1") -> SupplierSourceContractRegistration:
        """Return a registration only when it is production-selectable."""

        registration = self.get(contract_id, contract_version)
        if registration.support_status != SupplierContractSupportStatus.SUPPORTED:
            raise ValueError(
                f"Supplier source contract '{contract_id}@{contract_version}' is "
                f"{registration.support_status.value}, not SUPPORTED"
            )
        return registration

    def list(self, *, include_deprecated: bool = True) -> tuple[SupplierSourceContractRegistration, ...]:
        """List registrations in deterministic contract-id order."""

        registrations = tuple(self._registrations[key] for key in sorted(self._registrations))
        if include_deprecated:
            return registrations
        return tuple(
            item for item in registrations
            if item.support_status != SupplierContractSupportStatus.DEPRECATED
        )

    def snapshot(self) -> dict[tuple[str, str], SupplierSourceContractRegistration]:
        """Return a copy of the registry for tests and schema export."""

        return dict(self._registrations)


_REGISTRY = SupplierSourceContractRegistry()


def register_supplier_source_contract(declaration: SupplierSourceContractV1) -> SupplierSourceContractV1:
    """Register a public supplier-source contract declaration."""

    return _REGISTRY.register(declaration)


def get_supplier_source_contract(
    contract_id: str,
    contract_version: str = "v1",
) -> SupplierSourceContractRegistration:
    """Return a known supplier-source contract registration."""

    return _REGISTRY.get(contract_id, contract_version)


def get_supported_supplier_source_contract(
    contract_id: str,
    contract_version: str = "v1",
) -> SupplierSourceContractRegistration:
    """Return a supplier-source contract only when it is production-selectable."""

    return _REGISTRY.get_supported(contract_id, contract_version)


def iter_supplier_source_contracts(*, include_deprecated: bool = True) -> tuple[SupplierSourceContractRegistration, ...]:
    """Return supplier-source registrations in deterministic order."""

    return _REGISTRY.list(include_deprecated=include_deprecated)


def supplier_source_registry_snapshot() -> dict[tuple[str, str], SupplierSourceContractRegistration]:
    """Return a copy of the public supplier-source registry."""

    return _REGISTRY.snapshot()
