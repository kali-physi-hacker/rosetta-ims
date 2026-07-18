"""Audit-log viewer + onboarding report — Admin only."""
import json
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

import database
import models
from permissions import require_capability

router = APIRouter(prefix="/audit", tags=["audit"])


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "").split("+")[0])
    except Exception:
        return None


def _verified_sku_set(db: Session) -> set:
    """SKUs currently HITL-verified — latest confirm/assign/unverify event wins."""
    rows = (db.query(models.CatalogueAuditEvent.sku_code, models.CatalogueAuditEvent.action)
            .filter(models.CatalogueAuditEvent.action.in_(["confirm_match", "assign_new", "hitl_verify", "hitl_unverify"]),
                    models.CatalogueAuditEvent.sku_code.isnot(None))
            .order_by(models.CatalogueAuditEvent.created_at).all())
    verified = set()
    for sku, action in rows:
        verified.discard(str(sku)) if action == "hitl_unverify" else verified.add(str(sku))
    return verified


def _row(r: models.AuditLog) -> dict:
    details = None
    if r.details:
        try:
            details = json.loads(r.details)
        except Exception:
            details = r.details
    return {
        "id":                 r.id,
        "created_at":         r.created_at,
        "action":             r.action,
        "actor_username":     r.actor_username,
        "actor_display_name": r.actor_display_name,
        "actor_role":         r.actor_role,
        "entity_type":        r.entity_type,
        "entity_id":          r.entity_id,
        "entity_label":       r.entity_label,
        "details":            details,
        "ip":                 r.ip,
        "user_agent":         r.user_agent,
    }


# ── category quick-filters ───────────────────────────────────────────────────────
# Onboarding/OCR events are already mirrored into AuditLog as "catalogue.*" by
# services.audit.log_event, so everything lives in one table — no merge needed.
_CAT_OCR_ACTIONS = ("catalogue.confirm_match", "catalogue.assign_new")   # OCR match / confirm


@router.get("")
def list_audit(action: str | None = Query(None),
               entity_type: str | None = Query(None),
               actor: str | None = Query(None),
               q: str | None = Query(None),
               category: str | None = Query(None, description="ocr_match | update | hitl"),
               limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(database.get_db),
               _: models.User = Depends(require_capability("audit_view"))):
    """Audit log with `category` quick-filters:
      ocr_match -> catalogue.confirm_match + catalogue.assign_new
      update    -> every product.* edit (single, batch, cost, uom, price, stock, tags)
      hitl      -> both of the above (human-verified actions)."""
    cat = (category or "").strip().lower()
    query = db.query(models.AuditLog)
    if cat == "ocr_match":
        query = query.filter(models.AuditLog.action.in_(_CAT_OCR_ACTIONS))
    elif cat == "update":
        query = query.filter(models.AuditLog.action.like("product.%"))
    elif cat == "hitl":
        query = query.filter(models.AuditLog.action.like("product.%")
                             | models.AuditLog.action.in_(_CAT_OCR_ACTIONS + ("catalogue.hitl_verify",)))
    if action:
        query = query.filter(models.AuditLog.action.like(f"{action}%"))
    if entity_type:
        query = query.filter(models.AuditLog.entity_type == entity_type)
    if actor:
        query = query.filter(models.AuditLog.actor_username == actor.strip().lower())
    if q:
        term = f"%{q}%"
        query = query.filter(
            models.AuditLog.entity_label.ilike(term)
            | models.AuditLog.details.ilike(term)
            | models.AuditLog.actor_username.ilike(term)
        )
    rows = query.order_by(models.AuditLog.created_at.desc()).limit(limit).all()
    return {"events": [_row(r) for r in rows]}


@router.get("/facets")
def facets(db: Session = Depends(database.get_db),
           _: models.User = Depends(require_capability("audit_view"))):
    """Distinct actions + actors for the filter dropdowns."""
    actions = sorted({a[0] for a in db.query(models.AuditLog.action).distinct().all() if a[0]})
    actors = sorted({a[0] for a in db.query(models.AuditLog.actor_username).distinct().all() if a[0]})
    return {"actions": actions, "actors": actors}


