"""
Data-driven definitions for the EVCO mock quote PDFs.

This module builds plain Python dict/list structures describing 5 fictional
EVCO Price Quotation documents (header metadata + Part Pricing table rows +
resin/material rows + annotation instructions). generate_mock_quotes.py
renders these structures into PDFs with PyMuPDF; the same structures are
also dumped to data/mock_quote_data.json and used to build
mock_quote_manifest.json / mock_quote_row_manifest.json / coverage docs, so
the PDFs and the metadata can never drift apart.

Customer company names and customer numbers ARE real, taken from a live
export of IQMS's /CRM/CustomerCentral/CustomersLite endpoint (see
data/iqms_customers_lite.json), supplied directly for this purpose - they are
the only way to exercise CustomerRepository.find_candidates()'s real matching
logic (exact number, starts-with, >=80% name similarity) without a live IQMS
connection. Every other detail - contact person, email, street address, part
numbers, prices, BOMs - is fictional.
"""

import random

# ---------------------------------------------------------------------------
# Fictional reference pools (no real EVCO staff, customers, or part numbers)
# ---------------------------------------------------------------------------

DESCRIPTION_NOUNS = [
    "BRACKET", "COVER", "HOUSING", "BOX ASSY", "CLAMP", "LEVER", "SUPPORT",
    "PANEL", "DUCT", "TRAY", "KNOB", "BEZEL", "SKIRT", "SKIN", "FENDER",
    "LATCH", "HANDLE", "EMBLEM", "SHROUD", "DIVIDER", "CONSOLE", "CHART",
    "SEAL", "CAP ASSY", "GRILLE", "BAFFLE", "RETAINER", "SPACER", "GUARD",
]
DESCRIPTION_MODIFIERS = [
    "LH", "RH", "UPPER", "LOWER", "FRONT", "REAR", "INNER", "OUTER",
    "ASSY", "BLACK", "GREY", "MAIN", "AUX", "VENT", "ACCESS",
]
MATERIALS = [
    "PC/ABS PC-540A Black", "PC/ABS PC-540A Grey", "PC/ABS C6600 7T1D355 Black",
    "PC/ABS C6600 7G7A3228 Grey", "PC Wonderloy PC-540A Red", "Nylon 6/6 GF30 Black",
    "ABS 757 Natural", "PP Copolymer Black", "TPE Shore 70A Black",
]
BOM_RESIN_SUFFIXES = ["CHIMEI", "SABIC", "RAVAGO", "LOTTE"]
BOM_FAMILY_SUFFIXES = ["PROD", "SER", "WMF", "RET"]
LEAD_TIMES = ["9 Weeks", "12 Weeks", "13 Weeks", "16 Weeks", "20 Weeks"]

FICTIONAL_CONTACTS = {
    "sales": ["J. Sterling", "M. Okafor", "R. Delacroix", "P. Nakamura", "T. Whitfield"],
    "cs": ["R. Alvarez", "S. Boyko", "L. Fennimore", "K. Osei", "D. Marsh"],
    "engineer": ["T. Wynn / A. Castellano", "N. Farrow", "B. Okonkwo", "C. Vasquez", "E. Holt"],
    "signer": "Morgan Vale",
}

FICTIONAL_CUSTOMERS = {
    # "customer_number"/"name" are real (IQMS CustomersLite export); address/
    # attention/email below are fictional placeholders - none of that contact
    # detail was in the source export.
    "acme": {
        "name": "CLACK CORPORATION",
        "customer_number": "10329",
        "address": ["4410 Corridor Parkway, Suite 200", "Madison, WI 53704"],
        "attention": "Attention: Renee Vasquez, r.vasquez@clackcorp-mail.example",
    },
    "northwind_co": {
        # Long/suffixed form -> CustomerRepository.find_candidates() resolves
        # this to exactly one candidate (UNIQUE) - verified directly against
        # the real code, see validate_mock_quotes.py.
        "name": "ALIGN TECHNOLOGY INC",
        "customer_number": "11965",
        "address": ["1188 Harbor Freight Rd.", "Toledo, OH 43604"],
        "attention": "Attention: Devon Okafor, d.okafor@aligntech-mail.example",
    },
    "northwind_company": {
        # Short form of the SAME real company under a second, differently-
        # numbered IQMS record ("Align Technology" / DH102). Searching by
        # this name alone matches BOTH this record and "ALIGN TECHNOLOGY INC"
        # (AMBIGUOUS, EX-006) - verified directly against the real
        # CustomerRepository code; matching by customer number instead
        # resolves uniquely (VR-006). See validate_mock_quotes.py.
        "name": "Align Technology",
        "customer_number": "DH102",
        "address": ["1188 Harbor Freight Road", "Toledo, OH 43604"],
        "attention": "Attention: Devon Okafor, d.okafor@aligntech-mail.example",
    },
    "ironclad": {
        "name": "GREENHECK FAN CORP",
        "customer_number": "11853",
        "address": ["77 Foundry Row", "Canton, OH 44702"],
        "attention": "Attention: Priya Ramanathan, p.ramanathan@greenheck-mail.example",
    },
    "falcon": {
        "name": "HARLEY-DAVIDSON MOTOR CO",
        "customer_number": "11967",
        "address": ["902 Rotor Ave.", "Wichita, KS 67202"],
        "attention": "Attention: Owen Blackwell, o.blackwell@harley-mail.example",
    },
    "skyline": {
        "name": "ACUITY BRANDS LIGHTING INC",
        "customer_number": "10130",
        "address": ["2925 Lakeview Terrace", "Conyers, GA 30012"],
        "attention": "Attention: Harper Lindqvist, h.lindqvist@acuitybrands-mail.example",
    },
}

DUMMY_DISCLAIMER = (
    "ANY ORDER SUBMITTED UNDER THIS QUOTATION WILL NOT RESULT IN A CONTRACT UNTIL IT IS ACCEPTED AND "
    "ACKNOWLEDGED BY EVCO PLASTICS. SELLER'S ACCEPTANCE WILL BE CONDITIONAL ON BUYER'S ASSENT TO THESE "
    "TERMS AND CONDITIONS INCLUDING DISCLAIMERS OF WARRANTIES AND LIMITATIONS OF REMEDIES. UNLESS "
    "OTHERWISE SPECIFIED; TERMS, CONDITIONS, AND TOLERANCES FOLLOW SPI/SPE STANDARDS."
)

