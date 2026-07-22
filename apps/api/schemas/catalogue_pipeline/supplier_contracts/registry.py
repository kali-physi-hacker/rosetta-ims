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
        self._supplier_format_identities: dict[tuple[str, SupplierDocumentType, str, str], tuple[str, str]] = {}

    def register(self, declaration: SupplierSourceContractV1) -> SupplierSourceContractV1:
        """Register a supplier-format declaration, rejecting duplicate or conflicting identities."""

        _validate_declaration_identity(declaration)
        key = (declaration.contract_id, declaration.contract_version)
        if key in self._registrations:
            raise ValueError(f"duplicate supplier source contract registration: {declaration.contract_id}@{declaration.contract_version}")
        format_key = _supplier_format_identity(declaration)
        existing_key = self._supplier_format_identities.get(format_key)
        if existing_key is not None:
            existing_id, existing_version = existing_key
            raise ValueError(
                "conflicting supplier source contract identity for "
                f"{_format_key_label(format_key)}: existing {existing_id}@{existing_version}, "
                f"new {declaration.contract_id}@{declaration.contract_version}"
            )
        supplier_code_or_id = declaration.supplier.supplier_code or str(declaration.supplier.supplier_id)
        registration = SupplierSourceContractRegistration(
            contract_id=declaration.contract_id,
            contract_version=declaration.contract_version,
            supplier_code_or_id=supplier_code_or_id,
            document_type=declaration.document_type,
            support_status=declaration.support_status,
            declaration=declaration,
        )
        _assert_registration_matches_declaration(registration)
        self._registrations[key] = registration
        self._supplier_format_identities[format_key] = key
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


def _validate_declaration_identity(declaration: SupplierSourceContractV1) -> None:
    if not declaration.contract_id.strip():
        raise ValueError("supplier source contract_id cannot be blank")
    if not declaration.contract_version.strip():
        raise ValueError("supplier source contract_version cannot be blank")
    if not declaration.format_name.strip():
        raise ValueError("supplier source format_name cannot be blank")
    if declaration.support_status == SupplierContractSupportStatus.SUPPORTED and declaration.supplier.supplier_id is None:
        raise ValueError("SUPPORTED supplier source contracts require supplier_id for runtime selection")


def _supplier_identity(declaration: SupplierSourceContractV1) -> str:
    supplier = declaration.supplier
    if supplier.supplier_id is not None:
        return f"id:{supplier.supplier_id}"
    return f"code:{supplier.supplier_code}"


def _supplier_format_identity(declaration: SupplierSourceContractV1) -> tuple[str, SupplierDocumentType, str, str]:
    return (
        _supplier_identity(declaration),
        declaration.document_type,
        declaration.format_name.strip().casefold(),
        declaration.contract_version,
    )


def _format_key_label(format_key: tuple[str, SupplierDocumentType, str, str]) -> str:
    supplier, document_type, format_name, version = format_key
    return f"supplier={supplier}, document_type={document_type.value}, format={format_name}, version={version}"


def _assert_registration_matches_declaration(registration: SupplierSourceContractRegistration) -> None:
    declaration = registration.declaration
    supplier_code_or_id = declaration.supplier.supplier_code or str(declaration.supplier.supplier_id)
    if registration.contract_id != declaration.contract_id:
        raise ValueError("registration contract_id contradicts declaration")
    if registration.contract_version != declaration.contract_version:
        raise ValueError("registration contract_version contradicts declaration")
    if registration.supplier_code_or_id != supplier_code_or_id:
        raise ValueError("registration supplier identity contradicts declaration")
    if registration.document_type != declaration.document_type:
        raise ValueError("registration document_type contradicts declaration")
    if registration.support_status != declaration.support_status:
        raise ValueError("registration support_status contradicts declaration")
