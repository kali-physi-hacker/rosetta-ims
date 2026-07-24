"""Config-driven transformation engine — read + edit API (Phase B).

Business owners view every transformation and edit the PARAMETERS and TABLES (fees, thresholds,
staleness window, SF tiers) live. Each edit writes a NEW active config version (history
preserved), so rollback is one call. Formula editing arrives in Phase C.

RED ZONE: these endpoints change the live money/margin config. Mutations require the
`config_admin` capability, are audited, and invalidate the engine cache so changes take effect
immediately (the business chose live application).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import database
import models
from permissions import require_capability
from services import transform_engine as engine
from services import audit_log

router = APIRouter(prefix="/config", tags=["config"])


class EditBody(BaseModel):
    value:   Optional[float] = None   # for parameter transformations
    table:   Optional[dict]  = None   # for table transformations (e.g. sf_logistics)
    formula: Optional[str]   = None   # for formula transformations (Phase C, sandboxed)
    note:    Optional[str]   = None


@router.get("/transformations")
def list_transformations(db: Session = Depends(database.get_db)):
    """Registry + the active config's value/formula/table per transformation (for the UI)."""
    return {"transformations": engine.list_config(db)}


@router.get("/versions")
def list_versions(db: Session = Depends(database.get_db)):
    return {"versions": engine.list_versions(db)}


@router.post("/validate")
def validate(key: str, body: EditBody, db: Session = Depends(database.get_db),
             current_user: models.User = Depends(require_capability("config_admin"))):
    """Dry-run validate a proposed parameter/table edit — never writes."""
    t = db.query(models.Transformation).filter_by(key=key).first()
    if t is None:
        raise HTTPException(status_code=404, detail=f"unknown transformation: {key}")
    try:
        if t.kind == "parameter":
            engine.validate_param(key, body.value)
        elif t.kind == "table":
            engine.validate_table(key, body.table)
        elif t.kind == "formula":
            engine.validate_formula(key, body.formula)
        else:
            raise ValueError(f"{key} is not editable")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@router.put("/transformations/{key}")
def edit_transformation(key: str, body: EditBody, request: Request,
                        db: Session = Depends(database.get_db),
                        current_user: models.User = Depends(require_capability("config_admin"))):
    """Live-edit a parameter, table, or formula: validates, writes a new active version, audits."""
    try:
        new, before, after = engine.edit_value(
            db, key, value=body.value, table=body.table, formula=body.formula,
            editor=current_user.display_name, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_log.record(db, action="config.transform.update", actor=current_user,
                     entity_type="config", entity_id=key, entity_label=key,
                     details={"before": before, "after": after, "version": new.id, "note": body.note},
                     request=request)
    db.commit()
    engine.invalidate()   # live: subsequent calculations use the new value immediately
    return {"key": key, "before": before, "after": after, "version_id": new.id}


@router.post("/versions/{version_id}/restore")
def restore(version_id: int, request: Request, db: Session = Depends(database.get_db),
            current_user: models.User = Depends(require_capability("config_admin"))):
    """Roll back to a prior config version (creates a new active version; audited)."""
    try:
        new = engine.restore_version(db, version_id, editor=current_user.display_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    audit_log.record(db, action="config.version.restore", actor=current_user,
                     entity_type="config", entity_id=str(version_id), entity_label=f"restore v{version_id}",
                     details={"restored_from": version_id, "new_version": new.id}, request=request)
    db.commit()
    engine.invalidate()
    return {"restored_from": version_id, "new_version_id": new.id}