EVCO_ADDRESS_LINE = (
    # Plain ASCII separators (not the printed template's Wingdings diamond
    # glyph, which isn't in Arial's cmap and rendered as empty boxes with the
    # embedded Arial TTF).
    "121 EVCO CIRCLE, BOX 497, DEFOREST, WI. 53532 USA | (608) 846-6000 "
    "| PURCHASEORDERS@EVCOPLASTICS.COM"
)


def _price(rng, low, high):
    return f"${rng.uniform(low, high):.2f}"


def _tiered_prices(rng, base, count, drop_pct=0.14):
    """count decreasing prices starting near `base`, each tier ~drop_pct cheaper."""
    prices = []
    p = base
    for _ in range(count):
        prices.append(round(p, 2))
        p = p * (1 - drop_pct)
    return prices


def _description(rng):
    return f"{rng.choice(DESCRIPTION_MODIFIERS)} {rng.choice(DESCRIPTION_NOUNS)}"


def _long_description(rng):
    return (
        f"{rng.choice(DESCRIPTION_MODIFIERS)} {rng.choice(DESCRIPTION_NOUNS)} "
        f"W/ {rng.choice(DESCRIPTION_MODIFIERS)} {rng.choice(DESCRIPTION_NOUNS)} "
        f"AND INTEGRATED {rng.choice(DESCRIPTION_NOUNS)} MOUNT"
    )


