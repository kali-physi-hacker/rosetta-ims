#!/usr/bin/env python3
"""
Batch catalogue OCR importer.

Walks a folder of supplier catalogues (Region/Supplier/[Brand]/file...) and POSTs
each supported file to POST /catalogues/import, so the AI extraction runs and the
items land in the existing review queue. Folder name → supplier is inferred and,
when it matches an existing IMS supplier, sent as supplier_id (improves matching).

SAFE BY DEFAULT: this is a DRY RUN unless you pass --execute. Dry run hits no
upload endpoint and spends nothing — it just prints the plan.

Real OCR requires ANTHROPIC_API_KEY set in the *backend* environment. Without it
every import returns a stub (no extraction) — the items still appear in the queue
but need manual entry.

Examples
--------
# See the plan (no uploads, no cost):
python scripts/batch_import_catalogues.py "../Supplier Catalogue"

# Validate on one supplier, for real:
python scripts/batch_import_catalogues.py "../Supplier Catalogue" \
    --supplier Arrowana --execute

# Full run against local backend:
python scripts/batch_import_catalogues.py "../Supplier Catalogue" --execute
"""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

# Extensions the backend extractor actually handles (extraction_service.extract).
# docx/ini/psd/pptx are intentionally excluded — they extract to garbage or nothing.
SUPPORTED = {".png", ".jpg", ".jpeg", ".pdf", ".xlsx", ".xls", ".csv"}

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".csv": "text/csv",
}


def login(base_url: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base_url}/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["token"]