@router.get("/report/drill")
def report_drill(kind: str = Query(...),
                 status: str | None = Query(None),
                 reviewer: str | None = Query(None),
                 supplier: str | None = Query(None),
                 import_id: int | None = Query(None),
                 action: str | None = Query(None),
                 from_: str | None = Query(None, alias="from"),
                 to: str | None = Query(None),
                 limit: int = Query(800, ge=1, le=3000),
                 db: Session = Depends(database.get_db),
                 _: models.User = Depends(require_capability("audit_view"))):
    """The underlying list behind a report chart/figure (drill-down). Returns a normalised
    `items` list so the UI can render + link any drill uniformly."""
    CI, CA = models.CatalogueItem, models.CatalogueAuditEvent
    lo = (from_.strip() + "T00:00:00") if from_ else None
    hi = (to.strip() + "T23:59:59.999999") if to else None
    out, title = [], ""

    if kind == "items":
        q = db.query(CI)
        if status:
            q = q.filter(CI.review_status == status)
        if reviewer:
            q = q.filter(CI.reviewed_by == reviewer)
        if import_id:
            q = q.filter(CI.import_id == import_id)
        if supplier:
            sid = db.query(models.Supplier.id).filter(models.Supplier.name == supplier).scalar()
            q = q.filter(CI.supplier_id == sid)
        # range applies to processed items (reviewed_at); pending is a live snapshot
        if status != "pending":
            if lo: q = q.filter(CI.reviewed_at >= lo)
            if hi: q = q.filter(CI.reviewed_at <= hi)
        rows = q.order_by(CI.reviewed_at.desc(), CI.id.desc()).limit(limit).all()
        prod_sku = {p.id: p.sku_code for p in db.query(models.Product.id, models.Product.sku_code)
                    .filter(models.Product.id.in_([r.matched_product_id for r in rows if r.matched_product_id] or [0])).all()}
        sup_names = {s.id: s.name for s in db.query(models.Supplier).all()}
        for r in rows:
            sku = r.assigned_sku or prod_sku.get(r.matched_product_id)
            out.append({
                "label": r.raw_description or "(no description)",
                "sub": " · ".join(filter(None, [
                    f"SKU {r.supplier_sku}" if r.supplier_sku else None,
                    sup_names.get(r.supplier_id)])) or None,
                "meta": " · ".join(filter(None, [
                    r.review_status, (f"by {r.reviewed_by}" if r.reviewed_by else None),
                    (r.reviewed_at or "")[:16].replace("T", " ") or None])),
                "sku": sku,
                "href": f"/items/{sku}" if sku else None,
            })
        title = f"{(status or 'all').replace('_', ' ').title()} items" + (f" · {reviewer or supplier}" if (reviewer or supplier) else "")

    elif kind in ("to_verify", "verified"):
        verified = _verified_sku_set(db)
        prods = (db.query(models.Product)
                 .filter(models.Product.status == 'ACTIVE')
                 .order_by(models.Product.name).all())
        want_verified = kind == "verified"
        for p in prods:
            is_v = str(p.sku_code) in verified
            if is_v == want_verified:
                out.append({"label": p.name, "sub": p.brand or None,
                            "meta": " · ".join(filter(None, [p.category, p.sku_code])),
                            "sku": p.sku_code, "href": f"/items/{p.sku_code}"})
            if len(out) >= limit:
                break
        title = "SKUs to verify" if kind == "to_verify" else "Verified SKUs"

    elif kind == "actions":
        q = db.query(CA)
        if action:
            q = q.filter(CA.action == action)
        if lo: q = q.filter(CA.created_at >= lo)
        if hi: q = q.filter(CA.created_at <= hi)
        for e in q.order_by(CA.created_at.desc()).limit(limit).all():
            out.append({
                "label": e.sku_code or f"item #{e.item_id}",
                "sub": e.action, "meta": " · ".join(filter(None, [
                    (f"by {e.display_name or e.username}" if (e.display_name or e.username) else None),
                    (e.created_at or "")[:16].replace("T", " ")])),
                "sku": e.sku_code, "href": f"/items/{e.sku_code}" if e.sku_code else None})
        title = f"{action or 'all'} decisions".replace('_', ' ')

    return {"title": title, "count": len(out), "items": out, "truncated": len(out) >= limit}