def make_row(row_no, mold, evco_pn, bom, customer_pn, description, box_qty, tiers,
             scenarios, notes, material=None, lead_time=None, family_code=None,
             decoy_qty=None, annotation=None):
    return {
        "row": row_no,
        "mold": mold,
        "evco_pn": evco_pn,
        "bom": bom,
        "customer_pn": customer_pn,
        "description": description,
        "material": material,
        "box_qty": box_qty,
        "decoy_qty": decoy_qty,
        "lead_time": lead_time,
        "family_code": family_code,
        "tiers": tiers,  # list of {"moq": str, "price": str}
        "scenarios": scenarios,
        "annotation": annotation,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# PDF 1 - "CLACK CORPORATION" - tiered/resin template (65150-003 style)
# ---------------------------------------------------------------------------

def build_pdf1():
    rng = random.Random(74021)
    rows = []
    row_no = 0
    mold_base = 6600
    pn_base = 9480000

    def next_ids():
        nonlocal mold_base, pn_base
        mold_base += rng.choice([1, 1, 2, 3])
        pn_base += rng.randint(3, 40)
        return mold_base, pn_base

    # 18 "BOM resin-variant" part groups (2 BOM variants each = 36 rows).
    # Every row repeats its own Mold/EVCO PN/Customer PN explicitly (no blank
    # "carry-forward" cells) - the reference templates' visually-merged blank
    # cells rely on the extractor's fill-down logic, which is exactly the
    # kind of ambiguity that can misfire into an EX-002/EX-003 "missing
    # required field" exception if fill-down doesn't happen; this dataset
    # avoids that risk entirely by never leaving those cells blank.
    for i in range(18):
        mold, pn = next_ids()
        cust_pn = f"{10000 + rng.randint(0, 89999)}"
        desc = _description(rng)
        box_qty = rng.choice([14, 20, 20, 40, 60])
        resin_a, resin_b = rng.sample(BOM_RESIN_SUFFIXES, 2)
        base_price = rng.uniform(8.0, 140.0)
        tier_count = rng.choice([1, 2, 3])
        tiers_a = [{"moq": str(int(box_qty * (m + 1) * rng.choice([1, 3, 5]))),
                    "price": f"${p:.2f}"} for m, p in enumerate(_tiered_prices(rng, base_price, tier_count))]
        tiers_b = [{"moq": t["moq"], "price": f"${(float(t['price'][1:]) * rng.uniform(1.15, 1.4)):.2f}"} for t in tiers_a]

        scenarios_a = ["VALID_BASELINE", "BOM_RESIN_VARIANT"]
        if tier_count > 1:
            scenarios_a.append("MULTI_TIER_PRICING")
        else:
            scenarios_a.append("SINGLE_TIER_PRICING")
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}-{resin_a}", cust_pn, f"{desc}-{resin_a.title()}",
            str(box_qty), tiers_a, scenarios_a,
            "Baseline resin-variant row (first of pair) with its own quantity-break tier(s).",
            material=rng.choice(MATERIALS), lead_time=rng.choice(LEAD_TIMES),
        ))
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}-{resin_b}", cust_pn, f"{desc}-{resin_b.title()}",
            str(box_qty), tiers_b, ["VALID_BASELINE", "BOM_RESIN_VARIANT",
                                     "MULTI_TIER_PRICING" if tier_count > 1 else "SINGLE_TIER_PRICING"],
            "Second resin-variant row of the pair: Mold/EVCO PN/Customer PN/Parts-Box are repeated "
            "explicitly (not left blank) to avoid depending on extractor fill-down for a required field.",
            material=rng.choice(MATERIALS), lead_time=rng.choice(LEAD_TIMES),
        ))

    # Remaining single-BOM rows with assorted boundary/edge scenarios.
    special_specs = [
        ("MOQ_LOW_BOUNDARY", "MOQ set to exactly one box quantity - smallest realistic release."),
        ("AKA_QTY_BREAK_OF_ONE", "MOQ of literally 1 unit - BR-021 'AKA quantity break of 1' flavor."),
        ("BOX_QTY_MOQ_NOT_DIVISIBLE", "MOQ is not an even multiple of Parts/Box, violating the 'MOQ's are "
         "listed in even box quantities' convention used on the reference quotes."),
        ("MOQ_HIGH_VOLUME", "Large multi-thousand-unit MOQ / high-volume tier."),
        ("BLANK_CUSTOMER_PN", "Customer PN intentionally left blank on this single row only (not a majority "
         "of rows), to confirm an isolated blank does not trigger the required-column exception."),
        ("BLANK_DESCRIPTION", "Part Description intentionally left blank on this single row only."),
        ("BLANK_MOQ_SINGLE_ROW", "MOQ intentionally left blank on this single row only (price still present)."),
        ("BLANK_PRICE_SINGLE_ROW", "Price intentionally left blank ('TBD') on this single row only (MOQ still present)."),
        ("LONG_DESCRIPTION_WRAP", "Long, multi-clause description that wraps across two printed lines."),
        ("LEAD_TIME_EXTENDED", "Extended lead time (20 weeks) noted, mirroring the '## wks' style callouts."),
        ("COMBINED_MULTI_RULE", "Combines multi-tier pricing + high lead time + large customer-PN format in one row."),
        ("COMBINED_MULTI_RULE", "Combines single-tier pricing + boundary-low MOQ + long description in one row."),
    ]
    for tag, note in special_specs:
        mold, pn = next_ids()
        cust_pn = f"{10000 + rng.randint(0, 89999)}"
        box_qty = rng.choice([10, 12, 20, 25, 50])
        desc = _long_description(rng) if "LONG_DESCRIPTION" in tag or "COMBINED" in tag else _description(rng)
        if tag == "MOQ_LOW_BOUNDARY":
            moq, price = str(box_qty), _price(rng, 5, 20)
        elif tag == "AKA_QTY_BREAK_OF_ONE":
            moq, price = "1", _price(rng, 40, 90)
        elif tag == "BOX_QTY_MOQ_NOT_DIVISIBLE":
            moq, price = str(box_qty * 7 + 3), _price(rng, 2, 10)
        elif tag == "MOQ_HIGH_VOLUME":
            moq, price = str(box_qty * rng.randint(80, 200)), _price(rng, 0.5, 3)
        else:
            moq, price = str(box_qty * rng.randint(2, 12)), _price(rng, 3, 60)

        row_no += 1
        customer_pn_val = "" if tag == "BLANK_CUSTOMER_PN" else cust_pn
        description_val = "" if tag == "BLANK_DESCRIPTION" else desc
        moq_val = "" if tag == "BLANK_MOQ_SINGLE_ROW" else moq
        price_val = "" if tag == "BLANK_PRICE_SINGLE_ROW" else price
        tiers = [{"moq": moq_val, "price": price_val}]
        if tag == "COMBINED_MULTI_RULE" and "single-tier" not in note:
            extra = _tiered_prices(rng, float(price[1:]) if price_val else 20.0, 2)
            tiers = [{"moq": moq_val, "price": price_val}] + [
                {"moq": str(int(moq) * (i + 2)) if moq_val else "", "price": f"${p:.2f}"} for i, p in enumerate(extra)
            ]
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}", customer_pn_val, description_val, str(box_qty),
            tiers, [tag], note, material=rng.choice(MATERIALS),
            lead_time="20 Weeks" if "EXTENDED" in tag else rng.choice(LEAD_TIMES),
        ))

    # Two annotated rows (drawn as vector overlays at render time).
    mold, pn = next_ids()
    row_no += 1
    rows.append(make_row(
        row_no, str(mold), str(pn), f"{mold}/{pn}", f"{10000 + rng.randint(0, 89999)}", _description(rng),
        "24", [{"moq": "240", "price": _price(rng, 5, 20)}],
        ["STRUCK_ROW_RED_ANNOTATION"],
        "Entire row struck with a red line in the source PDF; must be excluded entirely from extraction "
        "(get_page_annotations -> strike_rows).",
        material=rng.choice(MATERIALS), lead_time=rng.choice(LEAD_TIMES),
        annotation={"type": "strike_red"},
    ))
    mold, pn = next_ids()
    row_no += 1
    rows.append(make_row(
        row_no, str(mold), str(pn), f"{mold}/{pn}", f"{10000 + rng.randint(0, 89999)}", _description(rng),
        "30", [{"moq": "300", "price": _price(rng, 5, 20)}],
        ["STRUCK_ROW_PINK_ANNOTATION"],
        "Entire row struck with a pink line in the source PDF; must be excluded entirely from extraction.",
        material=rng.choice(MATERIALS), lead_time=rng.choice(LEAD_TIMES),
        annotation={"type": "strike_pink"},
    ))
    mold, pn = next_ids()
    row_no += 1
    real_price = _price(rng, 10, 30)
    rows.append(make_row(
        row_no, str(mold), str(pn), f"{mold}/{pn}", f"{10000 + rng.randint(0, 89999)}", _description(rng),
        "40", [{"moq": "400", "price": real_price}],
        ["CELL_OVERRIDE_RED_BOX"],
        f"Printed price {real_price} is visually overridden by a small red-outlined box in the source PDF; "
        f"the override value must be used instead of the original printed value.",
        material=rng.choice(MATERIALS), lead_time=rng.choice(LEAD_TIMES),
        annotation={"type": "override_box", "override_price": _price(rng, 40, 90)},
    ))

    # Pad up to 60 rows with plain valid baseline rows for volume/realism.
    while row_no < 60:
        mold, pn = next_ids()
        box_qty = rng.choice([10, 20, 30, 40, 50])
        tier_count = rng.choice([1, 1, 2])
        prices = _tiered_prices(rng, rng.uniform(3, 80), tier_count)
        tiers = [{"moq": str(box_qty * (i + 1) * rng.choice([2, 4, 6])), "price": f"${p:.2f}"} for i, p in enumerate(prices)]
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}", f"{10000 + rng.randint(0, 89999)}", _description(rng),
            str(box_qty), tiers,
            ["VALID_BASELINE", "MULTI_TIER_PRICING" if tier_count > 1 else "SINGLE_TIER_PRICING"],
            "Plain valid baseline row added for table volume/realism.",
            material=rng.choice(MATERIALS), lead_time=rng.choice(LEAD_TIMES),
        ))

    resin_rows = []
    for i, mat in enumerate(MATERIALS[:7]):
        pn = 1388000 + i * 7
        old_p = round(rng.uniform(2.5, 6.0), 2)
        new_p = old_p if i % 3 else round(old_p * rng.uniform(0.95, 1.08), 2)
        resin_rows.append({
            "material_pn": str(pn),
            "description": mat,
            "moq": f"{rng.choice([1500, 3000, 4500, 6000])} lbs",
            "lead_time": rng.choice(["9 weeks", "16 weeks"]),
            "old_price": f"${old_p}/lb.",
            "new_price": f"${new_p}/lb.",
            "scenarios": ["RESIN_MATERIAL_ROW", "PRICE_UNCHANGED_ROW" if old_p == new_p else "PRICE_INCREASE_HIGHLIGHT"],
            "notes": "Resin/material reference row - per the extraction prompt's own instructions this table is "
                     "explicitly excluded from parts[] extraction; included here for visual/structural fidelity only.",
        })

    return {
        "file": "EVCO_QUOTE_74021_DUMMY.pdf",
        "quote_number": "74021 - 001",
        "opportunity_id": "74021",
        "type": "Active",
        "issue_date": "07-01-26",
        "price_effective_date": "July 20, 2026",
        "customer": FICTIONAL_CUSTOMERS["acme"],
        "template_style": "tiered_resin",
        "header_repeats_each_page": True,
        "effective_date_override": None,
        "garbled_two_line_header": False,
        "missing_required_column": None,
        "supersedes_prior_quote": True,
        "customer_similarity_test": False,
        "sales": FICTIONAL_CONTACTS["sales"][0],
        "cs": FICTIONAL_CONTACTS["cs"][0],
        "engineer": FICTIONAL_CONTACTS["engineer"][0],
        "plant": "DeForest/OSH",
        "cavities": "1",
        "mold_numbers": "See Table",
        "credit_terms": "1% 10 Days Net 30",
        "shipping": "FOB EVCO Dock DeForest/OSH",
        "lead_time_note": "Mfg. lead time as noted per line; add 4 weeks for non-domestic destinations.",
        "quote_comments": [
            "This quotation supersedes EVCO quote 74021-000A, dated 5/15/2026.",
            "In-Process Regrind Will Be Utilized Where Possible.",
            "Customer is responsible for excess materials not used within 90 days.",
        ],
        "rows": rows,
        "resin_rows": resin_rows,
    }