def fetch_suppliers(base_url: str, token: str) -> list[dict]:
    req = urllib.request.Request(
        f"{base_url}/suppliers", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except Exception:
        return []


def match_supplier_id(folder_name: str, suppliers: list[dict]) -> int | None:
    """Fuzzy folder→supplier: case-insensitive substring either direction."""
    fn = folder_name.lower().strip()
    # strip common noise like "(Reseller)" / "Ltd"
    fn_core = fn.replace("(reseller)", "").replace(" ltd", "").strip()
    for s in suppliers:
        name = (s.get("name") or "").lower().strip()
        if not name:
            continue
        if fn_core and (fn_core in name or name in fn_core):
            return s.get("id")
    return None


def build_multipart(file_path: str, supplier_id: int | None) -> tuple[bytes, str]:
    """Encode one file (+ optional supplier_id) as multipart/form-data."""
    boundary = "----imsbatch" + str(int(time.time() * 1000))
    ext = os.path.splitext(file_path)[1].lower()
    ctype = MIME.get(ext) or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        data = f.read()
    crlf = b"\r\n"
    parts: list[bytes] = []
    if supplier_id is not None:
        parts += [f"--{boundary}".encode(), crlf,
                  b'Content-Disposition: form-data; name="supplier_id"', crlf, crlf,
                  str(supplier_id).encode(), crlf]
    fname = os.path.basename(file_path)
    parts += [
        f"--{boundary}".encode(), crlf,
        f'Content-Disposition: form-data; name="file"; filename="{fname}"'.encode(), crlf,
        f"Content-Type: {ctype}".encode(), crlf, crlf,
        data, crlf,
        f"--{boundary}--".encode(), crlf,
    ]
    return b"".join(parts), boundary


def upload(base_url: str, token: str, file_path: str, supplier_id: int | None) -> dict:
    body, boundary = build_multipart(file_path, supplier_id)
    req = urllib.request.Request(
        f"{base_url}/catalogues/import", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


def discover(root: str, region: str | None, supplier: str | None,
             types: set[str], limit: int | None) -> list[tuple[str, str, str]]:
    """Return [(file_path, region, supplier_folder)] for matching files."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        parts = [] if rel == "." else rel.split(os.sep)
        f_region = parts[0] if len(parts) >= 1 else ""
        f_supplier = parts[1] if len(parts) >= 2 else (parts[0] if parts else "")
        if region and region.lower() not in f_region.lower():
            continue
        if supplier and supplier.lower() not in f_supplier.lower():
            continue
        for name in sorted(files):
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in types:
                continue
            out.append((os.path.join(dirpath, name), f_region.strip(), f_supplier.strip()))
    out.sort()
    return out[:limit] if limit else out


def main():
    ap = argparse.ArgumentParser(description="Batch catalogue OCR importer")
    ap.add_argument("root", help="Catalogue root folder (Region/Supplier/...)")
    ap.add_argument("--base-url", default="http://localhost:8001")
    ap.add_argument("--username", default="seph")
    ap.add_argument("--password", default="rosetta2024")
    ap.add_argument("--execute", action="store_true",
                    help="Actually upload. Without this it's a dry run (default).")
    ap.add_argument("--region", help="Only files under this region folder (substring)")
    ap.add_argument("--supplier", help="Only files under this supplier folder (substring)")
    ap.add_argument("--limit", type=int, help="Cap number of files")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="Seconds to sleep between dispatches (rate-limit cushion)")
    ap.add_argument("--types", default=",".join(sorted(SUPPORTED)),
                    help="Comma-separated extensions (with dot)")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        sys.exit(f"Not a directory: {args.root}")

    types = {t if t.startswith(".") else "." + t
             for t in (x.strip().lower() for x in args.types.split(",")) if t}
    plan = discover(args.root, args.region, args.supplier, types, args.limit)
    if not plan:
        sys.exit("No matching files found.")

    # Per-supplier breakdown
    by_sup: dict[str, int] = {}
    for _p, _r, sup in plan:
        by_sup[sup] = by_sup.get(sup, 0) + 1

    print(f"\nRoot:    {args.root}")
    print(f"Backend: {args.base_url}")
    print(f"Files:   {len(plan)} ({', '.join(sorted(types))})")
    print("Per supplier folder:")
    for sup, n in sorted(by_sup.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {sup}")

    token = login(args.base_url, args.username, args.password)
    suppliers = fetch_suppliers(args.base_url, token)
    sup_ids = {sup: match_supplier_id(sup, suppliers) for sup in by_sup}
    matched = sum(1 for v in sup_ids.values() if v is not None)
    print(f"\nSupplier folders matched to an IMS supplier_id: {matched}/{len(by_sup)}"
          + ("" if suppliers else "  (no suppliers in DB — run a Sheet sync first to enable matching)"))

    if not args.execute:
        print("\nDRY RUN — nothing uploaded. Re-run with --execute to import.")
        print("Reminder: real OCR needs ANTHROPIC_API_KEY set in the backend env.\n")
        return

    print(f"\nUploading {len(plan)} files (concurrency={args.concurrency})...\n")
    ok = err = total_items = 0
    failures: list[str] = []

    def work(entry):
        path, _region, sup = entry
        if args.delay:
            time.sleep(args.delay)
        try:
            res = upload(args.base_url, token, path, sup_ids.get(sup))
            return (path, res, None)
        except urllib.error.HTTPError as e:
            return (path, None, f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")
        except Exception as e:
            return (path, None, f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, e) for e in plan]
        for i, fut in enumerate(as_completed(futs), 1):
            path, res, error = fut.result()
            short = os.path.relpath(path, args.root)
            if error:
                err += 1
                failures.append(f"{short} → {error}")
                print(f"[{i}/{len(plan)}] ✗ {short} — {error}")
            else:
                ok += 1
                n = res.get("item_count", 0)
                total_items += n
                ai = "AI" if res.get("ai_enabled") else "stub"
                print(f"[{i}/{len(plan)}] ✓ {short} — {n} items ({ai}) import#{res.get('import_id')}")

    print(f"\nDone. {ok} ok, {err} failed, {total_items} items extracted total.")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  {f}")
    print("\nReview everything in the queue: /catalogues  (or GET /catalogues/queue/pending)\n")


if __name__ == "__main__":
    main()
