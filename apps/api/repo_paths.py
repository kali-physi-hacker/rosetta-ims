"""Where the contract artifacts live, whichever layout this is checked out in.

`docs/contracts` holds the exported schema files, and four callers resolved it
by counting directories up from their own file: `parents[3]`. That is right in
exactly one layout. In the monorepo, `apps/api/scripts/x.py` counts up through
`apps/api` and `apps` to the repository root, where `docs/` sits. Once apps/api
becomes its own repository (DEV-414) the same count lands two levels ABOVE the
checkout, so the exporter writes its schemas into a directory nobody is looking
at and every committed artifact then reads as stale.

Looking for the directory works in both layouts, and keeps working through the
split without a flag day.
"""

from __future__ import annotations

from pathlib import Path

#: The import root — where `import models` resolves from.
BACKEND_ROOT = Path(__file__).resolve().parent


def docs_root() -> Path:
    """The directory `docs/contracts` sits in: the repository root, either way."""
    for candidate in (BACKEND_ROOT, *BACKEND_ROOT.parents):
        if (candidate / "docs" / "contracts").is_dir():
            return candidate
    # Nothing exported yet. Beside the code is where it goes once this is its
    # own repository, which is the layout everything is moving towards.
    return BACKEND_ROOT
