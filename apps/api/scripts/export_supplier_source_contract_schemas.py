"""Export deterministic JSON Schemas for CIS-103B supplier-source contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from schemas.catalogue_pipeline.supplier_contracts import (  # noqa: E402
    SupplierSourceContractV1,
    iter_supplier_source_contracts,
)

OUTPUT_DIR = REPO_ROOT / "docs" / "contracts" / "catalogue-pipeline" / "supplier-source" / "v1"
SCHEMA_VERSION = "https://json-schema.org/draft/2020-12/schema"


def _schema_filename(contract_id: str) -> str:
    return f"{contract_id}.schema.json"


def _schema_text(contract_id: str, contract_version: str) -> str:
    schema = SupplierSourceContractV1.model_json_schema()
    schema["$schema"] = SCHEMA_VERSION
    schema["$id"] = (
        "https://rosetta-ims.local/contracts/catalogue-pipeline/"
        f"supplier-source/v1/{_schema_filename(contract_id)}"
    )
    schema["title"] = f"{contract_id} Supplier Source Contract"
    schema["description"] = (
        "JSON Schema derived from the authoritative SupplierSourceContractV1 "
        f"Pydantic model for {contract_id}@{contract_version}."
    )
    schema["properties"]["contract_id"] = {
        "const": contract_id,
        "description": "Stable supplier-format contract identity.",
        "title": "Contract Id",
        "type": "string",
    }
    schema["properties"]["contract_version"] = {
        "const": contract_version,
        "description": "Supplier-format major version.",
        "title": "Contract Version",
        "type": "string",
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_schemas(*, check: bool = False) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for registration in iter_supplier_source_contracts():
        path = OUTPUT_DIR / _schema_filename(registration.contract_id)
        expected = _schema_text(registration.contract_id, registration.contract_version)
        if check:
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            if actual != expected:
                stale.append(str(path.relative_to(REPO_ROOT)))
            continue
        path.write_text(expected, encoding="utf-8")
        print(path.relative_to(REPO_ROOT))
    if stale:
        print("Stale supplier-source schema files:", file=sys.stderr)
        for item in stale:
            print(f"  {item}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed schema files are stale.")
    args = parser.parse_args()
    return export_schemas(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
