"""
Generate the EVCO mock quote PDFs (and their accompanying manifests /
coverage docs) from evco_mock_data/data/mock_quote_data.py.

This script only writes inside evco_mock_data/ - it never touches the
existing EVCO backend project (app/, requirements.txt, etc.). It uses
PyMuPDF (fitz), which is already a dependency of the real project's PDF
extractor (app/modules/extraction/parser.py), so no new dependency is
introduced.

Usage:
    python generate_mock_quotes.py
"""

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.mock_quote_data import build_all_quotes, EVCO_ADDRESS_LINE, DUMMY_DISCLAIMER  # noqa: E402
from data.rule_catalog import RULE_CATALOG, SCENARIO_NO_RULE_REASON, quote_level_rules, rules_for_scenarios  # noqa: E402

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "pdfs"
DATA_DIR = ROOT / "data"

PAGE_W, PAGE_H = fitz.paper_size("letter")
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 36, 36, 40, 46
CONTENT_L, CONTENT_R = MARGIN_L, PAGE_W - MARGIN_R

BLACK = (0, 0, 0)
GRAY = (0.15, 0.15, 0.15)
LIGHT_GRAY = (0.85, 0.85, 0.85)
RED = (0.8, 0.08, 0.08)      # matches parser._classify_color's "red" band
PINK = (0.92, 0.55, 0.78)    # matches parser._classify_color's "pink" band
# Exact RGB measured from the real reference quote's table header band
# (C:\Data\Incoming\65150-003.pdf, fill=(0.0, 0.30998..., 0.61599...)).
EVCO_BLUE = (0.0, 0.31, 0.616)
WHITE = (1, 1, 1)

# Real Arial TTFs (Windows system fonts) embedded directly, rather than
# PyMuPDF's Base14 Helvetica substitute - measured directly against
# C:\Data\Incoming\65150-003.pdf, whose text spans are 'ArialMT'/'Arial-BoldMT'
# throughout (title/customer block at 11-11.5pt, disclaimer at Arial-Bold
# 6.5pt, Part Pricing table header at Arial-Bold 10pt white-on-blue, data at
# Arial 10pt with Arial-Bold price values) - not a Helvetica look-alike.
_WINFONTS = Path(r"C:\Windows\Fonts")
FONT_FILES = {
    "EvcoArial": _WINFONTS / "arial.ttf",
    "EvcoArialBold": _WINFONTS / "arialbd.ttf",
}
FONT = "EvcoArial"
FONT_BOLD = "EvcoArialBold"
_FONTS_AVAILABLE = all(p.exists() for p in FONT_FILES.values())
if not _FONTS_AVAILABLE:
    # Fall back to Base14 Helvetica if the Windows fonts aren't present
    # (e.g. a non-Windows CI runner) rather than failing generation outright.
    FONT, FONT_BOLD = "helv", "hebo"


def register_fonts(page):
    """Embed the real Arial faces on this page so insert_text(fontname=FONT/
    FONT_BOLD, ...) resolves to them instead of a Base14 substitute. Must be
    called once per newly-created page before any text is drawn on it.

    set_simple=True matters: PyMuPDF's default (False) embeds the font as a
    Type0/CID font and auto-builds a ToUnicode CMap that - observed directly -
    silently substitutes lookalike codepoints on round-trip text extraction
    (a plain space came back as U+00A0 NBSP, a hyphen as U+00AD soft hyphen),
    even though the glyphs render correctly. Confirmed this is NOT how the
    real reference quotes' own embedded Arial behaves (their extracted text
    has plain spaces/hyphens) - set_simple=True embeds a simple TrueType font
    with standard WinAnsi-style encoding instead, which round-trips clean.
    """
    if not _FONTS_AVAILABLE:
        return
    page.insert_font(fontname="EvcoArial", fontfile=str(FONT_FILES["EvcoArial"]), set_simple=True)
    page.insert_font(fontname="EvcoArialBold", fontfile=str(FONT_FILES["EvcoArialBold"]), set_simple=True)


# fitz.get_text_length() only knows the 14 built-in Base14 fonts, not custom
# embedded TTFs - fitz.Font(fontfile=...).text_length() measures the real
# Arial/Arial-Bold metrics instead, so _wrap_lines() below never overflows a
# column at the actual font this page draws with.
_ARIAL_FONT = fitz.Font(fontfile=str(FONT_FILES["EvcoArial"])) if _FONTS_AVAILABLE else None
_ARIAL_BOLD_FONT = fitz.Font(fontfile=str(FONT_FILES["EvcoArialBold"])) if _FONTS_AVAILABLE else None


def _text_length(text, fontname, size):
    if fontname == "EvcoArial" and _ARIAL_FONT is not None:
        return _ARIAL_FONT.text_length(text, fontsize=size)
    if fontname == "EvcoArialBold" and _ARIAL_BOLD_FONT is not None:
        return _ARIAL_BOLD_FONT.text_length(text, fontsize=size)
    return fitz.get_text_length(text, fontname="helv", fontsize=size)


# ---------------------------------------------------------------------------
# Column layouts per template style
# ---------------------------------------------------------------------------

