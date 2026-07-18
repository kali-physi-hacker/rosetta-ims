"""AI product tagging + categorization for catalogue onboarding.

Runs a batched Claude Haiku pass over extracted catalogue items and returns, per
item, free-form (Shopify-style) tags plus a suggested SKU category. Reuses the
extraction service's model + JSON-salvage helper. Degrades to empty suggestions
when ANTHROPIC_API_KEY is unset, so onboarding never breaks.
"""
import os
import json
import re

from services.extraction_service import _loads_json_array

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 8192
BATCH = 30   # items per Claude call — keeps the JSON response within MAX_TOKENS

# Suggested category must be one of the operational IMS item categories.
SKU_CATEGORIES = ["Medicine", "Preventative", "Supplement", "Shampoo", "Food",
                  "Not-For-Sale", "Pet Hygiene", "Cat Litter", "Others"]

# ── Controlled tag vocabulary ───────────────────────────────────────────────────
# The ONLY tags the tagger may assign are the store's real Shopify collection tags
# (imported into seed_collections.json). Using this exact vocabulary is what makes a
# newly-onboarded product land in the correct Shopify smart collections.
def _load_vocabulary() -> list[str]:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_collections.json")
    try:
        cols = json.load(open(path))
    except Exception:
        return []
    vocab = set()
    for c in cols:
        for r in (c.get("ruleSet") or {}).get("rules") or []:
            if r.get("column") == "TAG" and r.get("condition"):
                vocab.add(r["condition"].strip())
    return sorted(vocab)


CONTROLLED_TAGS = _load_vocabulary()
_VOCAB_LOWER = {t.lower(): t for t in CONTROLLED_TAGS}   # lower -> canonical casing


# ── Controlled subcategory vocabulary ───────────────────────────────────────────
# The functional/clinical class of a SKU is constrained to the PetProject "Tagging Logic"
# vocabulary (Pharmacy + Drug Type + Functional Ingredient Focus + Health & Wellness),
# seeded into seed_subcategories.json — no more free-form invented subcategories.
def _load_subcategories() -> list[str]:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_subcategories.json")
    try:
        return json.load(open(path)).get("subcategories", [])
    except Exception:
        return []


CONTROLLED_SUBCATEGORIES = _load_subcategories()
_SUB_LOWER = {s.lower(): s for s in CONTROLLED_SUBCATEGORIES}   # lower -> canonical casing
_SUBS_BLOCK = ", ".join(CONTROLLED_SUBCATEGORIES).replace("%", "%%")


def _canon_subcategory(value) -> str | None:
    """Map a free string to a controlled subcategory (canonical casing), else None.
    Exact (case-insensitive) match first, then a light alias pass for the common
    free-form values the AI/heuristics used to emit."""
    if not value:
        return None
    k = " ".join(str(value).strip().lower().split())
    if k in _SUB_LOWER:
        return _SUB_LOWER[k]
    alias = {
        "renal care": "Kidney Care", "kidney": "Kidney Care", "renal": "Kidney Care",
        "joint supplement": "Joint-Support", "joint": "Hip & Joint", "arthritis": "Anti-Inflammatory",
        "digestive supplement": "Digestive-Support", "digestive": "Digestive Care",
        "probiotic": "Probiotic & Prebiotic Formulas", "calming supplement": "Calming Formula",
        "calming": "Calming & Anxiety", "anxiety": "Calming & Anxiety",
        "dewormer": "Dewormer", "wormer": "Dewormer", "anthelmintic": "Antihelmintic",
        "flea & tick": "Flea & Tick", "flea and tick": "Flea & Tick", "antibiotics": "Antibiotic",
        "skin & coat": "Skin & Coat Support", "skin and coat": "Skin & Coat Support",
        "dental": "Dental Care", "urinary": "Urinary Care", "eye": "Eye Care", "ear": "Ear Care",
        "liver support": "Liver Care", "liver": "Liver Care", "allergy": "Allergies",
        "heartworm preventative": "Antihelmintic", "heartworm": "Antihelmintic", "pain relief": "Analgesic",
    }
    mapped = alias.get(k)
    return _SUB_LOWER.get(mapped.lower()) if mapped else None   # only ever emit a controlled value