# ---------------------------------------------------------------------------
# PDF 2 - "ALIGN TECHNOLOGY INC" - wide master-list template (58312-035 style)
# ---------------------------------------------------------------------------

def build_pdf2():
    rng = random.Random(74022)
    rows = []
    row_no = 0
    mold_base = 2100
    pn_base = 9440000

    def next_ids(step=None):
        nonlocal mold_base, pn_base
        mold_base += step or rng.choice([1, 5, 10, 20])
        pn_base += rng.randint(5, 60)
        return mold_base, pn_base

    # Family-suffix groups (PROD/SER/WMF/RET variants of one base BOM) - 10 groups x ~3 rows.
    for _ in range(10):
        mold, pn = next_ids()
        cust_pn = f"{rng.randint(40000000, 49999999)}A{rng.randint(1,9)}"
        desc = _description(rng)
        box_qty_base = rng.choice([20, 30, 40, 60, 100])
        variants = rng.sample(BOM_FAMILY_SUFFIXES, rng.choice([2, 3]))
        for v_idx, suffix in enumerate(variants):
            row_no += 1
            box_qty = box_qty_base if v_idx == 0 else max(5, box_qty_base - v_idx * 10)
            moq = box_qty * rng.choice([1, 3, 6])
            price = _price(rng, 0.7, 40)
            tags = ["VALID_BASELINE", "BOM_SUFFIX_VARIANT"]
            rows.append(make_row(
                row_no, str(mold), str(pn),
                f"{mold}/{pn}-{suffix}", cust_pn, desc, str(box_qty),
                [{"moq": str(moq), "price": price}], tags,
                f"Family-tool BOM suffix variant ({suffix}) of one base part/mold; Mold/EVCO PN are "
                f"repeated explicitly on every row of the group (not left blank), to avoid depending on "
                f"extractor fill-down for a required field.",
                family_code=rng.choice(["F", "I", "S", "P", None, None]),
            ))

    # BOM_COMBINED_PLUS rows (two component EVCO PNs feeding one BOM, "+"-joined).
    for _ in range(8):
        mold, pn1 = next_ids()
        pn2 = pn1 + rng.randint(1, 3)
        cust_pn = f"{rng.randint(10000000, 19999999)}A{rng.randint(1,9)}"
        box_qty = rng.choice([10, 15, 38, 50, 550])
        moq = box_qty * rng.choice([1, 3, 5])
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn1), f"{pn1} + {pn2}", cust_pn, _description(rng), str(box_qty),
            [{"moq": str(moq), "price": _price(rng, 0.6, 110)}],
            ["VALID_BASELINE", "BOM_COMBINED_PLUS"],
            "BOM/manufacturing number combines two component EVCO part numbers with a '+' separator "
            "(two-piece assembly), matching the reference wide master-list template's convention.",
            family_code=rng.choice(["F", "I", None]),
        ))

    # Boundary / sparse-field / combined-rule rows.
    special_specs = [
        ("MOQ_EQUALS_BOX_QTY", "MOQ set exactly equal to Parts/Box - the smallest realistic single-box order."),
        ("BOX_QTY_MOQ_NOT_DIVISIBLE", "MOQ not an even multiple of Parts/Box."),
        ("MOQ_HIGH_VOLUME", "Very large MOQ (thousands of units)."),
        ("BLANK_CUSTOMER_PN", "Customer PN left blank on this single row only."),
        ("BLANK_MOQ_SINGLE_ROW", "MOQ left blank on this single row only."),
        ("SPECIAL_CHAR_PART_NUMBER", "Customer PN uses an alphanumeric format with an embedded letter suffix, "
         "exercising non-numeric part-number parsing."),
        ("COMBINED_MULTI_RULE", "Combines a family-suffix BOM variant with a high-volume MOQ tier."),
        ("COMBINED_MULTI_RULE", "Combines a '+' combined BOM with a boundary-low MOQ."),
    ]
    for tag, note in special_specs:
        mold, pn = next_ids()
        cust_pn = f"{rng.randint(40000000, 49999999)}"
        box_qty = rng.choice([12, 20, 40, 100])
        if tag == "MOQ_EQUALS_BOX_QTY":
            moq = box_qty
        elif tag == "BOX_QTY_MOQ_NOT_DIVISIBLE":
            moq = box_qty * 4 + 5
        elif tag == "MOQ_HIGH_VOLUME":
            moq = box_qty * rng.randint(100, 300)
        else:
            moq = box_qty * rng.randint(2, 10)
        row_no += 1
        customer_pn_val = "" if tag == "BLANK_CUSTOMER_PN" else (
            f"{cust_pn}-REV{rng.choice(['A','B','C'])}" if tag == "SPECIAL_CHAR_PART_NUMBER" else cust_pn
        )
        bom = f"{mold}/{pn}-{rng.choice(BOM_FAMILY_SUFFIXES)}" if "COMBINED_MULTI_RULE" == tag and rng.random() < 0.5 else f"{mold}/{pn}"
        rows.append(make_row(
            row_no, str(mold), str(pn), bom, customer_pn_val, _description(rng), str(box_qty),
            [{"moq": "" if tag == "BLANK_MOQ_SINGLE_ROW" else str(moq), "price": _price(rng, 0.5, 60)}],
            [tag], note,
            family_code=rng.choice(["F", "I", "S", "P", None]),
        ))

    # Struck row + cell override (this PDF also carries the effective-date override annotation
    # and a 2-line-wrapped "EVCO MFG (BOM)" header, both quote-level structural features).
    mold, pn = next_ids()
    row_no += 1
    rows.append(make_row(
        row_no, str(mold), str(pn), f"{mold}/{pn}", f"{rng.randint(40000000, 49999999)}", _description(rng),
        "60", [{"moq": "600", "price": _price(rng, 1, 20)}],
        ["STRUCK_ROW_RED_ANNOTATION"],
        "Entire row struck with a red line; must be excluded entirely from extraction.",
        family_code="S", annotation={"type": "strike_red"},
    ))
    mold, pn = next_ids()
    row_no += 1
    real_price = _price(rng, 5, 40)
    rows.append(make_row(
        row_no, str(mold), str(pn), f"{mold}/{pn}", f"{rng.randint(40000000, 49999999)}", _description(rng),
        "80", [{"moq": "800", "price": real_price}],
        ["CELL_OVERRIDE_RED_BOX"],
        f"Printed price {real_price} is visually overridden by a small red-outlined box; override value must win.",
        family_code="P", annotation={"type": "override_box", "override_price": _price(rng, 40, 70)},
    ))

    while row_no < 65:
        mold, pn = next_ids()
        box_qty = rng.choice([10, 20, 40, 80, 100])
        moq = box_qty * rng.choice([2, 5, 10])
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}", f"{rng.randint(40000000, 49999999)}", _description(rng),
            str(box_qty), [{"moq": str(moq), "price": _price(rng, 0.5, 50)}],
            ["VALID_BASELINE", "SINGLE_TIER_PRICING"],
            "Plain valid baseline row added for table volume/realism.",
            family_code=rng.choice(["F", "I", "S", "P", None, None, None]),
        ))

    return {
        "file": "EVCO_QUOTE_74022_DUMMY.pdf",
        "quote_number": "74022 - 007",
        "opportunity_id": "74022",
        "type": "Active",
        "issue_date": "06-10-26",
        "price_effective_date": "June 20, 2026",
        "customer": FICTIONAL_CUSTOMERS["northwind_co"],
        "template_style": "wide_master",
        "header_repeats_each_page": False,
        # Deliberately in "<YYYY> <Month> <D>" order (not the human-readable
        # "July 4, 2026") because it must satisfy parser.py's
        # EFFECTIVE_TO_RE = r"Effective\s*to\D{0,10}(\d{4})\s*([A-Za-z]+)\s*(\d{1,2})",
        # which expects the 4-digit year to immediately precede the month
        # name and day, mirroring the real "...Effective to 2026May31" markup.
        "effective_date_override": "2026 July 4",
        "garbled_two_line_header": True,
        "missing_required_column": None,
        "supersedes_prior_quote": True,
        "customer_similarity_test": True,
        "sales": FICTIONAL_CONTACTS["sales"][1],
        "cs": FICTIONAL_CONTACTS["cs"][1],
        "engineer": FICTIONAL_CONTACTS["engineer"][1],
        "plant": "Various",
        "cavities": "Various",
        "mold_numbers": "Various",
        "credit_terms": "Net 60 days",
        "shipping": "FOB Evco. Freight Collect",
        "lead_time_note": "12 weeks for North America locations; add 4 weeks for Non-North America locations.",
        "quote_comments": [
            "This quotation supersedes EVCO quote 74022-006, dated 5/1/2026.",
            "Notes on family/tool column: F - family tool, ordered/shipped in equal quantities with its pair. "
            "I - insert-change tool, capable of producing multiple p/ns. S - must be molded/shipped in the "
            "same color at the same time. P - customer provides racks for packaging.",
        ],
        "rows": rows,
        "resin_rows": [],
    }