def build_columns(quote):
    style = quote["template_style"]
    x = CONTENT_L
    cols = []

    def add(field, label_lines, width):
        nonlocal x
        cols.append({"field": field, "label": label_lines, "x0": x, "width": width})
        x += width

    # Every branch's widths are hand-balanced to sum to exactly
    # CONTENT_R - CONTENT_L (540pt) so the table always spans the full page
    # width - and every header label is pre-wrapped to 2 lines wherever a
    # single line would exceed its column's width at HEADER_FONT_SIZE.
    if style == "tiered_resin":
        add("mold", ["Mold"], 30)
        add("evco_pn", ["EVCO", "PN"], 42)
        add("customer_pn", ["Customer", "PN"], 52)
        add("bom", ["Evco BOM"], 76)
        add("description", ["Part Description"], 92)
        add("material", ["Material"], 66)
        add("box_qty", ["Parts/", "Box"], 36)
        add("moq", ["MOQ"], 34)
        add("lead_time", ["Mfg. Lead", "Time"], 54)
        add("price", ["Price Each"], 58)
    elif style == "wide_master":
        has_bom = quote.get("missing_required_column") != "manufacturing_bom_number"
        add("mold", ["Mold"], 34 if has_bom else 38)
        add("evco_pn", ["EVCO PN"], 50 if has_bom else 56)
        if has_bom:
            if quote.get("garbled_two_line_header"):
                add("bom", ["EVCO MFG", "(BOM)"], 84)
            else:
                add("bom", ["EVCO MFG", "(BOM)"], 84)
        add("customer_pn", ["Customer PN"], 68 if has_bom else 82)
        add("description", ["Part Desc"], 112 if has_bom else 150)
        add("box_qty", ["Box Qty"], 44 if has_bom else 50)
        add("moq", ["MOQ"], 42 if has_bom else 52)
        add("family_code", ["B"], 18 if has_bom else 20)
        add("price", ["Pricing"], 88 if has_bom else 92)
    elif style == "simple_decoy":
        add("mold", ["Mold"], 30)
        add("evco_pn", ["EVCO", "PN"], 44)
        add("bom", ["EVCO", "MFG#"], 72)
        add("customer_pn", ["Customer", "PN"], 54)
        add("description", ["Part Description"], 130)
        add("decoy_qty", ["Annual", "Volume"], 56)
        add("box_qty", ["Parts/", "Box"], 44)
        add("moq", ["MOQ"], 38)
        add("price", ["Price Each"], 72)
    else:
        raise ValueError(style)

    total_width = sum(c["width"] for c in cols)
    assert abs(total_width - (CONTENT_R - CONTENT_L)) < 0.01, (
        f"{style} column widths sum to {total_width}, expected {CONTENT_R - CONTENT_L}"
    )
    # A header label that overflows its column bleeds into the next cell's
    # text - PyMuPDF's own table-grid header parse then merges/misattributes
    # the spillover character(s) (observed directly: "Customer" overflowing
    # into "Evco BOM" produced a header of "Custome"/"rEvco BOM"), which
    # corrupts find_master_table_header()'s column-alias matching for real.
    # Catch that at generation time instead of only in validate_mock_quotes.py.
    for c in cols:
        for line in c["label"]:
            w = _text_length(line, FONT_BOLD, DATA_FONT_SIZE)
            assert w <= c["width"] - 4, (
                f"{style} column {c['field']!r} label {line!r} is {w:.1f}pt wide, "
                f"doesn't fit its {c['width']}pt column - widen it or shorten the label"
            )
    return cols


# ---------------------------------------------------------------------------
# Row height estimation (shared by the pagination dry-run and real render)
# ---------------------------------------------------------------------------

# Matches the real reference quote's measured Part Pricing table: Arial
# 10pt data rows, ~13.9pt row height (C:\Data\Incoming\65150-003.pdf).
LINE_H = 14
DATA_FONT_SIZE = 10
# Every field except moq/price (which stack one value per pricing tier
# instead) is wrap-safe: at 10pt Arial a numeric field like a 7-digit EVCO PN
# can get tight in a narrow column, and wrapping to 2 lines on the rare
# overflow is a far safer failure mode than silently drawing past the
# column's grid line.
WRAP_FIELDS = ("mold", "evco_pn", "customer_pn", "bom", "description", "material",
               "box_qty", "decoy_qty", "lead_time", "family_code")

# Breakable at whitespace AND at '/', '-', '+' (kept attached to the preceding
# chunk) - part/BOM identifiers like "6601/9480026-CHIMEI" have no spaces at
# all, and the real reference quotes visibly wrap them at exactly these
# punctuation points (e.g. "6682/9475227-\nCHIMEI" in 65150-003.pdf).
_WRAP_TOKEN_RE = re.compile(r"[^/\-+\s]*[/\-+\s]|[^/\-+\s]+$")


def _tokenize_wrappable(text):
    return [t for t in _WRAP_TOKEN_RE.findall(text) if t]