def _is_operational(tag: str) -> bool:
    """Merchandising/operational tags that can't be inferred from product text — they're
    set by other rules (autoship, min-qty, brand programmes), not the AI tagger."""
    t = tag.lower()
    if ":" in tag or "autoship" in t:
        return True
    return t in {"auto50", "innovative brand", "canada", "non-canned", "pharmacy",
                 "dispensing fee not required", "prescription not required"}


# Product-intrinsic subset the AI chooses from.
AI_TAGS = [t for t in CONTROLLED_TAGS if not _is_operational(t)]
_TAGS_BLOCK = ", ".join(AI_TAGS).replace("%", "%%")   # escape % for the .% format below

TAGGING_PROMPT = (
"""You are merchandising products for a Hong Kong veterinary / pet-supply company so each lands
in the store's existing Shopify smart collections, which are driven by an EXACT, fixed tag set.

For EACH numbered product below, return a JSON object:
  {"index": <int>, "category": "<one of the categories>", "subcategory": "<class or null>", "tags": ["<tag>", ...]}

Rules:
- "category" MUST be exactly one of: %s — ALWAYS pick the best-fit category, never null.
- "subcategory": the product's FUNCTIONAL / CLINICAL class — what it IS, so two products of the
  same class can substitute for each other. It MUST be chosen EXACTLY (spelling + casing) from the
  CONTROLLED SUBCATEGORIES list below — do NOT invent one. Pick the single best-fit class from the
  product name + brand (a medicine's drug class, a supplement's focus, a clinical care area). Use
  null when none of the controlled subcategories fit (e.g. plain food / litter with no clinical
  function). Do NOT output any value that is not in this list.
  CONTROLLED SUBCATEGORIES: """ + _SUBS_BLOCK + """
- "tags": choose ONLY from the CONTROLLED TAGS list below, copied with EXACT spelling and casing.
  Be THOROUGH — most product labels support 3–8 tags. Assign EVERY tag the product clearly supports:
    • species/life-stage — Cats, Dogs, Kitten, Puppy (tag both if the label says it suits both)
    • form — Tablet, Capsule, Liquid, Powder, Chew, Spot On, Spray, …
    • food/treat type — Dry Food, Wet Food, Canned, Treats, Topper, …
    • health condition / function — Arthritis, Kidney Care, Dewormer, Flea & Tick, Allergies,
      Skin & Coat, Dental, Digestive, Urinary, Joint, Calming, …
    • prescription status — e.g. Prescription Required for Rx-only medicines
  Do NOT output any word that is not in this list, and do NOT force a tag you are unsure of — but do
  not be stingy: a tag clearly implied by the name/brand SHOULD be assigned.
  CONTROLLED TAGS: """ + _TAGS_BLOCK + """
- Return a JSON array of these objects, one per product, aligned by index. No prose, no markdown.

Products:
%s""")


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _norm_tags(tags) -> list[str]:
    """Keep only tags in the controlled Shopify vocabulary, mapped to canonical casing;
    dedupe. Anything off-vocabulary is dropped so tags always match collection rules."""
    out, seen = [], set()
    for t in (tags or []):
        canon = _VOCAB_LOWER.get(" ".join(str(t).strip().lower().split()))
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def detect_species(description: str, brand: str = None) -> str | None:
    """Determine the target species for a product using Claude WITH web search —
    so it can look the brand/product up instead of guessing from the price-list line.
    Returns 'dog' | 'cat' | 'both' | 'other' | None. Degrades to None on any failure."""
    if not ANTHROPIC_API_KEY or not (description or "").strip():
        return None
    q = (f"{brand} " if brand else "") + (description or "")
    try:
        msg = _client().messages.create(
            model=MODEL, max_tokens=900,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content":
                f'A Hong Kong vet / pet shop sells this product: "{q.strip()}". '
                "Which animal is it intended for? If you are not certain, SEARCH the web to identify the "
                "brand/product. Then reply with ONLY one word on the final line: dog, cat, both, or other. "
                "Use 'both' if it suits dogs and cats; 'other' for small animals/birds/etc. If you truly "
                "cannot determine it, reply: unknown."}],
        )
    except Exception:
        return None
    text = " ".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip().lower()
    if not text:
        return None
    if "both" in text:
        return "both"
    has_dog, has_cat = "dog" in text, "cat" in text
    if has_dog and has_cat:
        return "both"
    if has_dog:
        return "dog"
    if has_cat:
        return "cat"
    if "other" in text:
        return "other"
    return None