# ---------------------------------------------------------------------------
# PDF 3 - "Align Technology" (short form of PDF2's real customer) - simple/decoy-column template (64399 style)
# ---------------------------------------------------------------------------

def build_pdf3():
    rng = random.Random(74023)
    rows = []
    row_no = 0
    mold_base = 6100
    pn_base = 9500000

    def next_ids():
        nonlocal mold_base, pn_base
        mold_base += 1
        pn_base += rng.randint(1, 5)
        return mold_base, pn_base

    special_specs = [
        ("AKA_QTY_BREAK_OF_ONE", "MOQ of literally 1 unit."),
        ("BOX_QTY_MOQ_NOT_DIVISIBLE", "MOQ not an even multiple of Parts/Box."),
        ("MOQ_LOW_BOUNDARY", "MOQ equal to a single box quantity."),
        ("MOQ_HIGH_VOLUME", "Large multi-thousand-unit MOQ."),
        ("BLANK_PRICE_SINGLE_ROW", "Price left as 'TBD' on this single row only."),
        ("BLANK_CUSTOMER_PN", "Customer PN left blank on this single row only."),
        ("MULTI_TIER_PRICING", "Two-tier quantity-break pricing on a single part."),
        ("COMBINED_MULTI_RULE", "Combines a high decoy Annual Volume figure with a small true MOQ, to stress "
         "the 'don't confuse adjacent quantity columns' rule."),
    ]
    for tag, note in special_specs:
        mold, pn = next_ids()
        cust_pn = f"{5500 + rng.randint(0, 400)}"
        box_qty = rng.choice([10, 20, 25, 40])
        decoy_qty = rng.choice([5000, 8000, 10000, 12000, 15000])
        if tag == "MOQ_LOW_BOUNDARY":
            moq = box_qty
        elif tag == "AKA_QTY_BREAK_OF_ONE":
            moq = 1
        elif tag == "BOX_QTY_MOQ_NOT_DIVISIBLE":
            moq = box_qty * 6 + 4
        elif tag == "MOQ_HIGH_VOLUME":
            moq = box_qty * rng.randint(100, 250)
        elif tag == "COMBINED_MULTI_RULE":
            moq = box_qty
            decoy_qty = rng.choice([50000, 75000, 100000])
        else:
            moq = box_qty * rng.randint(3, 20)
        tiers = [{"moq": "" if tag == "BLANK_MOQ_SINGLE_ROW" else str(moq),
                  "price": "" if tag == "BLANK_PRICE_SINGLE_ROW" else _price(rng, 0.9, 3.5)}]
        if tag == "MULTI_TIER_PRICING":
            base = float(tiers[0]["price"][1:])
            tiers.append({"moq": str(moq * 2), "price": f"${base * 0.88:.2f}"})
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}",
            "" if tag == "BLANK_CUSTOMER_PN" else cust_pn, _description(rng), str(box_qty),
            tiers, [tag], note, decoy_qty=str(decoy_qty),
        ))

    row_no += 1
    mold, pn = next_ids()
    rows.append(make_row(
        row_no, str(mold), str(pn), f"{mold}/{pn}", f"{5500 + rng.randint(0, 400)}", _description(rng),
        "20", [{"moq": "200", "price": _price(rng, 1, 3)}],
        ["STRUCK_ROW_PINK_ANNOTATION"],
        "Entire row struck with a pink line; must be excluded entirely from extraction.",
        decoy_qty="10000", annotation={"type": "strike_pink"},
    ))

    while row_no < 56:
        mold, pn = next_ids()
        box_qty = rng.choice([10, 20, 25, 40])
        moq = box_qty * rng.randint(3, 15)
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}", f"{5500 + rng.randint(0, 400)}", _description(rng),
            str(box_qty), [{"moq": str(moq), "price": _price(rng, 0.9, 4)}],
            ["VALID_BASELINE", "DECOY_QUANTITY_COLUMN_ROW"],
            "Plain valid baseline row; table-wide decoy 'Annual Volume' column sits adjacent to the true MOQ "
            "column, exercising the extractor's 'must not confuse adjacent quantity columns' rule.",
            decoy_qty=str(rng.choice([5000, 8000, 10000, 12000, 15000])),
        ))

    return {
        "file": "EVCO_QUOTE_74023_DUMMY.pdf",
        "quote_number": "74023 - 002",
        "opportunity_id": "74023",
        "type": "Active",
        "issue_date": "05-18-26",
        "price_effective_date": "June 1, 2026",
        "customer": FICTIONAL_CUSTOMERS["northwind_company"],
        "template_style": "simple_decoy",
        "header_repeats_each_page": True,
        "effective_date_override": None,
        "garbled_two_line_header": False,
        "missing_required_column": None,
        "supersedes_prior_quote": False,
        "customer_similarity_test": True,
        "sales": FICTIONAL_CONTACTS["sales"][2],
        "cs": FICTIONAL_CONTACTS["cs"][2],
        "engineer": FICTIONAL_CONTACTS["engineer"][2],
        "plant": "DeForest/OSH",
        "cavities": "1",
        "mold_numbers": "See Table",
        "credit_terms": "1% 10 Days Net 30",
        "shipping": "FOB EVCO Dock DeForest/OSH",
        "lead_time_note": "12 weeks for each unplanned order.",
        "quote_comments": [
            "Initial Active quote.",
            "Customer is responsible for excess materials not used within 90 days.",
            "Deliberately uses the short form of the same real customer printed as "
            "'ALIGN TECHNOLOGY INC' on EVCO_QUOTE_74022_DUMMY.pdf, to exercise "
            "CustomerRepository's name-similarity/ambiguous-match logic against a real "
            "two-record customer duplication (see README and rule_summary.json).",
        ],
        "rows": rows,
        "resin_rows": [
            {
                "material_pn": "0488121",
                "description": "Lexan 945 7T8B2928",
                "moq": "1,500 lbs",
                "lead_time": "7 weeks",
                "old_price": "$1.603/lb.",
                "new_price": "$1.55/lb.",
                "scenarios": ["RESIN_MATERIAL_ROW", "PRICE_INCREASE_HIGHLIGHT"],
                "notes": "Resin reference row; excluded from parts[] extraction by design (see PDF1 note).",
            }
        ],
    }


