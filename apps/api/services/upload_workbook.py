"""Build the SKU upload workbook — two sheets, every closed field a dropdown.

Serves both the CLI (scripts/build_upload_workbook.py) and the inventory
page's Export, which is why it lives here rather than in scripts/.

`--template` writes an empty workbook to add products with. `--export` fills
it with what is already recorded, which is how you MODIFY: editing real rows
beats retyping SKUs, and a round trip that changes nothing must import back
unchanged.

Why a script and not a file someone drew once: the dropdown options are the
values this code and this database actually accept, read at build time. A
hand-made workbook drifts from them within a month, and drift is precisely
what put `#N/A` in as the single most common sell unit in the catalogue —
1,187 products carrying a spreadsheet error string as a fact. Re-run this
after adding a supplier or a category and the sheet catches up.

The sheet says three things in colour, and only three:

    solid header   required — a row cannot be created without it
    amber cell     required, and this row left it empty
    red cell       the value is wrong: off its dropdown, text where a number
                   belongs, or contradicting another cell on the row

A dropdown governs what is TYPED and nothing else, so an export can and does
open with red in it — 1,531 products hold a sell unit no list will accept.
That is the state of the data, shown rather than smuggled through.

Import into Google Sheets and the dropdowns, hidden lists and conditional
formatting all survive. Export is File → Download → CSV, which takes the
ACTIVE SHEET ONLY, so it is two downloads: one per data sheet.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook  # noqa: E402
from openpyxl.formatting.rule import FormulaRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from services.uom_vocabulary import (  # noqa: E402
    CANONICAL_UOMS, WEIGHT_UNITS, canonical_for, weight_in,
)

# ── the vocabularies ────────────────────────────────────────────────────────

STATUSES = ["ACTIVE", "INACTIVE", "DISCONTINUED"]
STORAGE = ["any", "clinic_only"]
SPECIES = ["dog", "cat", "both", "other"]
SEGMENTS = ["vet", "non_vet"]
COST_PER = ["pack", "unit"]
DEAL_KINDS = [
    "flat_unit_cost", "tier", "spend_unit_price",
    "percentage_discount", "qty_discount", "spend_discount",
    "buy_x_get_y",
]
CHANNELS = ["clinic", "shopify", "hktv"]

# ── the columns ─────────────────────────────────────────────────────────────
# (header, list-name or None, width, note-for-the-readme)

PRODUCT_COLUMNS: list[tuple[str, str | None, int, str]] = [
    ("sku",              None,          13, "Blank mints a new SKU. Filled updates that one."),
    ("name",             None,          34, "Required to create. Our name, not the supplier's."),
    ("category",         "categories",  16, "Required to create."),
    ("subcategory",      None,          18, "Functional class — antibiotic, dental, joint."),
    ("species",          "species",     10, ""),
    ("segment",          "segments",    11, ""),
    ("unit",             "uoms",        13, "Required. What ONE of the thing is — every number resolves to it."),
    ("status",           "statuses",    14, ""),
    ("storage",          "storage",     13, ""),
    ("weight",           None,          10, "Exactly as the supplier prints it. Do not convert anything."),
    ("weight_unit",      "weight_units", 12, "Which unit that number is in. Blank means grams."),
    ("notes",            None,          28, ""),
    ("buy.supplier",     "suppliers",   24, "Must already exist. Unknown names are reported, never created."),
    ("buy.brand",        "brands",      22, "Whose brand it is. Supplier information, not part of the product's identity."),
    ("buy.sku",          None,          16, "Their code for it."),
    ("buy.barcode",      None,          16, ""),
    ("buy.pack",         "uoms",        13, "What one purchase unit is called — case, carton, box."),
    ("buy.per_pack",     None,          12, "How many `unit` are inside it."),
    ("buy.cost",         None,          11, "The catalogue price, exactly as printed. Never divided."),
    ("buy.cost_per",     "cost_per",    12, "What that price buys: the pack, or one `unit`. REQUIRED with a cost."),
    ("buy.rrp",          None,          10, "Their recommended retail."),
    ("buy.min_qty",      None,          11, "Smallest order they accept."),
    ("buy.min_qty_uom",  "uoms",        14, "What that count is in. Blank reads as `unit`."),
    ("buy.multiple",     None,          11, "Order in steps of this."),
    ("buy.multiple_uom", "uoms",        14, "What that step is in. Blank reads as `unit`."),
    ("buy.note",         None,          24, ""),
]
for _ch in CHANNELS:
    PRODUCT_COLUMNS += [
        (f"sell.{_ch}.uom",      "uoms", 14, f"What a {_ch} customer is charged in."),
        (f"sell.{_ch}.per_unit", None,   14, "How many of those make one `unit`. Blank reads as 1."),
        (f"sell.{_ch}.multiple", None,   14, "Customer buys in multiples of this."),
        (f"sell.{_ch}.price",    None,   12, "Per one sell uom."),
    ]

DEAL_COLUMNS: list[tuple[str, str | None, int, str]] = [
    ("sku",          None,        13, "Which product. Matched by value — sort the file freely."),
    ("ref.name",     None,        34, "Read-only. Which product that is, so a deal can be read without looking it up."),
    ("supplier",     "suppliers", 24, "Whose deal it is. The pair must already be linked."),
    ("kind",         "kinds",     17, "Which of the four shapes."),
    ("min_qty",      None,        11, "How many to unlock it. flat_unit_cost · tier · buy_x_get_y."),
    ("min_spend",    None,        12, "spend_discount"),
    ("free_qty",     None,        11, "buy_x_get_y"),
    ("discount_pct", None,        13, "spend_discount — a number, 12.5 not 0.125."),
    ("cost",         None,        11, "flat_unit_cost · tier. The deal price, exactly as printed."),
    ("cost_per",     "cost_per",  12, "What that price buys: the pack, or one `unit`. REQUIRED with a cost."),
    ("note",         None,        30, "What the supplier called it."),
]

#: Filling this in is what makes a new row a product (or a deal) at all.
REQUIRED = {
    "products": {"name", "category", "unit"},
    "deals": {"sku", "supplier", "kind"},
}

#: Columns that must hold a number. Flagged rather than validated: a dash is a
#: legitimate value here — it is how the sheet says "clear this" — and a
#: numeric validation would refuse it.
NUMERIC_COLUMNS = {
    "weight", "buy.per_pack", "buy.cost", "buy.rrp", "buy.min_qty", "buy.multiple",
    "min_qty", "min_spend", "free_qty", "discount_pct", "cost",
} | {f"sell.{c}.{f}" for c in CHANNELS for f in ("per_unit", "multiple", "price")}

#: What each deal kind cannot do without. The sheet already flagged fields a
#: kind IGNORES; this is the other half, and without it a buy_x_get_y with no
#: quantities looked perfectly fine.
#:
#: A spend_discount owes its threshold: the kind is named for one, and a
#: percentage with no spend to unlock it is a different deal wearing this kind's
#: label. All 55 stored spend_discounts already carry a min_spend, so this
#: formalises what the data already does — and it leaves the 373 hand-computed
#: percentage deals without a home, which is a modelling question, not a
#: validation one.
#:
#: min_qty on flat_unit_cost is left out pending that same question: requiring it
#: would redden 822 of 935 stored flat terms AND make flat_unit_cost and tier
#: identical in every field, which is the confusion rather than the fix.
#:
#: cost_per is not listed: it has its own rule, which fires only once a cost is
#: present, and two rules for one mistake is one colour too many.
KIND_NEEDS = {
    # what unlocks it          what it gives
    "flat_unit_cost":      ("cost",),
    "tier":                ("min_qty", "cost"),
    "spend_unit_price":    ("min_spend", "cost"),
    "percentage_discount": ("discount_pct",),
    "qty_discount":        ("min_qty", "discount_pct"),
    "spend_discount":      ("min_spend", "discount_pct"),
    "buy_x_get_y":         ("min_qty", "free_qty"),
}

#: Filling these in is what makes a NEW product usable. A blank sku mints one,
#: and a product nobody can be bought from is a name with nothing behind it — no
#: cost, so no margin, so nothing the rest of the system can answer about it.
#:
#: Deliberately absent: barcode, rrp, min_qty and multiple. They are supplier
#: facts worth having and none of them stops a product working.
CREATION_NEEDS = (
    "buy.supplier", "buy.sku", "buy.pack", "buy.per_pack", "buy.cost", "buy.cost_per",
)

#: Identifiers, which must never be arithmetic. The longest barcode we hold is
#: eighteen digits and 974 of them are twelve or more — read as a number, one
#: displays as 8.0E+17, and a CSV download writes back what is displayed. Nine
#: of our own SKUs and forty-nine supplier codes begin with a zero, which the
#: same parse eats without a word. Formatting the column as text is what stops
#: a spreadsheet helping.
TEXT_COLUMNS = {"sku", "buy.sku", "buy.barcode"}

# A column named in a set above but absent from the sheet would silently do
# nothing, and the sets are the only place any of this is written down.
_HEADERS = {h for h, *_ in PRODUCT_COLUMNS} | {h for h, *_ in DEAL_COLUMNS}
_named = set().union(*REQUIRED.values()) | NUMERIC_COLUMNS | TEXT_COLUMNS | set(CREATION_NEEDS)
assert _named <= _HEADERS, f"named columns that do not exist: {sorted(_named - _HEADERS)}"

# A kind cannot both need a field and ignore it. Nothing would tell you which
# rule won; the sheet would simply contradict itself in two colours.
_IGNORED_BY_KIND = {
    "flat_unit_cost":      {"min_spend", "free_qty", "discount_pct"},
    "tier":                {"min_spend", "free_qty", "discount_pct"},
    "spend_unit_price":    {"min_qty", "free_qty", "discount_pct"},
    "percentage_discount": {"min_qty", "min_spend", "free_qty", "cost", "cost_per"},
    "qty_discount":        {"min_spend", "free_qty", "cost", "cost_per"},
    "spend_discount":      {"min_qty", "free_qty", "cost", "cost_per"},
    "buy_x_get_y":         {"min_spend", "discount_pct", "cost", "cost_per"},
}
for _kind, _needed in KIND_NEEDS.items():
    _clash = set(_needed) & _IGNORED_BY_KIND.get(_kind, set())
    assert not _clash, f"{_kind} both needs and ignores {sorted(_clash)}"
assert set(KIND_NEEDS) == set(_IGNORED_BY_KIND) == set(DEAL_KINDS), "a kind is missing a rule set"

# Red means "this value is wrong", and on a numeric column the only way to earn
# it is to not be a number. That reading holds only while the two are disjoint.
_DROPDOWNS = {h for h, li, *_ in PRODUCT_COLUMNS + DEAL_COLUMNS if li}
assert not (NUMERIC_COLUMNS & _DROPDOWNS), \
    f"numeric columns cannot also be dropdowns: {sorted(NUMERIC_COLUMNS & _DROPDOWNS)}"


# ── look ────────────────────────────────────────────────────────────────────

INK = "FF0E141B"

#: Header fills, strong for a required column and a tint of the same hue for an
#: optional one — so "must I fill this in?" is answered before anyone types,
#: without losing which group a column belongs to.
GROUP_FILLS = {
    "key":  ("FF7A4E9C", "FFEFE7F5"),
    "buy":  ("FF8A5F14", "FFF6EBD6"),
    "sell": ("FF1F6B3D", "FFE2F1E8"),
    # Reference, not input. Grey because nothing here is written back.
    "ref":  ("FF5B6472", "FFEDEFF2"),
}

THIN = Side(style="thin", color="FFD3DAE1")


def _cf_fill(rgb: str) -> PatternFill:
    """A conditional-format fill that actually paints.

    A conditional format carries its own style record (a `dxf`), and a fill
    inside one is not read like a cell fill: Excel swaps foreground and
    background there. Built the ordinary way — foreground only, which is how a
    cell fill is written — the amber and red rules below reached the file as
    `<patternFill><fgColor/></patternFill>` and rendered as no highlight at
    all, so nothing marked a missing required field.

    Every workbook Excel and Sheets write themselves sets BOTH colours to the
    same value, in explicit FF-alpha ARGB. Doing the same makes the swap moot.
    """
    return PatternFill("solid", fgColor=rgb, bgColor=rgb)


# One colour, because there is one outcome: this row cannot be uploaded as it
# stands. Missing and contradictory were amber and red until it became clear
# that splitting them just nominates one as the ignorable kind.
RED = _cf_fill("FFF9DEDA")


def _group(header: str) -> str:
    """Which block a column belongs to — the prefix, made visible."""
    if header.startswith("buy."):
        return "buy"
    if header.startswith("sell."):
        return "sell"
    if header.startswith("ref."):
        return "ref"
    return "key"


def _write_header(ws, columns, required: set[str]) -> None:
    for index, (header, _list, width, _note) in enumerate(columns, start=1):
        strong, tint = GROUP_FILLS[_group(header)]
        needed = header in required
        cell = ws.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True, color="FFFFFFFF" if needed else INK, size=10)
        cell.fill = PatternFill("solid", fgColor=strong if needed else tint,
                                bgColor=strong if needed else tint)
        cell.alignment = Alignment(vertical="center")
        cell.border = Border(bottom=THIN)
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "C2"


def _lists_sheet(wb, suppliers: list[str], categories: list[str], brands: list[str]):
    """Every dropdown's options, one column each, on a hidden sheet.

    Hidden rather than absent because Sheets and Excel both need the range to
    resolve, and because someone will eventually want to see what is on offer.
    """
    ws = wb.create_sheet("lists")
    named = {
        "uoms": CANONICAL_UOMS,
        "statuses": STATUSES,
        "storage": STORAGE,
        "species": SPECIES,
        "segments": SEGMENTS,
        "weight_units": WEIGHT_UNITS,
        "cost_per": COST_PER,
        "kinds": DEAL_KINDS,
        "categories": categories,
        "suppliers": suppliers,
        "brands": brands,
    }
    ranges: dict[str, str] = {}
    for index, (name, values) in enumerate(named.items(), start=1):
        letter = get_column_letter(index)
        header = ws.cell(row=1, column=index, value=name)
        header.font = Font(bold=True, size=9)
        for offset, value in enumerate(values, start=2):
            ws.cell(row=offset, column=index, value=value)
        ws.column_dimensions[letter].width = max(12, min(30, max((len(v) for v in values), default=12) + 2))
        ranges[name] = f"lists!${letter}$2:${letter}${len(values) + 1}"
    ws.sheet_state = "hidden"
    return ranges


def _apply_text_format(ws, columns) -> None:
    """Mark the identifier columns as text, so nothing helpfully rounds them.

    Set on the COLUMN, which covers the empty rows below the data — the ones
    someone pastes a barcode into — without bringing them into existence.
    Writing the format cell by cell down to the validated row instead gave the
    sheet four hundred trailing rows that were empty but no longer absent, and
    a CSV download writes those out as four hundred blank lines.

    The cells that already hold a value are formatted too: a column default
    does not reach back over a cell that was written before it.
    """
    for index, (header, _list, _width, _note) in enumerate(columns, start=1):
        if header not in TEXT_COLUMNS:
            continue
        ws.column_dimensions[get_column_letter(index)].number_format = "@"
        for row in range(2, ws.max_row + 1):
            ws.cell(row=row, column=index).number_format = "@"


def _apply_validation(ws, columns, ranges: dict[str, str], last_row: int) -> None:
    """Closed dropdowns: a value off the list is refused, not warned about.

    Warning-style validation is clicked through as a reflex, and the whole
    reason this workbook exists is that free text produced sixty-one spellings
    of about twenty units.
    """
    for index, (header, list_name, _width, _note) in enumerate(columns, start=1):
        if not list_name:
            continue
        letter = get_column_letter(index)
        rule = DataValidation(
            type="list",
            formula1=ranges[list_name],
            allow_blank=True,
            showDropDown=False,   # openpyxl inverts this: False SHOWS the arrow
        )
        rule.error = "Pick a value from the list. Ask for a new one rather than typing it here."
        rule.errorTitle = f"{header} is a fixed list"
        rule.showErrorMessage = True
        ws.add_data_validation(rule)
        rule.add(f"{letter}2:{letter}{last_row}")


def _conditional_rules(ws, columns, ranges: dict[str, str], last_row: int, *, deals: bool) -> None:
    """What a dropdown cannot say: which combinations make sense together."""
    at = {header: get_column_letter(i) for i, (header, *_rest) in enumerate(columns, start=1)}
    row_span = f"$A2:${get_column_letter(len(columns))}2"

    # Red wherever a row has been started and something it cannot do without is
    # still empty. Red rather than amber because the row cannot be used either
    # way: "fill this in" and "this contradicts itself" are both things to fix
    # before uploading, and two colours for one outcome only invites deciding
    # that one of them is the ignorable kind.
    # Sorted so two builds of the same sheet come out identical.
    for header in sorted(REQUIRED[ws.title]):
        column = at[header]
        ws.conditional_formatting.add(
            f"{column}2:{column}{last_row}",
            FormulaRule(
                formula=[f'AND(COUNTA({row_span})>0,{column}2="")'],
                fill=RED, stopIfTrue=False,
            ),
        )

    # A dropdown only governs what someone TYPES. Values already in the sheet
    # arrived from the database, and 1,935 products carry a sell unit that is
    # not on the list — `#N/A` on 1,187 of them. Unflagged they look fine until
    # the cell is touched and refuses to keep its own value, so flag them now.
    # COUNTIF is case-insensitive, which under-flags pure case differences
    # (`PCS` for `Pcs`); the exact-match alternative is a SUMPRODUCT per cell,
    # and this sheet has to open with eleven thousand rows in it.
    for index, (header, list_name, _width, _note) in enumerate(columns, start=1):
        if not list_name:
            continue
        column = get_column_letter(index)
        ws.conditional_formatting.add(
            f"{column}2:{column}{last_row}",
            FormulaRule(
                formula=[f'AND({column}2<>"",COUNTIF({ranges[list_name]},{column}2)=0)'],
                fill=RED, stopIfTrue=False,
            ),
        )

    # A number that arrived as text is invisible: same digits, same column, and
    # every total downstream quietly leaves it out. ISNUMBER is the only thing
    # that can see the difference. The dash is exempt — it means "clear this".
    for index, (header, _list, _width, _note) in enumerate(columns, start=1):
        if header not in NUMERIC_COLUMNS:
            continue
        column = get_column_letter(index)
        ws.conditional_formatting.add(
            f"{column}2:{column}{last_row}",
            FormulaRule(
                formula=[f'AND({column}2<>"",{column}2<>"-",NOT(ISNUMBER({column}2)))'],
                fill=RED, stopIfTrue=False,
            ),
        )

    if not deals:
        # The products sheet's version of "a kind owes its fields": a BLOCK owes
        # its fields once anyone starts filling it in.
        #
        # Supplier terms with no supplier cannot be attached to anything. 84 rows
        # are in that state today, 81 of them stating a pack size for a supplier
        # they never name.
        buy_cols = [get_column_letter(i) for i, (h, *_r) in enumerate(columns, start=1)
                    if h.startswith("buy.")]
        assert buy_cols, "no buy block to check"
        supplier = at["buy.supplier"]
        ws.conditional_formatting.add(
            f"{supplier}2:{supplier}{last_row}",
            FormulaRule(
                formula=[f'AND({supplier}2="",COUNTA(${buy_cols[0]}2:${buy_cols[-1]}2)>0)'],
                fill=RED, stopIfTrue=False,
            ),
        )

        # A blank sku mints a new product, and a new product owes the terms it is
        # bought on. An EXISTING row (sku filled) is exempt: editing a name should
        # not demand that someone re-state the whole supplier block.
        sku = at["sku"]
        for header in CREATION_NEEDS:
            column = at[header]
            ws.conditional_formatting.add(
                f"{column}2:{column}{last_row}",
                FormulaRule(
                    formula=[f'AND(${sku}2="",COUNTA({row_span})>0,{column}2="")'],
                    fill=RED, stopIfTrue=False,
                ),
            )

        # A count of a unit nobody named. Note this is NOT "a price with no uom":
        # blank per_unit reads as 1, which makes a blank sell uom mean "priced per
        # one `unit`" — a documented default, not an omission. Flagging those
        # would paint 6,915 perfectly good rows. Stating how many of a thing make
        # a unit, without saying what the thing is, is the contradiction — and no
        # stored row does it, so this guards entry rather than accusing the data.
        for channel in CHANNELS:
            uom = at[f"sell.{channel}.uom"]
            per_unit, multiple = at[f"sell.{channel}.per_unit"], at[f"sell.{channel}.multiple"]
            ws.conditional_formatting.add(
                f"{uom}2:{uom}{last_row}",
                FormulaRule(
                    formula=[f'AND({uom}2="",OR({per_unit}2<>"",{multiple}2<>""))'],
                    fill=RED, stopIfTrue=False,
                ),
            )

        # A cost with no basis is the case-price bug waiting to happen.
        cost, basis = at["buy.cost"], at["buy.cost_per"]
        ws.conditional_formatting.add(
            f"{basis}2:{basis}{last_row}",
            FormulaRule(formula=[f'AND({cost}2<>"",{basis}2="")'], fill=RED, stopIfTrue=False),
        )
        return

    # Each deal kind uses two fields and ignores the rest; filling one it
    # ignores means the row would half-apply, so flag it before upload does.
    kind = at["kind"]
    ignored = {
        # flat_unit_cost takes a min_qty. It was listed as ignored here, which
        # painted 113 stored terms red for carrying a perfectly good threshold —
        # and the rest of the codebase never agreed with that rule: cost-to-hit
        # charges "min_qty units at the achieved cost" for tier AND flat, the
        # inventory page renders "N+ units" for it, and the SKU page classifies a
        # flat term with min_qty > 1 as a volume deal rather than an everyday
        # price. The two kinds differ in whether there is a ladder above them,
        # not in which fields apply.
        k: tuple(sorted(v)) for k, v in _IGNORED_BY_KIND.items()
    }
    for kind_value, fields in ignored.items():
        for field in fields:
            column = at[field]
            ws.conditional_formatting.add(
                f"{column}2:{column}{last_row}",
                FormulaRule(
                    formula=[f'AND(${kind}2="{kind_value}",{column}2<>"")'],
                    fill=RED, stopIfTrue=False,
                ),
            )

    # The mirror of the ignored map. A buy_x_get_y with no quantities, or a tier
    # with no threshold, described nothing at all and the sheet said nothing
    # about it — only fields that should have been EMPTY were ever flagged.
    for kind_value, fields in KIND_NEEDS.items():
        for field in fields:
            column = at[field]
            ws.conditional_formatting.add(
                f"{column}2:{column}{last_row}",
                FormulaRule(
                    formula=[f'AND(${kind}2="{kind_value}",{column}2="")'],
                    fill=RED, stopIfTrue=False,
                ),
            )

    # A deal price with no basis is the case-price bug, and this sheet is where
    # it actually happened: mbb_terms.unit_cost was declared to be per sellable
    # unit and had no way to say otherwise, so pack prices went in as unit
    # prices and every margin at that tier read too dear.
    #
    # Owed only by the two kinds that name a price. buy_x_get_y and
    # spend_discount have no cost of their own — a cost on one of those is
    # already red for being a field its kind ignores, and demanding a basis for
    # it as well would be a second colour for one mistake.
    cost, basis = at["cost"], at["cost_per"]
    priced = ",".join(f'${kind}2="{k}"' for k in ("flat_unit_cost", "tier"))
    ws.conditional_formatting.add(
        f"{basis}2:{basis}{last_row}",
        FormulaRule(
            formula=[f'AND(OR({priced}),{cost}2<>"",{basis}2="")'],
            fill=RED, stopIfTrue=False,
        ),
    )


def _readme(wb, columns_by_sheet: dict[str, list], supplier_count: int) -> None:
    ws = wb.create_sheet("readme", 0)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 96

    def line(a: str = "", b: str = "", *, bold: bool = False, size: int = 11) -> None:
        row = ws.max_row + 1 if ws.max_row > 1 or ws["A1"].value else 1
        ws.cell(row=row, column=1, value=a).font = Font(bold=True, size=size, color=INK)
        cell = ws.cell(row=row, column=2, value=b)
        cell.font = Font(bold=bold, size=size, color=INK)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    line("Rosetta SKU upload", "Add products, or edit the ones already here, then send each sheet as its own CSV.", bold=True, size=13)
    line()
    line("Two sheets", "products — one row per product, per supplier.    deals — one row per deal.")
    line("", "A product bought from three suppliers is three rows sharing a sku.")
    line()
    line("Exporting", "File → Download → Comma-separated values. It takes the SHEET YOU ARE ON, so do it twice.")
    line()
    line("blank sku", "Mints a new SKU. name, category and unit must be filled.")
    line("blank cell", "Leaves whatever is already recorded alone.")
    line("a dash  -", "Clears the value.")
    line()
    line("Solid header", "Required — a row without it cannot be created.  Pale header: optional.")
    line("Red cell", "Fix before uploading. Either something required is missing — a deal KIND")
    line("", "owes its own fields, so buy_x_get_y needs min_qty and free_qty, and a")
    line("", "started buy block needs a supplier — or the value is wrong: off its")
    line("", "dropdown, text where a number belongs, a cost with no basis, or a field")
    line("", "the kind it names ignores.")
    line("", "A sell price with no sell uom is NOT an error: blank per_unit reads as 1,")
    line("", "so it means priced per one `unit`. Stating a count without naming the")
    line("", "unit it counts is the error, and that turns red.")
    line("", "Exported rows can already be red: a unit recorded before the lists were")
    line("", "fixed is shown as what it is, not quietly kept.")
    line()
    line("buy.cost", "Copy the catalogue price as printed. If it is $130 for a box of 60, write")
    line("", "130 — not 2.17. Nothing in the buy columns is converted, either way.")
    line()
    line("deal cost", "Same rule on the deals sheet, and the same cost_per beside it — asked")
    line("", "for only by the two kinds that name a price, flat_unit_cost and tier.")
    line()
    line("ref. columns", "Read-only, and grey in the header. There to tell you what a row is")
    line("", "about; nothing in them is written back.")
    line()
    line("buy.cost_per", "Say pack or unit every time you write a cost. A case price read as a unit")
    line("", "price is wrong by the pack size, and nothing downstream can tell.")
    line()
    line("Numbers", "Quantities, costs and prices must be numbers. A number typed with a unit")
    line("", 'or a stray space ("12 kg", " 12") is text, sums as zero, and turns red.')
    line()
    line("sku, barcode", "Formatted as text on purpose. Left as numbers a long barcode becomes")
    line("", "8.0E+17 and a leading zero disappears — both permanently, on download.")
    line()
    line("Dropdowns", f"Fixed lists, taken from what the system accepts — including all {supplier_count} suppliers.")
    line("", "A value not on the list is refused. If you need a new one, ask rather than typing it.")
    line()
    line("Rebuilding", "scripts/build_upload_workbook.py --template out.xlsx   (or --export to pull current data)")
    line("", "Re-run it after a supplier or category is added, or the dropdowns will be out of date.")
    line()
    line("Columns", "")
    for sheet_name, columns in columns_by_sheet.items():
        line(f"  {sheet_name}", "")
        for header, list_name, _width, note in columns:
            suffix = "  (dropdown)" if list_name else ""
            line(f"    {header}", f"{note}{suffix}")


# ── data for --export ───────────────────────────────────────────────────────

def _export_rows(limit: int | None, skus: list[str] | None = None):
    """Current products, one row per supplier link, in the sheet's own shape.

    `skus` is how the inventory page's Export stays honest. Its filtering —
    search, supplier, collection, channel, category, quick filters, pinned —
    all happens in the browser, over rows already loaded. The server has never
    heard of a pinned SKU, so reproducing those filters here would drift the
    moment someone adds one, and would quietly export the wrong rows rather
    than failing. The page sends what it is showing instead.
    """
    import database
    import models
    from services import offering_costs

    db = next(database.get_db())
    products = db.query(models.ProductVariant)
    if skus:
        # Chunked: SQLite caps variables per statement, and a whole-catalogue
        # export is eleven thousand of them.
        wanted, chunk = [], 900
        unique = list(dict.fromkeys(skus))
        for start in range(0, len(unique), chunk):
            wanted.extend(
                db.query(models.ProductVariant)
                .filter(models.ProductVariant.sku_code.in_(unique[start:start + chunk]))
                .all()
            )
        order = {sku: i for i, sku in enumerate(unique)}
        products = sorted(wanted, key=lambda p: order.get(p.sku_code, len(order)))
    else:
        products = products.order_by(models.ProductVariant.sku_code)
        if limit:
            products = products.limit(limit)
        products = products.all()

    listings: dict[int, dict[str, object]] = {}
    for item in db.query(models.SellingItem).all():
        listings.setdefault(item.product_variant_id, {})[(item.channel or "").lower()] = item

    rows = []
    for product in products:
        links = list(product.product_suppliers or ())
        # A product with no supplier still deserves a row — its identity is
        # editable even when nobody sells it to us yet.
        for link in links or [None]:
            base = {
                "sku": product.sku_code,
                "name": product.name,
                "category": product.category,
                "subcategory": product.subcategory,
                # Grouped with the buy block because that is where it reads —
                # a brand is whose line the supplier carries — but stored on the
                # PRODUCT, so it belongs to every row of that sku including the
                # 6,366 that have no supplier link at all. Setting it inside the
                # link branch dropped it for exactly those.
                "buy.brand": product.brand,
                "species": product.species,
                "segment": product.segment,
                "unit": product.uom,
                "status": product.status,
                "storage": product.storage_rule,
                # The sheet carries the supplier's own figure and says which
                # unit it is in. Grams is what the database keeps and what
                # delivery cost is computed from, but converting is this code's
                # job, not the job of whoever is reading a catalogue.
                "weight": weight_in(product.weight_g, product.weight_unit),
                "weight_unit": (product.weight_unit
                                or ("g" if product.weight_g is not None else None)),
                "notes": product.notes,
            }
            if link is not None:
                # Everything under buy. is the supplier's own terms, unconverted:
                # Alfamedic prices Keppra at 130 a box of 60, so the sheet says
                # 130 / pack / 60, not the 2.1666… per tablet every margin runs
                # on. Deriving one from the other is this code's job, and a
                # person checking the sheet against the catalogue PDF should
                # find the number they are looking at.
                price = offering_costs.catalogue_price_for_link(link)
                base.update({
                    "buy.supplier": link.supplier.name if link.supplier else None,
                    "buy.sku": link.supplier_sku,
                    "buy.barcode": link.barcode,
                    # The packaging configuration wins where it exists: 29
                    # priced offerings carry a real count there while the legacy
                    # link still says 1, and it is the packaging figure the cost
                    # maths actually divides by.
                    "buy.pack": (canonical_for(price.pack_uom) or price.pack_uom) if price else None,
                    "buy.per_pack": (price.units_per_pack if price and price.units_per_pack is not None
                                     else link.units_per_pack),
                    "buy.cost": price.amount if price else None,
                    "buy.cost_per": price.per if price else None,
                    "buy.rrp": link.rrp,
                    "buy.min_qty": link.minimum_order_qty,
                    # Without these two the sheet says "41" where the database
                    # says "41 Can(s)", and a round trip reads it back as 41
                    # units — the buy.cost_per hazard, one column over.
                    "buy.min_qty_uom": link.minimum_order_uom,
                    "buy.multiple_uom": link.order_increment_uom,
                    "buy.note": link.pricing_note,
                })
            for channel in CHANNELS:
                item = listings.get(product.id, {}).get(channel)
                if item is None:
                    continue
                base.update({
                    f"sell.{channel}.uom": item.sell_uom,
                    f"sell.{channel}.per_unit": float(item.sell_uom_count) if item.sell_uom_count is not None else None,
                    f"sell.{channel}.multiple": item.order_multiple,
                    f"sell.{channel}.price": item.selling_price,
                })
            rows.append(base)

    # Deals follow the same filter: a workbook that described 200 products and
    # 1,401 deals would be describing two different sets.
    exported_ids = {p.id for p in products}
    deals = []
    for term, link in (
        db.query(models.MbbTerm, models.ProductSupplier)
        .join(models.ProductSupplier, models.ProductSupplier.id == models.MbbTerm.product_supplier_id)
        .all()
    ):
        if link.product_id not in exported_ids:
            continue
        product = db.get(models.ProductVariant, link.product_id)
        if product is None:
            continue
        deals.append({
            "sku": product.sku_code,
            "ref.name": product.name,
            "supplier": link.supplier.name if link.supplier else None,
            "kind": term.kind,
            "min_qty": term.min_qty,
            "min_spend": term.min_spend,
            "free_qty": term.free_qty,
            "discount_pct": term.discount_pct,
            # The amount as stated, and what it buys. cost_basis is NULL on every
            # row written before the column existed, and NULL has always meant
            # per unit — so that is what the sheet reports for them, which is
            # also the claim worth showing, since 179 of those rows are wrong by
            # exactly a pack-size factor in one direction or the other.
            "cost": term.unit_cost,
            "cost_per": (term.cost_basis or "unit") if term.unit_cost is not None else None,
            "note": term.note,
        })
    return rows, deals


def _reference_values():
    """Suppliers, categories and brands, as the database has them.

    Brands need folding before they can be offered. products.brand is free text
    and 511 spellings collapse to 463 once case and punctuation are ignored —
    `ZIGNATURE` beside `Zignature`, `almo nature` beside `Almo Nature`. The most
    used spelling of each wins, so the list is what people already write rather
    than a ruling from outside. supplier_brands adds 117 more that no product
    carries yet, which is exactly what a NEW product needs to be able to pick.
    """
    import re
    from collections import defaultdict

    from sqlalchemy import func

    import database
    import models

    db = next(database.get_db())
    suppliers = sorted(
        {(s.name or "").strip() for s in db.query(models.Supplier).all() if (s.name or "").strip()}
    )
    categories = sorted(
        {
            (c or "").strip()
            for (c,) in db.query(models.ProductVariant.category).distinct()
            if (c or "").strip()
        }
    )
    fold = lambda b: re.sub(r"[^a-z0-9]", "", b.lower())   # noqa: E731
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for name, uses in db.query(models.ProductVariant.brand, func.count()).group_by(
            models.ProductVariant.brand).all():
        if (name or "").strip():
            groups[fold(name)].append((uses, name.strip()))
    for (name,) in db.query(models.SupplierBrand.brand_name).distinct():
        if (name or "").strip():
            groups[fold(name)].append((0, name.strip()))
    brands = sorted({max(v)[1] for v in groups.values()}, key=str.lower)
    return suppliers, categories, brands


# ── build ───────────────────────────────────────────────────────────────────

def build(destination, *, with_data: bool, limit: int | None = None,
          skus: list[str] | None = None) -> dict:
    """Write the workbook to a path or a file-like object."""
    suppliers, categories, brands = _reference_values()
    product_rows, deal_rows = _export_rows(limit, skus) if with_data else ([], [])

    wb = Workbook()
    wb.remove(wb.active)

    products = wb.create_sheet("products")
    deals = wb.create_sheet("deals")
    ranges = _lists_sheet(wb, suppliers, categories, brands)

    for ws, columns, rows in ((products, PRODUCT_COLUMNS, product_rows), (deals, DEAL_COLUMNS, deal_rows)):
        _write_header(ws, columns, REQUIRED[ws.title])
        for offset, row in enumerate(rows, start=2):
            for index, (header, *_rest) in enumerate(columns, start=1):
                value = row.get(header)
                if value is not None:
                    ws.cell(row=offset, column=index, value=value)
        # Validation and formatting reach beyond the data so pasted rows are
        # covered too — a sheet that stops validating at the last filled row
        # is a sheet that stops validating exactly when someone adds work.
        last = max(len(rows) + 1, 1) + 400
        _apply_text_format(ws, columns)
        _apply_validation(ws, columns, ranges, last)
        _conditional_rules(ws, columns, ranges, last, deals=ws is deals)

    _readme(wb, {"products": PRODUCT_COLUMNS, "deals": DEAL_COLUMNS}, len(suppliers))
    wb.active = 0
    wb.save(destination)
    return {
        "path": destination if isinstance(destination, str) else None,
        # Two counts, because they differ and the difference has been reported
        # as a bug: the products sheet is one row per product PER SUPPLIER, so a
        # SKU bought from two suppliers is two rows sharing a sku. Reporting the
        # row count as "products" is what made the page promise one number and
        # the download announce another.
        "skus": len({row["sku"] for row in product_rows if row.get("sku")}),
        "rows": len(product_rows),
        "deals": len(deal_rows),
        "suppliers": len(suppliers),
        "categories": len(categories),
        "brands": len(brands),
        "uoms": len(CANONICAL_UOMS),
    }


def build_bytes(*, with_data: bool, skus: list[str] | None = None) -> tuple[bytes, dict]:
    """The workbook as bytes, for handing straight to a download response."""
    buffer = BytesIO()
    result = build(buffer, with_data=with_data, skus=skus)
    return buffer.getvalue(), result