def _wrap_lines(text, width, size=DATA_FONT_SIZE, fontname=FONT):
    """Greedy word-wrap using the real embedded font's metric text width
    (_text_length, backed by fitz.Font.text_length on the actual Arial TTF),
    not a character-count guess - guarantees rendered text never exceeds
    `width`, and that row_height() (which calls this) always matches what
    draw_wrapped() draws."""
    if not text:
        return []
    text = str(text)
    tokens = _tokenize_wrappable(text)
    lines, cur = [], ""
    for tok in tokens:
        trial = cur + tok
        if cur and _text_length(trial.rstrip(), fontname, size) > width:
            lines.append(cur.rstrip())
            cur = tok
        else:
            cur = trial
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [text]


def row_height(row, cols_by_field):
    tier_lines = max(len(row["tiers"]), 1)
    wrap_lines = 1
    for field in WRAP_FIELDS:
        col = cols_by_field.get(field)
        if col:
            wrap_lines = max(wrap_lines, len(_wrap_lines(row.get(field), col["width"] - 4)))
    return LINE_H * max(tier_lines, wrap_lines) + 6


def header_row_height(cols):
    max_lines = max(len(c["label"]) for c in cols)
    return LINE_H * max_lines + 10


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------

def draw_text(page, x, y, text, size=DATA_FONT_SIZE, color=BLACK, bold=False):
    if not text:
        return
    page.insert_text((x, y), str(text), fontsize=size, fontname=FONT if not bold else FONT_BOLD, color=color)


def draw_wrapped(page, x, width, y_top, text, size=DATA_FONT_SIZE, color=BLACK, line_h=LINE_H, bold=False):
    fontname = FONT_BOLD if bold else FONT
    for i, ln in enumerate(_wrap_lines(text, width, size, fontname)):
        page.insert_text((x, y_top + (i + 1) * line_h - 2), ln, fontsize=size, fontname=fontname, color=color)


def draw_grid_row(page, y0, y1, cols):
    page.draw_line((CONTENT_L, y1), (cols[-1]["x0"] + cols[-1]["width"], y1), color=GRAY, width=0.6)
    for c in cols:
        page.draw_line((c["x0"], y0), (c["x0"], y1), color=GRAY, width=0.6)
    last = cols[-1]
    page.draw_line((last["x0"] + last["width"], y0), (last["x0"] + last["width"], y1), color=GRAY, width=0.6)


def draw_dummy_banner(page, y_top):
    """A bordered 'not a real quote' box, placed below the quote metadata block
    so it never overlaps other header text - mirrors the real
    '58312-035A MOCK PROD QUOTE.pdf' reference's own NOT A REAL QUOTE box
    convention. No full-page watermark: overlapping vector text on top of the
    Part Pricing table would land in the same y-band as real table text and
    corrupt PyMuPDF's reading-order text extraction (see build_annotated_page_text
    in app/modules/extraction/parser.py), which must stay clean for a mock PDF
    meant to exercise that exact extractor.
    """
    rect = fitz.Rect(CONTENT_R - 170, y_top, CONTENT_R, y_top + 16)
    page.draw_rect(rect, color=RED, width=1.2)
    page.insert_text((rect.x0 + 4, rect.y1 - 5), "MOCK DATA - NOT A REAL QUOTE",
                      fontsize=7.5, fontname=FONT_BOLD, color=RED)


