"""Move a price list to the supplier whose letterhead its pages actually carry.

Kangaroo Pet Nutrition and K.P.N. Trading are one group trading under two
names, and their files arrive named after both ("KPN_Kangaroo.pdf",
"Kangaroo _ KPN.pdf"). Two of them were uploaded to the wrong supplier, so each
was read by the other's contract: headings that contract does not declare, no
price found, and the rows held. Re-running changed nothing, because the run
kept the same wrong contract.

Nothing is guessed from the filename. The supplier is decided by the letterhead
VISION ALREADY CAPTURED on each page — the same `supplier_identity_text` the
conformance identity check reads — so this reports the evidence it acted on and
refuses anything that is not clear-cut.

    python scripts/reroute_misfiled_supplier_documents.py            # dry run
    python scripts/reroute_misfiled_supplier_documents.py --apply

Costs nothing at a provider: the corrected read is a re-parse of the stored
observations, never a re-scan of the pages.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import tempfile
import time
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/reroute.db")

import database  # noqa: E402
import models  # noqa: E402

#: The suppliers that share a printed identity, and the SUPPORTED contract each
#: one's own layout is read by.
FAMILY = {
    81: {"name": "Kangaroo Pet Nutrition", "marks": ("KANGAROO", "袋鼠"),
         "contract": "kangaroo_pet_nutrition.unit_price_list.v1"},
    15: {"name": "K.P.N. Trading", "marks": ("K.P.N", "KPN", "凱邦"),
         "contract": "kpn_trading.pack_price_list.v1"},
}
#: A document must be this lopsided before it is moved. Below it, the pages
#: genuinely disagree and a person should look rather than a script decide.
MAJORITY = 0.80

#: How long to let a catalogue worker claim the queued re-parse before running
#: it here instead. Production runs a dispatcher; a dev laptop usually does not.
WORKER_GRACE_SECONDS = 20
RUN_TIMEOUT_SECONDS = 900


def _letterheads(db, run_uuids):
    tally = collections.Counter()
    rows = (
        db.query(models.CatalogueExtractedEvidence.source_metadata_json)
        .filter(models.CatalogueExtractedEvidence.ingestion_run_uuid.in_(run_uuids))
        .all()
    )
    for (meta_json,) in rows:
        text = (json.loads(meta_json or "{}").get("supplier_identity_text") or "").strip()
        if not text:
            continue
        upper = text.upper()
        for supplier_id, spec in FAMILY.items():
            if any(mark in upper for mark in spec["marks"]):
                tally[supplier_id] += 1
                break
    return tally


def _drive(db, run_uuid: str) -> None:
    """See the queued re-parse through, whoever ends up running it.

    A host with the catalogue worker claims the run within seconds. Executing
    it inline as well makes both sides race for the same claim, which is
    exactly what happened the first time this script ran in production:
    DuplicateRunClaim, and the script reported 0 rows for a run the worker was
    busy completing correctly. So wait first, and only take the work if nothing
    else has after a grace period — which is the normal case on a laptop.
    """
    from models.ingestion_run import TERMINAL_STATUSES
    from orchestration.catalogue_flows import catalogue_ingestion_flow
    from orchestration.catalogue_types import DuplicateRunClaim

    started = time.time()
    while True:
        db.expire_all()
        run = db.query(models.IngestionRun).filter_by(run_uuid=run_uuid).first()
        if run is not None and run.status in TERMINAL_STATUSES:
            return
        waited = time.time() - started
        if run is not None and run.status == "queued" and waited > WORKER_GRACE_SECONDS:
            print(f"    no worker claimed it in {WORKER_GRACE_SECONDS}s — running it here")
            try:
                catalogue_ingestion_flow(ingestion_run_id=UUID(run_uuid))
            except DuplicateRunClaim:
                # A worker took it between the check and the claim. It owns the
                # run now; fall through and keep waiting for its result.
                pass
            continue
        if waited > RUN_TIMEOUT_SECONDS:
            print(f"    still {run.status if run else 'missing'} after "
                  f"{RUN_TIMEOUT_SECONDS}s — leaving it to the worker")
            return
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write. Without it, only report.")
    args = parser.parse_args()
    db = database.SessionLocal()
    try:
        documents = (
            db.query(models.CatalogueSourceDocument)
            .filter(models.CatalogueSourceDocument.supplier_id.in_(FAMILY))
            .order_by(models.CatalogueSourceDocument.id)
            .all()
        )
        planned = []
        for doc in documents:
            runs = db.query(models.IngestionRun).filter_by(catalogue_source_document_id=doc.id).all()
            if not runs:
                continue
            tally = _letterheads(db, [r.run_uuid for r in runs])
            total = sum(tally.values())
            print(f"\n{doc.filename}  (filed under {FAMILY[doc.supplier_id]['name']}, {len(runs)} run(s))")
            if not total:
                print("    no letterhead captured on any page — leaving alone")
                continue
            for supplier_id, n in tally.most_common():
                print(f"    {n:>5} page(s) printed {FAMILY[supplier_id]['name']}")
            winner, count = tally.most_common(1)[0]
            share = count / total
            if winner == doc.supplier_id:
                print(f"    -> correct already ({share:.0%})")
            elif share < MAJORITY:
                print(f"    -> MIXED ({share:.0%} for {FAMILY[winner]['name']}) — a person should decide, not this script")
            else:
                print(f"    -> MOVE to {FAMILY[winner]['name']} ({share:.0%}) and re-read with "
                      f"{FAMILY[winner]['contract']}")
                planned.append((doc, runs, winner))

        if not planned:
            print("\nNothing to reroute.")
            return 0
        if not args.apply:
            print(f"\nDry run. {len(planned)} document(s) would move. Re-run with --apply.")
            return 0

        from services.catalogue_submission import CatalogueSubmissionService

        for doc, runs, supplier_id in planned:
            contract = FAMILY[supplier_id]["contract"]
            newest = max(runs, key=lambda r: (str(r.created_at or ""), r.id))
            doc.supplier_id = supplier_id
            doc.supplier_source_contract_id = contract
            for run in runs:
                run.supplier_id = supplier_id
                if run.status not in ("queued", "running"):
                    run.status = "cancelled"
                    run.error_summary = (
                        "cancelled: read under the other supplier's contract; "
                        "superseded by the corrected re-read"
                    )
            db.commit()
            result = CatalogueSubmissionService(db).reparse(
                UUID(newest.run_uuid), from_stage="conformance", submitted_by="reroute",
                contract_id=contract, contract_version="v1")
            _drive(db, str(result.ingestion_run_id))
            fresh = db.query(models.IngestionRun).filter_by(run_uuid=str(result.ingestion_run_id)).first()
            db.refresh(fresh)
            fresh.parent_run_id = None   # this IS the current read of the document
            db.commit()
            rows = db.query(models.CatalogueNormalizedRow).filter_by(ingestion_run_uuid=fresh.run_uuid).count()
            cands = db.query(models.CatalogueMasteringCandidate).filter_by(ingestion_run_uuid=fresh.run_uuid).count()
            print(f"\n{doc.filename} -> {FAMILY[supplier_id]['name']} / {contract}")
            print(f"    run {fresh.run_uuid}  {fresh.status}  rows {rows}  candidates {cands}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