# ---------------------------------------------------------------------------
# PDF 4 - "GREENHECK FAN CORP" - EX-001 (Type: Inactive), tiered template reused
# ---------------------------------------------------------------------------

def build_pdf4():
    rng = random.Random(74024)
    rows = []
    row_no = 0
    mold_base = 3300
    pn_base = 9460000

    def next_ids():
        nonlocal mold_base, pn_base
        mold_base += rng.choice([1, 2, 5])
        pn_base += rng.randint(2, 30)
        return mold_base, pn_base

    tags_cycle = [
        ("VALID_BASELINE", "SINGLE_TIER_PRICING"), ("VALID_BASELINE", "MULTI_TIER_PRICING"),
        ("MOQ_LOW_BOUNDARY",), ("MOQ_HIGH_VOLUME",), ("LEAD_TIME_EXTENDED",),
        ("BOM_RESIN_VARIANT", "SINGLE_TIER_PRICING"),
    ]
    while row_no < 58:
        mold, pn = next_ids()
        box_qty = rng.choice([10, 20, 25, 40, 60])
        tags = tags_cycle[row_no % len(tags_cycle)]
        tier_count = 2 if "MULTI_TIER_PRICING" in tags else 1
        moq = box_qty if "MOQ_LOW_BOUNDARY" in tags else box_qty * (rng.randint(50, 150) if "MOQ_HIGH_VOLUME" in tags else rng.randint(2, 12))
        prices = _tiered_prices(rng, rng.uniform(3, 90), tier_count)
        tiers = [{"moq": str(moq * (i + 1)), "price": f"${p:.2f}"} for i, p in enumerate(prices)]
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}", f"{rng.randint(80000, 99999)}", _description(rng),
            str(box_qty), tiers, list(tags),
            "Row content is otherwise valid; the quote-level Type field ('Inactive') is the sole driver of "
            "EX-001 here, so this row is designed to look correct in isolation and never reaches "
            "post-extraction row checks because the pre-extraction fail-fast excludes the whole document.",
            material=rng.choice(MATERIALS), lead_time="20 Weeks" if "LEAD_TIME_EXTENDED" in tags else rng.choice(LEAD_TIMES),
        ))

    resin_rows = [{
        "material_pn": "1388200",
        "description": "Nylon 6/6 GF30 Black",
        "moq": "2,000 lbs",
        "lead_time": "9 weeks",
        "old_price": "$2.10/lb.",
        "new_price": "$2.10/lb.",
        "scenarios": ["RESIN_MATERIAL_ROW", "PRICE_UNCHANGED_ROW"],
        "notes": "Resin reference row; moot alongside the rest of this document once EX-001 excludes it.",
    }]

    return {
        "file": "EVCO_QUOTE_74024_DUMMY.pdf",
        "quote_number": "74024 - 001",
        "opportunity_id": "74024",
        "type": "Inactive",
        "issue_date": "04-02-26",
        "price_effective_date": "April 15, 2026",
        "customer": FICTIONAL_CUSTOMERS["ironclad"],
        "template_style": "tiered_resin",
        "header_repeats_each_page": True,
        "effective_date_override": None,
        "garbled_two_line_header": False,
        "missing_required_column": None,
        "supersedes_prior_quote": False,
        "customer_similarity_test": False,
        "sales": FICTIONAL_CONTACTS["sales"][3],
        "cs": FICTIONAL_CONTACTS["cs"][3],
        "engineer": FICTIONAL_CONTACTS["engineer"][3],
        "plant": "DeForest/OSH",
        "cavities": "1",
        "mold_numbers": "See Table",
        "credit_terms": "1% 10 Days Net 30",
        "shipping": "FOB EVCO Dock DeForest/OSH",
        "lead_time_note": "Mfg. lead time as noted per line.",
        "quote_comments": [
            "This quotation has been superseded and is retained here only as an inactive historical record.",
            "Customer is responsible for excess materials not used within 90 days.",
        ],
        "rows": rows,
        "resin_rows": resin_rows,
    }


