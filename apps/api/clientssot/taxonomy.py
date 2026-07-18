# -*- coding: utf-8 -*-
"""Client SSOT care taxonomy — MAIN category -> SUB-categories (mirrors Rosetta product classes).
Built bottom-up from CHS history + DaySmart live + product names, framed commercially.
`classify(text)` maps a free-text clinic reason / product name to (kind, main, sub).
kind: 'care' (tag the pet) | 'event' (pet timeline) | 'engagement' (ops axis, not care) | None.
v2 (2026-06-20): closed CHS gaps (EARS plural, CHECK UP, SOFT STOOL, DTM/ringworm, HOSPITAL DAY,
HEAD TILT, VAC/FVCRP typos) + added Endocrine & Neurological mains. Pending Chris's review."""
import re

# MAIN -> [SUB]  (the tree the Clientbase UI renders, like product category -> sub-category)
TAXONOMY = {
    "Skin & Coat":        ["Allergy / Atopy", "Skin infection / Dermatitis", "Lumps & Wounds", "Grooming & Shampoo"],
    "Digestive":          ["GI upset", "Digestive diet", "Liver / Hepatic"],
    "Urinary & Renal":    ["Urinary / FLUTD", "Renal / Kidney (CKD)"],
    "Dental":             ["Dental disease", "Dental care & chews"],
    "Eyes & Ears":        ["Eye", "Ear"],
    "Mobility":           ["Joint / Arthritis"],
    "Heart":              ["Cardiac"],
    "Respiratory":        ["Respiratory"],
    "Endocrine":          ["Diabetes", "Thyroid"],
    "Neurological":       ["Seizure / Neuro", "Vestibular / Head tilt"],
    "Preventative":       ["Vaccination", "Parasite control"],
    "Weight & Nutrition": ["Weight management", "Senior / Age", "Life-stage diet"],
    "Behaviour":          ["Calming / Anxiety"],
    "Cat-specific":       ["Hairball"],
}
EVENTS     = ["Surgery", "Wellness / Recheck", "Diagnostics", "Hospitalisation", "Health certificate"]
ENGAGEMENT = ["Rx request", "Question", "History request", "Insurance", "Discharge"]