def draw_page_chrome(page, quote, page_no_placeholder, show_full_header):
    # Font sizes/weights below are measured directly against the real
    # reference quote (C:\Data\Incoming\65150-003.pdf): title/quote-metadata
    # Arial-Bold 11pt, customer block Arial(-Bold) 11.5pt, address line Arial
    # 9pt, disclaimer Arial-Bold 6.5pt, PRICE EFFECTIVE DATE as a single
    # Arial-Bold 11.5pt line - not a generic/approximate style.
    y = MARGIN_T
    page.insert_text((MARGIN_L, y), "evco", fontsize=22, fontname=FONT_BOLD, color=EVCO_BLUE)
    page.insert_text((MARGIN_L, y + 14), "Where Innovation Takes Form", fontsize=7, fontname=FONT, color=GRAY)
    page.insert_text((PAGE_W / 2 - 55, y - 6), "PRICE QUOTATION", fontsize=11, fontname=FONT_BOLD, color=BLACK)

    # Right column: every block below gets its own fixed y-offset from `y`
    # (MARGIN_T), rather than being derived from the left column's cumulative
    # height - the two columns' content lengths are independent (a short
    # customer address vs. a long one, an omitted effective date, etc.), so
    # deriving one column's position from the other's height is exactly what
    # caused the banner/PRICE EFFECTIVE DATE/quote-metadata collisions seen in
    # earlier drafts of this generator.
    right_x = CONTENT_R - 175
    page.insert_text((right_x, y - 6), f"Quote#: {quote['quote_number']}", fontsize=11, fontname=FONT_BOLD, color=BLACK)
    page.insert_text((right_x, y + 7), f"Type: {quote['type']}", fontsize=11, fontname=FONT_BOLD, color=BLACK)
    page.insert_text((right_x, y + 20), f"Issue Date: {quote['issue_date']}", fontsize=11, fontname=FONT_BOLD, color=BLACK)
    draw_dummy_banner(page, y + 36)

    if show_full_header:
        if quote["price_effective_date"]:
            ped_text = f"PRICE EFFECTIVE DATE:   {quote['price_effective_date']}"
            ped_size = 11.5
            ped_width = _text_length(ped_text, FONT_BOLD, ped_size)
            # Right-align within the content area instead of a fixed x - this
            # line is long enough (label + date) that a fixed offset tuned for
            # the shorter Quote#/Type/Issue Date lines above can run off the
            # page edge for a long date string.
            ped_x = max(MARGIN_L + 260, CONTENT_R - ped_width)
            page.insert_text((ped_x, y + 62), ped_text, fontsize=ped_size, fontname=FONT_BOLD, color=BLACK)
        if quote.get("effective_date_override"):
            # Must match parser.py's EFFECTIVE_TO_RE exactly: "Effective to"
            # followed directly by "<YYYY> <Month> <D>" (see mock_quote_data.py).
            page.insert_text((right_x, y + 78), f"SAM: Effective to {quote['effective_date_override']}",
                              fontsize=8, fontname=FONT, color=RED)

    y += 32
    page.insert_text((MARGIN_L, y), EVCO_ADDRESS_LINE, fontsize=9, fontname=FONT, color=BLACK)
    y += 15

    if show_full_header:
        cust = quote["customer"]
        page.insert_text((MARGIN_L, y), cust["name"], fontsize=11.5, fontname=FONT_BOLD, color=BLACK)
        y += 14
        for line in cust["address"]:
            page.insert_text((MARGIN_L, y), line, fontsize=11.5, fontname=FONT, color=BLACK)
            y += 13
        page.insert_text((MARGIN_L, y), cust["attention"], fontsize=11.5, fontname=FONT, color=BLACK)
        y += 20

        # The right column (PRICE EFFECTIVE DATE / override) reaches at most
        # MARGIN_T + 92; make sure the disclaimer paragraph starts below both
        # columns regardless of which one ran longer.
        y = max(y, MARGIN_T + 96)
        draw_wrapped(page, MARGIN_L, PAGE_W - MARGIN_L - MARGIN_R, y, DUMMY_DISCLAIMER,
                     size=6.5, color=BLACK, line_h=8, bold=True)
        y += 24

    return y + 8


def draw_footer(page, quote, page_no, total_pages):
    y = PAGE_H - MARGIN_B + 18
    page.insert_text((MARGIN_L, y), f"Opportunity ID: {quote['opportunity_id']}", fontsize=7, fontname=FONT, color=GRAY)
    page.insert_text((MARGIN_L, y + 10), "SF 2.036, Rev. 9/24/25 (MOCK)", fontsize=7, fontname=FONT, color=GRAY)
    page.insert_text((CONTENT_R - 70, y), f"Page {page_no} of {total_pages}", fontsize=7, fontname=FONT, color=GRAY)


# ---------------------------------------------------------------------------
# Table rendering (dry-run for pagination, then real render with annotations)
# ---------------------------------------------------------------------------

TABLE_TOP_MARGIN = 8
TABLE_BOTTOM_LIMIT = PAGE_H - MARGIN_B - 10


def render_table(doc, quote, cols, first_page, start_y, continuation_top, page_no_state, total_pages):
    """Render every data row across as many pages as needed. Returns list of row render-info dicts."""
    hdr_h = header_row_height(cols)
    cols_by_field = {c["field"]: c for c in cols}
    page = first_page
    y = start_y
    render_info = []

    def draw_header_row(pg, y0):
        y1 = y0 + hdr_h
        # Blue band + white bold text, matching the real reference quote's
        # table header exactly (C:\Data\Incoming\65150-003.pdf).
        pg.draw_rect(fitz.Rect(cols[0]["x0"], y0, cols[-1]["x0"] + cols[-1]["width"], y1),
                     color=None, fill=EVCO_BLUE)
        for c in cols:
            for li, line in enumerate(c["label"]):
                pg.insert_text((c["x0"] + 3, y0 + (li + 1) * LINE_H), line, fontsize=DATA_FONT_SIZE,
                               fontname=FONT_BOLD, color=WHITE)
        draw_grid_row(pg, y0, y1, cols)
        return y1

    header_drawn_on_this_page = False
    y = draw_header_row(page, y)
    header_drawn_on_this_page = True

    for row in quote["rows"]:
        rh = row_height(row, cols_by_field)
        if y + rh > TABLE_BOTTOM_LIMIT:
            draw_footer(page, quote, page_no_state[0], total_pages)
            page_no_state[0] += 1
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            register_fonts(page)
            y = draw_page_chrome(page, quote, page_no_state[0], show_full_header=False)
            y = max(y, continuation_top)
            if quote["header_repeats_each_page"]:
                y = draw_header_row(page, y)
            header_drawn_on_this_page = quote["header_repeats_each_page"]

        y0 = y
        y1 = y + rh
        for c in cols:
            field = c["field"]
            if field == "moq":
                for ti, tier in enumerate(row["tiers"]):
                    draw_text(page, c["x0"] + 2, y0 + (ti + 1) * LINE_H, tier.get("moq", ""))
            elif field == "price":
                for ti, tier in enumerate(row["tiers"]):
                    val = tier.get("price", "")
                    # Price values are always bold, matching the real reference
                    # quote's Arial-Bold price convention (65150-003.pdf). The
                    # override annotation itself is drawn separately, on top,
                    # by apply_annotations() - this printed value stays as-is.
                    draw_text(page, c["x0"] + 2, y0 + (ti + 1) * LINE_H, val, bold=True)
            elif field in WRAP_FIELDS:
                draw_wrapped(page, c["x0"] + 2, c["width"] - 4, y0, row.get(field) or "")
            else:
                val = row.get(field)
                draw_text(page, c["x0"] + 2, y0 + LINE_H, val if val is not None else "")
        draw_grid_row(page, y0, y1, cols)

        render_info.append({
            "row": row["row"], "page_index": page.number, "y0": y0, "y1": y1,
            "price_col_x0": cols[-1]["x0"], "price_col_x1": cols[-1]["x0"] + cols[-1]["width"],
        })
        y = y1

    draw_footer(page, quote, page_no_state[0], total_pages)
    page_no_state[0] += 1
    return render_info, page