# ---------------------------------------------------------------------------
# PDF 5 - "HARLEY-DAVIDSON MOTOR CO" - EX-002 (missing header field + missing
# required column entirely), wide master-list template reused
# ---------------------------------------------------------------------------

def build_pdf5():
    rng = random.Random(74025)
    rows = []
    row_no = 0
    mold_base = 5200
    pn_base = 9470000

    def next_ids():
        nonlocal mold_base, pn_base
        mold_base += rng.choice([1, 3, 8])
        pn_base += rng.randint(3, 45)
        return mold_base, pn_base

    tags_cycle = [
        ("VALID_BASELINE", "SINGLE_TIER_PRICING"), ("VALID_BASELINE", "MULTI_TIER_PRICING"),
        ("BOM_SUFFIX_VARIANT",), ("BOM_COMBINED_PLUS",), ("MOQ_LOW_BOUNDARY",),
        ("MOQ_HIGH_VOLUME",), ("BOX_QTY_MOQ_NOT_DIVISIBLE",),
    ]
    while row_no < 62:
        mold, pn = next_ids()
        box_qty = rng.choice([10, 20, 40, 60, 100])
        tags = tags_cycle[row_no % len(tags_cycle)]
        tier_count = 2 if "MULTI_TIER_PRICING" in tags else 1
        if "MOQ_LOW_BOUNDARY" in tags:
            moq = box_qty
        elif "MOQ_HIGH_VOLUME" in tags:
            moq = box_qty * rng.randint(80, 200)
        elif "BOX_QTY_MOQ_NOT_DIVISIBLE" in tags:
            moq = box_qty * 5 + 7
        else:
            moq = box_qty * rng.randint(2, 10)
        prices = _tiered_prices(rng, rng.uniform(0.6, 60), tier_count)
        tiers = [{"moq": str(moq * (i + 1)), "price": f"${p:.2f}"} for i, p in enumerate(prices)]
        bom = f"{mold}/{pn}"
        if "BOM_SUFFIX_VARIANT" in tags:
            bom = f"{mold}/{pn}-{rng.choice(BOM_FAMILY_SUFFIXES)}"
        elif "BOM_COMBINED_PLUS" in tags:
            bom = f"{pn} + {pn + 1}"
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), bom, f"{rng.randint(40000000, 49999999)}", _description(rng),
            str(box_qty), tiers, list(tags),
            "Row content is otherwise realistic and varied; this document's EX-002 trigger is quote-level "
            "(a missing 'PRICE EFFECTIVE DATE:' header line, and the EVCO MFG (BOM) column dropped from the "
            "table header entirely) and fires before any row-level check would run, per "
            "validate_before_extraction()'s fail-fast ordering in app/modules/extraction/parser.py.",
        ))

    return {
        "file": "EVCO_QUOTE_74025_DUMMY.pdf",
        "quote_number": "74025 - 003",
        "opportunity_id": "74025",
        "type": "Active",
        "issue_date": "08-05-26",
        "price_effective_date": "",  # intentionally omitted -> EX-002 (missing header field)
        "customer": FICTIONAL_CUSTOMERS["falcon"],
        "template_style": "wide_master",
        "header_repeats_each_page": False,
        "effective_date_override": None,
        "garbled_two_line_header": False,
        "missing_required_column": "manufacturing_bom_number",  # BOM column entirely absent -> EX-002 (missing column)
        "supersedes_prior_quote": True,
        "customer_similarity_test": False,
        "sales": FICTIONAL_CONTACTS["sales"][4],
        "cs": FICTIONAL_CONTACTS["cs"][4],
        "engineer": FICTIONAL_CONTACTS["engineer"][4],
        "plant": "Various",
        "cavities": "Various",
        "mold_numbers": "Various",
        "credit_terms": "Net 60 days",
        "shipping": "FOB Evco. Freight Collect",
        "lead_time_note": "12 weeks for North America locations.",
        "quote_comments": [
            "This quotation supersedes EVCO quote 74025-002, dated 6/1/2026.",
            "PRICE EFFECTIVE DATE intentionally omitted from this document's header, and the EVCO MFG (BOM) "
            "column intentionally omitted from the Part Pricing table, to exercise EX-002 (required "
            "information missing) via two independent causes in one document.",
        ],
        "rows": rows,
        "resin_rows": [],
    }