# ordered (regex, kind, main, sub) — first match wins; specific before generic
RULES = [
    (r"cytopoint|apoquel|atop|allerg",                          "care", "Skin & Coat", "Allergy / Atopy"),
    (r"dermat|pyoderma|hot spot|ringworm|dermatophyte|\bdtm\b|fungal|mange|mite|scab", "care", "Skin & Coat", "Skin infection / Dermatitis"),
    (r"lump|mass|wound|pressure sore|abscess|\bcyst\b|tumou?r|growth",  "care", "Skin & Coat", "Lumps & Wounds"),
    (r"shampoo|groom|\bcoat\b|bath",                            "care", "Skin & Coat", "Grooming & Shampoo"),
    (r"\bskin\b|pruritus|itch|alopecia|hair loss",              "care", "Skin & Coat", "Skin infection / Dermatitis"),

    (r"renal|kidney|\bckd\b|sc fluid|\bk/d\b|nephr",            "care", "Urinary & Renal", "Renal / Kidney (CKD)"),
    (r"urin|flutd|haematuria|hematuria|cysto|bladder|\bs/o\b|\bu/d\b|stones?", "care", "Urinary & Renal", "Urinary / FLUTD"),

    (r"liver|hepat|\bl/d\b|jaundice",                           "care", "Digestive", "Liver / Hepatic"),
    (r"\bd\+|diarr|\bv\+|vomit|not eating|inappet|appetite|\bgi\b|gastro|nausea|\bi/d\b|soft stool|\bstool|faec|constipat|colitis|pancrea", "care", "Digestive", "GI upset"),
    (r"probiotic|digest",                                       "care", "Digestive", "Digestive diet"),

    (r"dental|\bteeth\b|trim teeth|\btooth|tartar|plaque|gingiv|periodont|dental scal", "care", "Dental", "Dental disease"),

    (r"\beyes?\b|ocular|ophthal|conjunctiv|cornea|tear|cataract|glaucoma",  "care", "Eyes & Ears", "Eye"),
    (r"\bears?\b|otitis|otic|aural|head tilt",                  "care", "Eyes & Ears", "Ear"),

    (r"limp|lame|arthr|joint|mobility|cruciate|patella|\bj/d\b|hip dysp|ivdd", "care", "Mobility", "Joint / Arthritis"),

    (r"murmur|cardi|heart|\bchf\b|\bh/d\b",                     "care", "Heart", "Cardiac"),

    (r"sneez|cough|\brti\b|\bflu\b|respir|nasal|kennel cough|dyspn", "care", "Respiratory", "Respiratory"),

    (r"diabet|\bdka\b|insulin",                                 "care", "Endocrine", "Diabetes"),
    (r"thyroid|\bt4\b|cushing|addison",                         "care", "Endocrine", "Thyroid"),

    (r"seizure|\bfit\b|convuls|epilep|neuro",                   "care", "Neurological", "Seizure / Neuro"),
    (r"head tilt|vestibul|nystagmus|ataxia",                    "care", "Neurological", "Vestibular / Head tilt"),

    (r"deworm|heartworm|proheart|\bflea|\btick|nexgard|bravecto|revolution|milbemax|frontline|parasit|\bdrontal", "care", "Preventative", "Parasite control"),
    (r"vacc|\bvac\b|rabies|rabisin|fvrcp|fvcrp|dhlppi|dhppi|nobivac|booster|\bf3\b|\bf4\b|\bc5\b", "care", "Preventative", "Vaccination"),

    (r"weight|obes|satiety|metabolic|\br/d\b|slim",            "care", "Weight & Nutrition", "Weight management"),
    (r"senior|geriatr|mature|aging|ageing",                    "care", "Weight & Nutrition", "Senior / Age"),
    (r"calm|anxiet|stress|behav|zylkene|adaptil|feliway|aggress", "care", "Behaviour", "Calming / Anxiety"),
    (r"hairball",                                               "care", "Cat-specific", "Hairball"),

    # events (pet timeline, not care-tags)
    (r"spay|castrat|neuter|\bovh\b|surgery|\bsx\b|stitches|suture|\bdesex|remove stitch", "event", "Surgery", "Surgery"),
    (r"hospital|inpatient|\badmit|\bicu\b|day care",           "event", "Hospitalisation", "Hospitalisation"),
    (r"health cert|travel cert|export cert|certificate|import permit", "event", "Health certificate", "Health certificate"),
    (r"recheck|body check|\bb/c\b|\br/c\b|\brv\b|\br/v\b|follow up|po-?sx|post op|check ?up|health check|annual|wellness", "event", "Wellness / Recheck", "Wellness / Recheck"),
    (r"\bbloods?\b|\bu/s\b|ultrasound|\blab\b|urinalysis|\bc\+s\b|x-?ray|radiograph|cytology|biopsy|\btest\b|scan|sample", "event", "Diagnostics", "Diagnostics"),

    # engagement / ops (NOT care)
    (r"insurance",                                             "engagement", "Insurance", "Insurance"),
    (r"\brx\b|prescription|dis ?med|disp med|request med|\bpx\b|refill", "engagement", "Rx request", "Rx request"),
    (r"request hx|req hx|rq hx|send hx|fax hx|\bhx\b|history|medical record", "engagement", "History request", "History request"),
    (r"discharge|\bd/c\b",                                     "engagement", "Discharge", "Discharge"),
    (r"question|enquir|\bpc\b|phone|message|\bemail\b|update|estimate|second o|2nd o|quote", "engagement", "Question", "Question"),
]

def classify(text):
    """Return (kind, main, sub) for a free-text reason/name, or (None, None, None) if unmatched."""
    if not text:
        return (None, None, None)
    t = text.lower()
    for pat, kind, main, sub in RULES:
        if re.search(pat, t):
            return (kind, main, sub)
    return (None, None, None)