def apply_annotations(doc, quote, render_info):
    info_by_row = {r["row"]: r for r in render_info}
    for row in quote["rows"]:
        ann = row.get("annotation")
        if not ann:
            continue
        info = info_by_row[row["row"]]
        page = doc[info["page_index"]]
        y_mid = (info["y0"] + info["y1"]) / 2
        if ann["type"] in ("strike_red", "strike_pink"):
            color = RED if ann["type"] == "strike_red" else PINK
            page.draw_line((CONTENT_L + 2, y_mid), (info["price_col_x1"] - 2, y_mid), color=color, width=2.2)
        elif ann["type"] == "override_box":
            # Opaque white fill first, so the box fully covers the original
            # printed value underneath rather than visually overlapping it -
            # only the override value (in red, on top) should be legible here.
            size = 9.5
            text_w = _text_length(ann["override_price"], FONT_BOLD, size)
            rect = fitz.Rect(info["price_col_x0"] + 1, info["y0"] + 1,
                              info["price_col_x0"] + 1 + text_w + 6, info["y0"] + LINE_H + 1)
            page.draw_rect(rect, color=RED, fill=WHITE, width=1.1)
            page.insert_text((rect.x0 + 3, rect.y1 - 3), ann["override_price"],
                              fontsize=size, fontname=FONT_BOLD, color=RED)


# ---------------------------------------------------------------------------
# Resin/material + terms/comments pages
# ---------------------------------------------------------------------------

def draw_resin_table(doc, quote, page_no_state, total_pages):
    if not quote["resin_rows"]:
        return
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    register_fonts(page)
    y = draw_page_chrome(page, quote, page_no_state[0], show_full_header=False)
    page.insert_text((MARGIN_L, y), "Materials / Resin Pricing", fontsize=10, fontname=FONT_BOLD, color=BLACK)
    y += 14

    cols = [
        {"field": "material_pn", "label": ["Evco PN"], "x0": CONTENT_L, "width": 60},
        {"field": "description", "label": ["Description"], "x0": CONTENT_L + 60, "width": 160},
        {"field": "moq", "label": ["MOQ"], "x0": CONTENT_L + 220, "width": 70},
        {"field": "lead_time", "label": ["Lead Time"], "x0": CONTENT_L + 290, "width": 60},
        {"field": "old_price", "label": ["Old Price"], "x0": CONTENT_L + 350, "width": 70},
        {"field": "new_price", "label": ["New Price"], "x0": CONTENT_L + 420, "width": 70},
    ]
    for c in cols:
        page.insert_text((c["x0"] + 2, y + LINE_H), c["label"][0], fontsize=8.2, fontname=FONT_BOLD, color=BLACK)
    y1 = y + LINE_H + 4
    draw_grid_row(page, y, y1, cols)
    y = y1
    for r in quote["resin_rows"]:
        y0 = y
        y1 = y + LINE_H + 4
        for c in cols:
            draw_text(page, c["x0"] + 2, y0 + LINE_H, r.get(c["field"], ""))
        draw_grid_row(page, y0, y1, cols)
        y = y1

    y += 14
    page.insert_text((MARGIN_L, y), "In-Process Regrind Will Be Utilized Where Possible.", fontsize=7.5, fontname=FONT)
    y += 10
    page.insert_text((MARGIN_L, y), "Customer is responsible for excess materials not used within 90 days.",
                      fontsize=7.5, fontname=FONT)
    draw_footer(page, quote, page_no_state[0], total_pages)
    page_no_state[0] += 1