# ---------------------------------------------------------------------------
# PDF 6 - "ACUITY BRANDS LIGHTING INC" - per-row blank-field template: one row
# with Mold blank (soItemNumber gap), one row with Price blank (EX-003)
# ---------------------------------------------------------------------------

def build_pdf6():
    rng = random.Random(74026)
    rows = []
    row_no = 0
    mold_base = 7100
    pn_base = 9490000

    def next_ids():
        nonlocal mold_base, pn_base
        mold_base += rng.choice([1, 2, 4])
        pn_base += rng.randint(2, 20)
        return mold_base, pn_base

    # Single-tier rows, one blank field each.
    single_tier_specs = [
        ("BLANK_MOLD_SINGLE_ROW", "Mold intentionally left blank on this single row only - soItemNumber "
         "has nothing to populate from for this row, the rest of the row is otherwise valid."),
        ("BLANK_PRICE_SINGLE_ROW", "Price intentionally left as 'TBD' on this single row only (MOQ still present)."),
        ("BLANK_CUSTOMER_PN", "Customer PN intentionally left blank on this single row only."),
        ("BLANK_DESCRIPTION", "Part Description intentionally left blank on this single row only."),
        ("BLANK_PARTS_BOX_SINGLE_ROW", "Parts/Box intentionally left blank on this single row only "
         "(MOQ and price still present)."),
    ]
    for tag, note in single_tier_specs:
        mold, pn = next_ids()
        cust_pn = f"{6600 + rng.randint(0, 300)}"
        box_qty = rng.choice([10, 20, 30, 50])
        moq = box_qty * rng.randint(3, 12)
        row_no += 1
        rows.append(make_row(
            row_no, "" if tag == "BLANK_MOLD_SINGLE_ROW" else str(mold), str(pn), f"{mold}/{pn}",
            "" if tag == "BLANK_CUSTOMER_PN" else cust_pn,
            "" if tag == "BLANK_DESCRIPTION" else _description(rng),
            "" if tag == "BLANK_PARTS_BOX_SINGLE_ROW" else str(box_qty),
            [{"moq": str(moq), "price": "" if tag == "BLANK_PRICE_SINGLE_ROW" else _price(rng, 5, 40)}],
            [tag], note,
        ))

    # Same blank-field gaps, but on genuinely multi-tier (2-break) pricing rows -
    # proves the missing-field check fires per-row regardless of how many
    # pricing tiers that row carries, not just on the simplest single-tier case.
    multi_tier_specs = [
        ("BLANK_CUSTOMER_PN", "Customer PN intentionally left blank on this multi-tier pricing row."),
        ("BLANK_DESCRIPTION", "Part Description intentionally left blank on this multi-tier pricing row."),
        ("BLANK_PARTS_BOX_SINGLE_ROW", "Parts/Box intentionally left blank on this multi-tier pricing row."),
    ]
    for tag, note in multi_tier_specs:
        mold, pn = next_ids()
        cust_pn = f"{6600 + rng.randint(0, 300)}"
        box_qty = rng.choice([10, 20, 30, 50])
        base_moq = box_qty * rng.randint(3, 8)
        prices = _tiered_prices(rng, rng.uniform(10, 40), 2)
        tiers = [{"moq": str(base_moq * (i + 1)), "price": f"${p:.2f}"} for i, p in enumerate(prices)]
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}",
            "" if tag == "BLANK_CUSTOMER_PN" else cust_pn,
            "" if tag == "BLANK_DESCRIPTION" else _description(rng),
            "" if tag == "BLANK_PARTS_BOX_SINGLE_ROW" else str(box_qty),
            tiers, [tag, "MULTI_TIER_PRICING"], note,
        ))

    while row_no < 24:
        mold, pn = next_ids()
        box_qty = rng.choice([10, 20, 30, 50])
        moq = box_qty * rng.randint(3, 12)
        row_no += 1
        rows.append(make_row(
            row_no, str(mold), str(pn), f"{mold}/{pn}", f"{6600 + rng.randint(0, 300)}", _description(rng),
            str(box_qty), [{"moq": str(moq), "price": _price(rng, 5, 40)}],
            ["VALID_BASELINE"],
            "Plain valid baseline row.",
        ))

    return {
        "file": "EVCO_QUOTE_74026_DUMMY.pdf",
        "quote_number": "74026 - 001",
        "opportunity_id": "74026",
        "type": "Active",
        "issue_date": "09-10-26",
        "price_effective_date": "September 21, 2026",
        "customer": FICTIONAL_CUSTOMERS["skyline"],
        "template_style": "simple_decoy",
        "header_repeats_each_page": True,
        "effective_date_override": None,
        "garbled_two_line_header": False,
        "missing_required_column": None,
        "supersedes_prior_quote": False,
        "customer_similarity_test": False,
        "sales": FICTIONAL_CONTACTS["sales"][0],
        "cs": FICTIONAL_CONTACTS["cs"][0],
        "engineer": FICTIONAL_CONTACTS["engineer"][0],
        "plant": "DeForest/OSH",
        "cavities": "1",
        "mold_numbers": "See Table",
        "credit_terms": "1% 10 Days Net 30",
        "shipping": "FOB EVCO Dock DeForest/OSH",
        "lead_time_note": "12 weeks for each unplanned order.",
        "quote_comments": [
            "Initial Active quote.",
            "Individual rows intentionally omit one required field each - Mold, Price, Customer PN, "
            "Part Description, or Parts/Box - to exercise EX-003 (required quote-line info missing) and "
            "the soItemNumber gap (Mold) across both single-tier and multi-tier pricing rows.",
        ],
        "rows": rows,
        "resin_rows": [],
    }


def build_all_quotes():
    return [build_pdf1(), build_pdf2(), build_pdf3(), build_pdf4(), build_pdf5(), build_pdf6()]