# ── Rule-based fallback ─────────────────────────────────────────────────────────
# So a scan ALWAYS yields tags + category + subcategory even with no API key or a failed
# AI batch. Only controlled-vocab tags are emitted (canonical casing via _VOCAB_LOWER).
def _vocab(*names: str) -> list[str]:
    out = []
    for n in names:
        c = _VOCAB_LOWER.get(n.lower())
        if c and c not in out:
            out.append(c)
    return out


_HEUR_TAG_RULES = [
    (r"\b(dog|canine|puppy|puppies|k9)\b", ["Dogs"]),
    (r"\b(puppy|puppies)\b",              ["Puppy", "Dogs"]),
    (r"\b(cat|feline|kitten|kittens)\b",  ["Cats"]),
    (r"\b(kitten|kittens)\b",             ["Kitten", "Cats"]),
    (r"\b(tab|tabs|tablet|tablets)\b",    ["Tablet"]),
    (r"\b(cap|caps|capsule|capsules)\b",  ["Capsule"]),
    (r"\b(susp|suspension|syrup|solution|liquid|drops|oral sol|elixir)\b", ["Liquid"]),
    (r"\b(powder|sachet|sachets)\b",      ["Powder"]),
    (r"\b(chew|chews|chewable)\b",        ["Chew"]),
    (r"\b(spot[- ]?on|pipette|pipettes)\b", ["Spot On"]),
    (r"\b(spray|sprays)\b",               ["Spray"]),
    (r"\b(dry food|kibble|\bdry\b)\b",    ["Dry Food"]),
    (r"\b(wet food|canned|\bcan\b|pouch|gravy|jelly|loaf)\b", ["Wet Food", "Canned"]),
    (r"\b(treat|treats|snack|snacks|jerky|topper)\b", ["Treats"]),
    (r"\b(dewormer|wormer|anthelmintic|deworm|praziquantel|fenbendazole)\b", ["Dewormer"]),
    (r"\b(flea|fleas|tick|ticks)\b",      ["Flea & Tick"]),
    (r"\b(joint|arthritis|glucosamine|chondroitin|mobility)\b", ["Joint", "Arthritis"]),
    (r"\b(kidney|renal)\b",               ["Kidney Care"]),
    (r"\b(dental|teeth|tartar|oral care)\b", ["Dental"]),
    (r"\b(skin|coat|derma|fur)\b",        ["Skin & Coat"]),
    (r"\b(urinary|urine|bladder|cystitis)\b", ["Urinary"]),
    (r"\b(calm|calming|anxiety|stress)\b", ["Calming"]),
    (r"\b(probiotic|digest|gut|gastro|gastrointestinal)\b", ["Digestive"]),
    (r"\b(allerg)\w*", ["Allergies"]),
]

_HEUR_CAT_RULES = [
    (r"\b(litter)\b", "Cat Litter"),
    (r"\b(shampoo|conditioner|wash|wipe|wipes|deodor|cologne|perfume|grooming|brush|nail)\b", "Pet Hygiene"),
    (r"\b(flea|tick|wormer|dewormer|heartworm|preventative|spot[- ]?on|vaccine|vaccination)\b", "Preventative"),
    (r"\b(supplement|probiotic|vitamin|omega|glucosamine|joint|calming|nutraceutical)\b", "Supplement"),
    (r"\b(food|kibble|\bcan\b|canned|pouch|diet|treat|snack|topper|jerky|formula)\b", "Food"),
    (r"\b(tab|tablet|capsule|injection|antibiotic|\bmg\b|\bml\b|rx|prescription|cream|ointment|drops)\b", "Medicine"),
]