def draw_terms_page(doc, quote, page_no_state, total_pages):
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    register_fonts(page)
    y = draw_page_chrome(page, quote, page_no_state[0], show_full_header=(quote["template_style"] == "wide_master"))
    page.insert_text((MARGIN_L, y), "Quote Comments", fontsize=10, fontname=FONT_BOLD, color=BLACK)
    y += 12
    for line in quote["quote_comments"]:
        draw_wrapped(page, MARGIN_L, PAGE_W - MARGIN_L - MARGIN_R, y, f"• {line}", size=7.5, line_h=10)
        y += 10 * max(1, math.ceil(len(line) / 100)) + 4

    y += 10
    for label, value in [("Credit Terms", quote["credit_terms"]), ("Shipping & Handling", quote["shipping"]),
                         ("Lead Time", quote["lead_time_note"])]:
        page.insert_text((MARGIN_L, y), f"{label}: {value}", fontsize=7.5, fontname=FONT, color=BLACK)
        y += 11

    y += 14
    page.insert_text((MARGIN_L, y), f"Sales: {quote['sales']}", fontsize=8, fontname=FONT)
    page.insert_text((MARGIN_L + 160, y), f"CS: {quote['cs']}", fontsize=8, fontname=FONT)
    page.insert_text((MARGIN_L + 300, y), f"Engineer: {quote['engineer']}", fontsize=8, fontname=FONT)
    y += 12
    page.insert_text((MARGIN_L, y), f"Plant: {quote['plant']}   #Cavities: {quote['cavities']}   "
                      f"Mold Number: {quote['mold_numbers']}", fontsize=8, fontname=FONT)
    y += 24
    page.insert_text((MARGIN_L, y), "Sincerely Yours,", fontsize=8, fontname=FONT)
    y += 12
    page.insert_text((MARGIN_L, y), "Morgan Vale (fictional signer - mock data)", fontsize=8, fontname=FONT_BOLD)
    y += 10
    page.insert_text((MARGIN_L, y), "Evco Plastics (MOCK DATA)", fontsize=8, fontname=FONT)

    draw_footer(page, quote, page_no_state[0], total_pages)
    page_no_state[0] += 1


# ---------------------------------------------------------------------------
# Top-level per-quote render
# ---------------------------------------------------------------------------

def estimate_total_pages(quote, cols):
    hdr_h = header_row_height(cols)
    cols_by_field = {c["field"]: c for c in cols}
    start_y = 190 if quote["template_style"] != "wide_master" else 190
    continuation_top = MARGIN_T + 60
    pages = 1
    y = start_y + hdr_h
    for row in quote["rows"]:
        rh = row_height(row, cols_by_field)
        if y + rh > TABLE_BOTTOM_LIMIT:
            pages += 1
            y = continuation_top + (hdr_h if quote["header_repeats_each_page"] else 0)
        y += rh
    pages += 1  # terms page
    if quote["resin_rows"]:
        pages += 1
    if quote["template_style"] == "wide_master":
        pages += 1  # dedicated front-matter page 2 before the table starts
    return pages


def render_quote_pdf(quote):
    cols = build_columns(quote)
    doc = fitz.open()
    page_no_state = [1]

    wide = quote["template_style"] == "wide_master"
    total_pages = estimate_total_pages(quote, cols)

    page1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    register_fonts(page1)
    y = draw_page_chrome(page1, quote, page_no_state[0], show_full_header=True)

    if wide:
        # Front matter continues onto a second page (matches the 58312-035
        # reference, which prints table notes on page 2 before Part Pricing
        # data begins on page 3), then the header row is drawn only once.
        draw_wrapped(page1, MARGIN_L, PAGE_W - MARGIN_L - MARGIN_R, y,
                     "Part Pricing - see table on the following page(s). U.S. Production. "
                     f"Part Lead Time: {quote['lead_time_note']}", size=7.5, line_h=10)
        draw_footer(page1, quote, page_no_state[0], total_pages)
        page_no_state[0] += 1

        page2 = doc.new_page(width=PAGE_W, height=PAGE_H)
        register_fonts(page2)
        y2 = draw_page_chrome(page2, quote, page_no_state[0], show_full_header=False)
        page2.insert_text((MARGIN_L, y2), "Notes on column 'B':", fontsize=8, fontname=FONT_BOLD)
        y2 += 12
        for line in quote["quote_comments"]:
            draw_wrapped(page2, MARGIN_L, PAGE_W - MARGIN_L - MARGIN_R, y2, line, size=7.5, line_h=10)
            y2 += 10 * max(1, math.ceil(len(line) / 100)) + 4
        draw_footer(page2, quote, page_no_state[0], total_pages)
        page_no_state[0] += 1

        table_first_page = doc.new_page(width=PAGE_W, height=PAGE_H)
        register_fonts(table_first_page)
        table_start_y = draw_page_chrome(table_first_page, quote, page_no_state[0], show_full_header=False)
        continuation_top = MARGIN_T + 60
        render_info, _ = render_table(doc, quote, cols, table_first_page, table_start_y,
                                       continuation_top, page_no_state, total_pages)
    else:
        continuation_top = MARGIN_T + 60
        render_info, _ = render_table(doc, quote, cols, page1, y, continuation_top, page_no_state, total_pages)

    apply_annotations(doc, quote, render_info)

    draw_resin_table(doc, quote, page_no_state, total_pages)
    draw_terms_page(doc, quote, page_no_state, total_pages)

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PDF_DIR / quote["file"]
    page_count = doc.page_count
    doc.save(str(out_path))
    doc.close()
    return out_path, page_count