@router.get("/report")
def onboarding_report(from_: str | None = Query(None, alias="from"),
                      to: str | None = Query(None),
                      db: Session = Depends(database.get_db),
                      _: models.User = Depends(require_capability("audit_view"))):
    """Comprehensive catalogue-onboarding + platform-usage report.

    Date range (`from`/`to`, YYYY-MM-DD) scopes the ACTIVITY (decisions, reviewers,
    suppliers, imports, timeline, sessions). The 'amount left' snapshot (pending /
    to-verify) is always the live now, since it's inherently present-tense."""
    CI, CA, AU = models.CatalogueItem, models.CatalogueAuditEvent, models.AuditLog
    lo = (from_.strip() + "T00:00:00") if from_ else None
    hi = (to.strip() + "T23:59:59.999999") if to else None
    ranged = bool(lo or hi)

    def between(q, col):
        if lo: q = q.filter(col >= lo)
        if hi: q = q.filter(col <= hi)
        return q

    # ── ACTIVITY in range: items processed (by reviewed_at) ──────────────────────
    act_q = db.query(CI.review_status, func.count()).filter(
        CI.review_status.in_(['matched', 'new_sku', 'rejected']))
    act_q = between(act_q, CI.reviewed_at)
    act_counts = dict(act_q.group_by(CI.review_status).all())
    matched  = act_counts.get('matched', 0)
    new_sku  = act_counts.get('new_sku', 0)
    rejected = act_counts.get('rejected', 0)

    # ── SNAPSHOT (always now): queue + verification ──────────────────────────────
    snap = dict(db.query(CI.review_status, func.count()).group_by(CI.review_status).all())
    pending = snap.get('pending', 0)
    extracted = sum(snap.values())
    imp_q = between(db.query(func.count(models.CatalogueImport.id)), models.CatalogueImport.imported_at)
    imports_total = imp_q.scalar() or 0

    verified = _verified_sku_set(db)
    active_skus = {str(s[0]) for s in db.query(models.Product.sku_code)
                   .filter(models.Product.status == 'ACTIVE').all()}
    verified_active = len(verified & active_skus)
    to_verify = len(active_skus) - verified_active

    # ── By decision action (catalogue_audit, in range) ───────────────────────────
    by_action = {a: c for a, c in between(db.query(CA.action, func.count()), CA.created_at)
                 .group_by(CA.action).all()}

    # ── By reviewer (items reviewed in range) ────────────────────────────────────
    rev = defaultdict(lambda: {"matched": 0, "new_sku": 0, "rejected": 0})
    rq = db.query(CI.reviewed_by, CI.review_status, func.count()).filter(
        CI.reviewed_by.isnot(None), CI.review_status.in_(['matched', 'new_sku', 'rejected']))
    for who, st, c in between(rq, CI.reviewed_at).group_by(CI.reviewed_by, CI.review_status).all():
        rev[who or '—'][st] = c
    by_reviewer = sorted([{"reviewer": k, **v, "total": sum(v.values())} for k, v in rev.items()],
                         key=lambda x: -x["total"])

    # ── By supplier — all items by default; reviewed-in-range when a range is set ─
    sup_names = {s.id: s.name for s in db.query(models.Supplier).all()}
    sup = defaultdict(lambda: {"matched": 0, "new_sku": 0, "rejected": 0, "pending": 0})
    sq = db.query(CI.supplier_id, CI.review_status, func.count())
    if ranged:
        sq = between(sq.filter(CI.review_status != 'pending'), CI.reviewed_at)
    for sid, st, c in sq.group_by(CI.supplier_id, CI.review_status).all():
        sup[sup_names.get(sid, "Unassigned")][st] += c
    by_supplier = sorted([{"supplier": k, **v, "total": sum(v.values())} for k, v in sup.items()],
                         key=lambda x: -x["total"])[:25]

    # ── By import (imported in range; counts are current item state) ─────────────
    imp_rows = (db.query(CI.import_id, CI.review_status, func.count())
                .group_by(CI.import_id, CI.review_status).all())
    imp_counts = defaultdict(lambda: {"matched": 0, "new_sku": 0, "rejected": 0, "pending": 0})
    for iid, st, c in imp_rows:
        imp_counts[iid][st] = c
    by_import = []
    iq = between(db.query(models.CatalogueImport), models.CatalogueImport.imported_at)
    for imp in iq.order_by(models.CatalogueImport.id.desc()).limit(60).all():
        cc = imp_counts.get(imp.id, {})
        by_import.append({
            "import_id": imp.id, "filename": imp.filename,
            "supplier": sup_names.get(imp.supplier_id, "—"),
            "imported_at": imp.imported_at,
            "extracted": sum(cc.values()),
            **{k: cc.get(k, 0) for k in ('matched', 'new_sku', 'rejected', 'pending')},
        })

    # ── Daily activity timeline (decisions in range) ─────────────────────────────
    tl = defaultdict(lambda: {"matched": 0, "new_sku": 0, "rejected": 0})
    for action, created in between(db.query(CA.action, CA.created_at).filter(
            CA.action.in_(['confirm_match', 'assign_new', 'reject'])), CA.created_at).all():
        d = (created or "")[:10]
        key = {"confirm_match": "matched", "assign_new": "new_sku", "reject": "rejected"}.get(action)
        if d and key:
            tl[d][key] += 1
    timeline = [{"date": d, **tl[d]} for d in sorted(tl)][-90:]

    # ── Platform usage: login/logout sessions per user (in range) ────────────────
    ev = between(db.query(AU.actor_username, AU.actor_display_name, AU.action, AU.created_at)
                 .filter(AU.action.in_(['login.success', 'logout', 'login.fail'])),
                 AU.created_at).order_by(AU.actor_username, AU.created_at).all()
    sessions = []
    per_user = defaultdict(lambda: {"display": None, "logins": 0, "failed": 0,
                                    "seconds": 0, "sessions": 0, "last_login": None})
    open_login = {}   # user -> the created_at of their last unmatched login.success
    for user, disp, action, created in ev:
        u = user or "—"
        pu = per_user[u]
        if disp:
            pu["display"] = disp
        if action == 'login.fail':
            pu["failed"] += 1
            continue
        if action == 'login.success':
            pu["logins"] += 1
            pu["last_login"] = created
            open_login[u] = created
        elif action == 'logout':
            login_at = open_login.pop(u, None)
            if login_at:
                t0, t1 = _parse(login_at), _parse(created)
                if t0 and t1 and t1 >= t0:
                    dur = (t1 - t0).total_seconds()
                    if dur <= 36 * 3600:   # ignore absurd spans (a left-open session)
                        pu["seconds"] += dur
                        pu["sessions"] += 1
                        sessions.append({"user": u, "display": pu["display"] or u,
                                         "login_at": login_at, "logout_at": created,
                                         "seconds": int(dur)})
    sessions_sorted = sorted(sessions, key=lambda s: s["logout_at"], reverse=True)[:40]
    by_user = sorted(
        [{"user": u, "display": v["display"] or u, "logins": v["logins"], "failed": v["failed"],
          "sessions": v["sessions"], "total_seconds": int(v["seconds"]),
          "avg_seconds": int(v["seconds"] / v["sessions"]) if v["sessions"] else 0,
          "last_login": v["last_login"]}
         for u, v in per_user.items()],
        key=lambda x: -x["total_seconds"])

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "range": {"from": from_, "to": to, "ranged": ranged},
        "onboarding": {
            # 'matched/new_sku/rejected/processed' are ACTIVITY in the selected range
            # (all-time when no range). 'pending/extracted/imports' describe the queue.
            "totals": {
                "imports": imports_total, "extracted": extracted,
                "matched": matched, "new_sku": new_sku, "rejected": rejected, "pending": pending,
                "processed": matched + new_sku + rejected,
            },
            # 'left' is the live snapshot — always now, never date-filtered.
            "left": {"pending_review": pending, "to_verify": to_verify,
                     "verified": verified_active, "active_products": len(active_skus)},
            "by_action": by_action,
            "by_reviewer": by_reviewer,
            "by_supplier": by_supplier,
            "by_import": by_import,
            "timeline": timeline,
        },
        "usage": {
            "by_user": by_user,
            "recent_sessions": sessions_sorted,
            "totals": {
                "logins": sum(u["logins"] for u in by_user),
                "failed_logins": sum(u["failed"] for u in by_user),
                "total_seconds": sum(u["total_seconds"] for u in by_user),
            },
        },
    }
