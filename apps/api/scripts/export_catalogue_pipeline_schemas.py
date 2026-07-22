"""Export deterministic JSON Schemas for CIS-103 catalogue pipeline contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from schemas.catalogue_pipeline import iter_contract_models  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "docs" / "contracts" / "catalogue-pipeline" / "v1"
SCHEMA_VERSION = "https://json-schema.org/draft/2020-12/schema"


def _schema_filename(contract_id: str) -> str:
    return f"{contract_id}.schema.json"


def _schema_text(model: type) -> str:
    schema = model.model_json_schema()
    contract_id = model.contract_id
    schema["$schema"] = SCHEMA_VERSION
    schema["$id"] = f"https://rosetta-ims.local/contracts/catalogue-pipeline/v1/{_schema_filename(contract_id)}"
    if model.__doc__:
        schema.setdefault("description", " ".join(model.__doc__.split()))
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_schemas(*, check: bool = False) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for model in iter_contract_models():
        path = OUTPUT_DIR / _schema_filename(model.contract_id)
        expected = _schema_text(model)
        if check:
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            if actual != expected:
                stale.append(str(path.relative_to(REPO_ROOT)))
            continue
        path.write_text(expected, encoding="utf-8")
        print(path.relative_to(REPO_ROOT))
    if stale:
        print("Stale catalogue pipeline schema files:", file=sys.stderr)
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

