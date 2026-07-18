# -*- coding: utf-8 -*-
"""Interim product-family normaliser for the Demand Breakdown roll-up.

DaySmart has NO SKU storage and Dr Hugh's (CHS) export had none either; only Shopify carries SKUs (in the
sku field, not the title). So the SAME real product is fragmented across 3 naming systems. Until Rosetta
IMS + OCR + HITL build the canonical product master, this collapses the worst fragmentation by name so
Revolution/Cerenia/etc. roll up across clinic + online. Heuristic + curated — deliberately imperfect."""
import re

# single-product meds & preventatives — collapse every size/variant/source into one family
_SINGLE = [
    ("selehld", "Selehld"), ("revolution", "Revolution"), ("selamectin", "Revolution"),
    ("cerenia", "Cerenia"), ("maropitant", "Cerenia"),
    ("nexgard", "NexGard"), ("bravecto", "Bravecto"), ("simparica", "Simparica"), ("credelio", "Credelio"),
    ("frontline", "Frontline"), ("advocate", "Advocate"), ("seresto", "Seresto"), ("broadline", "Broadline"),
    ("heartgard", "Heartgard"), ("milbemax", "Milbemax"), ("milpro", "Milpro"), ("drontal", "Drontal"),
    ("proheart", "ProHeart"), ("interceptor", "Interceptor"), ("endogard", "Endogard"), ("profender", "Profender"),
    ("apoquel", "Apoquel"), ("cytopoint", "Cytopoint"), ("atopica", "Atopica"), ("ciclosporin", "Atopica"), ("cyclosporin", "Atopica"),
    ("metacam", "Metacam"), ("meloxicam", "Metacam"), ("onsior", "Onsior"), ("robenacoxib", "Onsior"),
    ("metronidazole", "Metronidazole"), ("doxycycline", "Doxycycline"),
    ("amoxyclav", "Amoxyclav"), ("clavamox", "Amoxyclav"), ("clavulanic", "Amoxyclav"),
    ("baytril", "Baytril"), ("enrofloxacin", "Baytril"), ("marbofloxacin", "Marbofloxacin"),
    ("cephalexin", "Cephalexin"), ("clindamycin", "Clindamycin"), ("convenia", "Convenia"), ("cefovecin", "Convenia"),
    ("gabapentin", "Gabapentin"), ("prednisolone", "Prednisolone"), ("vetmedin", "Vetmedin"), ("pimobendan", "Vetmedin"),
    ("fortekor", "Fortekor"), ("benazepril", "Fortekor"), ("semintra", "Semintra"), ("telmisartan", "Semintra"),
    ("nobivac", "Vaccine"), ("vaccin", "Vaccine"), ("rabies", "Vaccine"),
]
_DIET_LINES = [
    ("anallergenic", "Anallergenic"), ("renal", "Renal"), ("gastrointest", "GI"), ("hypoallergenic", "Hypoallergenic"),
    ("urinary", "Urinary"), ("s/o", "Urinary"), ("satiety", "Satiety"), ("hepatic", "Hepatic"), ("diabetic", "Diabetic"),
    ("dental", "Dental"), ("sensitiv", "Sensitivity"), ("mobility", "Mobility"), ("recovery", "Recovery"),
    ("metabolic", "Metabolic"), ("obesity", "Obesity"), ("calm", "Calm"), ("k/d", "Kidney"), ("i/d", "Digestive"),
    ("z/d", "Allergy"), ("c/d", "Urinary"), ("j/d", "Joint"),
]
_FOOD_BRANDS = ["stella", "orijen", "acana", "ziwi", "k9 natural", "feline natural", "instinct",
                "canagan", "applaws", "almo nature", "weruva", "tiki", "open farm"]

def product_family(name: str) -> str:
    n = (name or "").strip()
    nl = n.lower()
    if not nl:
        return n
    for k, v in _SINGLE:
        if k in nl:
            return v
    brand = None
    if "royal canin" in nl:
        brand = "Royal Canin"
    elif "hill" in nl and ("prescription" in nl or "science" in nl or "diet" in nl):
        brand = "Hill's"
    if brand:
        for k, v in _DIET_LINES:
            if k in nl:
                return f"{brand} {v} (Rx diet)"
        return f"{brand} (diet)"
    for b in _FOOD_BRANDS:
        if b in nl:
            return n.split(" - ")[0].split(",")[0].strip()[:32]
    return n   # tail: leave as-is (OCR/HITL will canonicalise later)

# a few Ohana product names carry the assigned Shopify SKU prepended, e.g. "50010287 - VetriScience…"
_LEAD_SKU = re.compile(r"^\s*([0-9]{4,}[A-Za-z0-9\-]*)\s*[-–:]\s+")

def leading_sku(name: str) -> str:
    m = _LEAD_SKU.match(name or "")
    return m.group(1) if m else ""