# ---------------------------------------------------------------------------
# Manifests / coverage docs
# ---------------------------------------------------------------------------

def build_manifests(quotes, page_counts):
    quote_manifest = {"quotes": []}
    row_manifest = {"quotes": []}
    all_scenario_rows = []  # (pdf_file, row_no, scenario, is_quote_level_exception, notes)

    for q in quotes:
        pricing_row_count = len(q["rows"])
        scenario_counter = Counter()
        for r in q["rows"]:
            for s in r["scenarios"]:
                scenario_counter[s] += 1
            if r.get("annotation"):
                scenario_counter[r["annotation"]["type"].upper() + "_ANNOTATION_DRAWN"] += 1

        quote_manifest["quotes"].append({
            "file": q["file"],
            "quote_number": q["quote_number"],
            "opportunity_id": q["opportunity_id"],
            "type": q["type"],
            "customer_name": q["customer"]["name"],
            "issue_date": q["issue_date"],
            "price_effective_date": q["price_effective_date"] or None,
            "template_style": q["template_style"],
            "page_count": page_counts.get(q["file"]),
            "pricing_row_count": pricing_row_count,
            "resin_row_count": len(q["resin_rows"]),
            "quote_level_exception": (
                "EX-001" if q["type"].lower() != "active" else
                ("EX-002" if (not q["price_effective_date"] or q.get("missing_required_column")) else None)
            ),
            "missing_required_column": q.get("missing_required_column"),
            "scenario_row_counts": dict(scenario_counter),
        })

        rows_entry = {
            "file": q["file"], "quote_number": q["quote_number"],
            "customer_name": q["customer"]["name"], "pricing_row_count": pricing_row_count,
            "rows": [],
        }
        quote_excluded = q["type"].lower() != "active" or (not q["price_effective_date"]) or bool(q.get("missing_required_column"))
        exclusion_code = "EX-001" if q["type"].lower() != "active" else ("EX-002" if quote_excluded else None)
        for r in q["rows"]:
            expected = "EXCLUDED_QUOTE_LEVEL" if quote_excluded else _row_expected_result(r)
            entry = {
                "row": r["row"], "evco_pn": r["evco_pn"] or None, "bom": r["bom"] or None,
                "customer_pn": r["customer_pn"] or None, "scenario": r["scenarios"],
                "annotation": r["annotation"], "expected_result": expected,
                "quote_level_exception_code": exclusion_code if quote_excluded else None,
                "reason": r["notes"],
            }
            rows_entry["rows"].append(entry)
            all_scenario_rows.append((q["file"], r["row"], r["scenarios"], quote_excluded, r["notes"]))
        row_manifest["quotes"].append(rows_entry)

    return quote_manifest, row_manifest, all_scenario_rows


def _row_expected_result(row):
    ann = row.get("annotation")
    if ann and ann["type"] in ("strike_red", "strike_pink"):
        return "EXCLUDED_ROW_STRUCK_BY_ANNOTATION"
    if ann and ann["type"] == "override_box":
        return "VALID_WITH_PRICE_OVERRIDDEN"
    if any(s.startswith("BLANK_") for s in row["scenarios"]):
        return "VALID_ISOLATED_BLANK_FIELD"
    return "VALID_STRUCTURAL_INPUT"


CODE_VERIFIABLE_TAGS = {
    "STRUCK_ROW_RED_ANNOTATION", "STRUCK_ROW_PINK_ANNOTATION", "CELL_OVERRIDE_RED_BOX",
}
DEFERRED_AGENT_TAGS = {
    "MOQ_LOW_BOUNDARY", "MOQ_HIGH_VOLUME", "MOQ_EQUALS_BOX_QTY", "BOX_QTY_MOQ_NOT_DIVISIBLE",
    "AKA_QTY_BREAK_OF_ONE", "MULTI_TIER_PRICING", "SINGLE_TIER_PRICING",
}


