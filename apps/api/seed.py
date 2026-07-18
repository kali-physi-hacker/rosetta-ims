"""
Seed category rules and suppliers. Run on first setup or to reset reference data.
Usage: python seed.py
"""
from datetime import datetime
from database import SessionLocal, engine
import models

models.Base.metadata.create_all(bind=engine)

# IMS operational item categories — the single category list the picker offers.
CATEGORY_RULES = [
    dict(category='Medicine',     gp_floor=0.70, storage_rule='clinic_only', channel_restriction='clinic'),
    dict(category='Preventative', gp_floor=0.40, storage_rule='any',         channel_restriction=None),
    dict(category='Supplement',   gp_floor=0.40, storage_rule='any',         channel_restriction=None),
    dict(category='Shampoo',      gp_floor=0.40, storage_rule='any',         channel_restriction=None),
    dict(category='Food',         gp_floor=0.35, storage_rule='any',         channel_restriction=None),
    dict(category='Not-For-Sale', gp_floor=0.00, storage_rule='any',         channel_restriction=None),
    dict(category='Pet Hygiene',  gp_floor=0.40, storage_rule='any',         channel_restriction=None),
    dict(category='Cat Litter',   gp_floor=0.35, storage_rule='any',         channel_restriction=None),
    dict(category='Others',       gp_floor=0.40, storage_rule='any',         channel_restriction=None),
]

SUPPLIERS = [
    dict(code='ALF', name='Alfamedic'),
    dict(code='ARR', name="Arrowana Int'l Ltd"),
    dict(code='AVM', name='Asia Vet Medical Limited'),
    dict(code='CVP', name='C. Vetapet & Company'),
    dict(code='BPC', name='Blue Pet Co'),
    dict(code='BGB', name='BuggyBix'),
    dict(code='CAE', name='Caesars'),
    dict(code='ETT', name='Etta International'),
    dict(code='HPI', name='Happypaws Int\'l Ltd'),
]

def seed():
    db = SessionLocal()
    now = datetime.utcnow().isoformat()
    try:
        # Category rules — upsert by primary key
        for rule in CATEGORY_RULES:
            existing = db.query(models.CategoryRule).filter(
                models.CategoryRule.category == rule['category']
            ).first()
            if existing:
                existing.gp_floor = rule['gp_floor']
                existing.storage_rule = rule['storage_rule']
                existing.channel_restriction = rule['channel_restriction']
            else:
                db.add(models.CategoryRule(**rule))

        # Suppliers — upsert by code
        for sup in SUPPLIERS:
            existing = db.query(models.Supplier).filter(
                models.Supplier.code == sup['code']
            ).first()
            if not existing:
                db.add(models.Supplier(**sup, created_at=now))

        db.commit()
        print(f"Seeded {len(CATEGORY_RULES)} category rules and {len(SUPPLIERS)} suppliers.")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
