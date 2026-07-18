from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

import models
import database

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
def list_tags(db: Session = Depends(database.get_db)):
    """All tags with their product counts, most-used first. Powers autocomplete
    and the smart-collection rule builder."""
    rows = (db.query(models.Tag.slug, models.Tag.label, func.count(models.ProductTag.id))
            .outerjoin(models.ProductTag, models.ProductTag.tag_id == models.Tag.id)
            .group_by(models.Tag.id)
            .order_by(func.count(models.ProductTag.id).desc(), models.Tag.label)
            .all())
    return [{"slug": s, "label": l, "count": c} for s, l, c in rows]