def write_coverage_md(quotes, all_scenario_rows, path):
    lines = [
        "# Business Rule Coverage (row level)",
        "",
        "Generated by generate_mock_quotes.py from data/mock_quote_data.py - regenerate rather than hand-editing.",
        "",
        "**Verifiability key** (see business_rule_plan.md for the full explanation):",
        "- `code-verifiable` - asserted directly against the real project code in validate_mock_quotes.py "
        "(app/modules/extraction/parser.py's deterministic, non-LLM functions).",
        "- `agent-deferred` - the spec's own text marks this rule as evaluated by agent/LLM reasoning, not "
        "by code in this repository; the row is realistic input for it, but pass/fail cannot be asserted here.",
        "- `structural-analog` - approximates a PyMuPDF parsing edge case; the input is faithful to the "
        "documented trigger condition, but the exact internal failure mode isn't guaranteed to reproduce.",
        "",
        "| PDF | Row | EVCO PN | Scenario(s) | Verifiability | Expected Result |",
        "|---|---|---|---|---|---|",
    ]
    tag_totals = Counter()
    tag_pdfs = {}
    for q in quotes:
        excluded = q["type"].lower() != "active" or (not q["price_effective_date"]) or bool(q.get("missing_required_column"))
        for r in q["rows"]:
            verifiability = "structural-analog"
            if r.get("annotation"):
                verifiability = "code-verifiable"
            elif any(t in DEFERRED_AGENT_TAGS for t in r["scenarios"]) or any(
                t in ("BOM_RESIN_VARIANT", "BOM_COMBINED_PLUS", "BOM_SUFFIX_VARIANT",
                      "DECOY_QUANTITY_COLUMN_ROW", "VALID_BASELINE", "COMBINED_MULTI_RULE",
                      "SPECIAL_CHAR_PART_NUMBER", "LONG_DESCRIPTION_WRAP", "LEAD_TIME_EXTENDED",
                      "CARRY_FORWARD_MERGED_CELL") for t in r["scenarios"]
            ):
                verifiability = "structural-analog" if not any(
                    t in ("BLANK_CUSTOMER_PN", "BLANK_DESCRIPTION", "BLANK_MOQ_SINGLE_ROW", "BLANK_PRICE_SINGLE_ROW")
                    for t in r["scenarios"]
                ) else "code-verifiable"
            expected = "EXCLUDED_QUOTE_LEVEL" if excluded else _row_expected_result(r)
            lines.append(
                f"| {q['file']} | {r['row']} | {r['evco_pn'] or '-'} | {', '.join(r['scenarios'])} | "
                f"{verifiability} | {expected} |"
            )
            for t in r["scenarios"]:
                tag_totals[t] += 1
                tag_pdfs.setdefault(t, set()).add(q["file"])
            if r.get("annotation"):
                key = r["annotation"]["type"].upper() + "_ANNOTATION"
                tag_totals[key] += 1
                tag_pdfs.setdefault(key, set()).add(q["file"])

    lines += ["", "## Summary by scenario tag", "", "| Scenario Tag | Total Rows | PDFs |", "|---|---|---|"]
    for tag in sorted(tag_totals):
        lines.append(f"| {tag} | {tag_totals[tag]} | {len(tag_pdfs[tag])} |")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_rule_summary(quotes):
    """
    Per-quote and per-row summary of which official EVCO spec rule codes
    (BR-xxx/BRM-xxx/VR-xxx/EX-xxx) and PDF-annotation conventions (ANN-xxx)
    apply, using data/rule_catalog.py's scenario-tag mapping. See
    business_rule_plan.md for what "verifiability" means for each code.
    """
    summary = {
        "rule_reference": RULE_CATALOG,
        "quotes": [],
    }
    for q in quotes:
        q_rules = quote_level_rules(q)
        row_entries = []
        aggregate = []
        for r in q["rows"]:
            row_codes = rules_for_scenarios(r["scenarios"])
            for c in row_codes:
                if c not in aggregate:
                    aggregate.append(c)
            no_rule_reasons = {
                tag: SCENARIO_NO_RULE_REASON[tag]
                for tag in r["scenarios"]
                if tag in SCENARIO_NO_RULE_REASON
            }
            row_entries.append({
                "row": r["row"],
                "evco_pn": r["evco_pn"] or None,
                "scenarios": r["scenarios"],
                "applicable_rules": [
                    {"code": c, **{k: v for k, v in RULE_CATALOG[c].items()}}
                    for c in row_codes
                ],
                "scenarios_with_no_rule_code": no_rule_reasons or None,
            })
        summary["quotes"].append({
            "file": q["file"],
            "quote_number": q["quote_number"],
            "customer_name": q["customer"]["name"],
            "customer_number": q["customer"].get("customer_number"),
            "type": q["type"],
            "quote_level_rules": [
                {"code": c, **{k: v for k, v in RULE_CATALOG[c].items()}}
                for c in q_rules
            ],
            "aggregate_row_rule_codes": aggregate,
            "rows": row_entries,
        })
    return summary


def main():
    quotes = build_all_quotes()
    page_counts = {}
    for q in quotes:
        out_path, _ = render_quote_pdf(q)
        with fitz.open(str(out_path)) as reopened:
            page_counts[q["file"]] = reopened.page_count
        print(f"Wrote {out_path} ({page_counts[q['file']]} pages, {len(q['rows'])} pricing rows)")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "mock_quote_data.json").write_text(json.dumps(quotes, indent=2), encoding="utf-8")

    quote_manifest, row_manifest, all_scenario_rows = build_manifests(quotes, page_counts)
    (ROOT / "mock_quote_manifest.json").write_text(json.dumps(quote_manifest, indent=2), encoding="utf-8")
    (ROOT / "mock_quote_row_manifest.json").write_text(json.dumps(row_manifest, indent=2), encoding="utf-8")
    write_coverage_md(quotes, all_scenario_rows, ROOT / "business_rule_coverage.md")

    rule_summary = build_rule_summary(quotes)
    (ROOT / "rule_summary.json").write_text(json.dumps(rule_summary, indent=2), encoding="utf-8")

    print("\nWrote mock_quote_manifest.json, mock_quote_row_manifest.json, "
          "business_rule_coverage.md, rule_summary.json")


if __name__ == "__main__":
    main()