# Subcategory heuristics map to CONTROLLED subcategory values only (validated via
# _canon_subcategory). Non-clinical products (plain food / litter / shampoo) have no
# clinical class and get a null subcategory by design.
_HEUR_SUB_RULES = [
    (r"\b(amoxi|clavul|cephalexin|cefovecin|metronidazole|doxycyclin|enrofloxacin|antibiotic)\b", "Antibiotic"),
    (r"\b(antifungal|ketoconazole|itraconazole|miconazole|clotrimazole)\b", "Antifungal"),
    (r"\b(dewormer|wormer|anthelmintic|praziquantel|fenbendazole|milbemycin)\b", "Dewormer"),
    (r"\b(heartworm|heartgard|ivermectin|selamectin|moxidectin)\b", "Antihelmintic"),
    (r"\b(flea|tick)\b", "Flea & Tick"),
    (r"\b(joint|glucosamine|chondroitin|arthritis|mobility)\b", "Joint-Support"),
    (r"\b(kidney|renal)\b", "Kidney Care"),
    (r"\b(dental|teeth|tartar|oral care)\b", "Dental Care"),
    (r"\b(urinary|urine|bladder|cystitis)\b", "Urinary Care"),
    (r"\b(eye|ophthalmic|ocular)\b", "Eye Care"),
    (r"\b(ear|otic|aural)\b", "Ear Care"),
    (r"\b(skin|coat|derma|fur)\b", "Skin & Coat Support"),
    (r"\b(probiotic|digest|gut|gastro)\b", "Digestive-Support"),
    (r"\b(calm|anxiety|stress|sedative)\b", "Calming Formula"),
    (r"\b(allerg|antihistamine)\b", "Allergies"),
    (r"\b(pain|analges|nsaid|anti.?inflamm)\b", "Anti-Inflammatory"),
    (r"\b(vitamin|multivitamin)\b", "Vitamin"),
    (r"\b(immune|immunity)\b", "Immune-Support"),
    (r"\b(omega|fish oil|epa|dha)\b", "Omega-Rich"),
]


def _heuristic(item: dict) -> dict:
    text = " ".join(str(item.get(k) or "") for k in ("description", "brand", "supplier")).lower()
    tags: list[str] = []
    for pat, names in _HEUR_TAG_RULES:
        if re.search(pat, text):
            for t in _vocab(*names):
                if t not in tags:
                    tags.append(t)
    category    = next((c for pat, c in _HEUR_CAT_RULES if re.search(pat, text)), None)
    subcategory = next((s for pat, s in _HEUR_SUB_RULES if re.search(pat, text)), None)
    return {"tags": tags, "category": category, "subcategory": _canon_subcategory(subcategory)}


def _merge_heuristic(ai: dict, heur: dict) -> dict:
    """Gap-fill an AI result with the rule-based one — only where the AI left a blank, so
    the AI's choices always win and the fallback just guarantees non-empty output."""
    tags = list(ai.get("tags") or [])
    if not tags:                                   # AI gave none → take all heuristic tags
        tags = list(heur.get("tags") or [])
    else:                                          # AI gave some → add any extra species/form
        for t in (heur.get("tags") or []):
            if t not in tags:
                tags.append(t)
    return {
        "tags": tags,
        "category": ai.get("category") or heur.get("category"),
        "subcategory": ai.get("subcategory") or heur.get("subcategory"),
    }


def suggest_tags(items: list[dict]) -> list[dict]:
    """items: [{description, brand, category, supplier}] -> aligned list of
    {"tags": [...], "category": <str|None>, "subcategory": <str|None>}. Same length/order.
    Never raises. A rule-based fallback always fills gaps (tags / category / subcategory)
    so onboarding gets useful tagging even with no API key or a failed AI batch."""
    results: list[dict] = [{"tags": [], "category": None, "subcategory": None} for _ in items]
    if not items:
        return results
    if not ANTHROPIC_API_KEY:
        return [_merge_heuristic(r, _heuristic(it)) for r, it in zip(results, items)]

    cats = ", ".join(SKU_CATEGORIES)
    client = _client()

    for base in range(0, len(items), BATCH):
        batch = items[base:base + BATCH]
        lines = []
        for i, it in enumerate(batch):
            bits = [f'#{i} {it.get("description") or ""}']
            if it.get("brand"):    bits.append(f'brand: {it["brand"]}')
            if it.get("supplier"): bits.append(f'supplier: {it["supplier"]}')
            lines.append(" | ".join(bits))
        prompt = TAGGING_PROMPT % (cats, "\n".join(lines))
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            parsed = _loads_json_array(msg.content[0].text)
        except Exception:
            continue   # leave this batch's items with empty suggestions

        for obj in parsed:
            try:
                idx = int(obj.get("index"))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                cat = obj.get("category")
                results[base + idx] = {
                    "tags": _norm_tags(obj.get("tags")),
                    "category": cat if cat in SKU_CATEGORIES else None,
                    "subcategory": _canon_subcategory(obj.get("subcategory")),
                }

    # Always guarantee non-empty tagging — gap-fill any item the AI left blank/partial.
    return [_merge_heuristic(r, _heuristic(it)) for r, it in zip(results, items)]
