"""
EVCO Price Quotation PDF Extraction — PyMuPDF-based single-stage extractor.

STRUCTURE:
  1. Backend config (replaces load_env / raw os.environ from standalone script)
  2. Your exact extraction code (paste your standalone script functions here)
  3. PDFExtractor wrapper (thin class that connects to the backend)
  4. Old code (commented out)

HOW TO UPDATE: Paste your updated standalone script functions between the
"EXTRACTION CODE START" and "EXTRACTION CODE END" markers. The module-level
`client` and `model_name` globals are already set up from backend settings,
so your code will use them automatically. Do NOT paste load_env(), the
client/model_name initialization, pdf_dir/output_dir, or main() — those
are standalone-only and are handled by the backend.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import fitz  # PyMuPDF
from openai import OpenAI
from pydantic import BaseModel, Field

from app.core.config import settings


# ---------------------------------------------------------------------------
# Pydantic schemas — all models for extraction live here.
# ---------------------------------------------------------------------------

class PricingTier(BaseModel):
    moq: Optional[str] = Field(default=None, description="Minimum Order Quantity (MOQ), as printed (e.g. '2,000')")
    price: Optional[str] = Field(default=None, description="Unit price, as printed (e.g. '$0.769')")


class PartInfo(BaseModel):
    line_number: Optional[str] = Field(default=None, description="Line number for the extracted row, as printed")
    mold_number: Optional[str] = Field(default=None, description="Mold number/identifier")
    evco_part_number: Optional[str] = Field(default=None, description="EVCO Part Number")
    manufacturing_bom_number: Optional[str] = Field(default=None, description="EVCO Manufacturing/BOM code")
    customer_part_number: Optional[str] = Field(default=None, description="Customer Part Number")
    part_description: Optional[str] = Field(default=None, description="Part description")
    box_quantity: Optional[str] = Field(default=None, description="Box Quantity, as printed")
    pricing_tiers: List[PricingTier] = Field(default_factory=list, description="List of pricing tiers (moq & price)")


class QuoteExtractionResponse(BaseModel):
    quote_number: Optional[str] = Field(default=None, description="The quote number (e.g. 58312 - 035)")
    type: Optional[str] = Field(default=None, description="Type of the quote (e.g. Active)")
    issue_date: Optional[str] = Field(default=None, description="Issue date of the quote")
    price_effective_date: Optional[str] = Field(default=None, description="Price effective date of the quote (e.g. 6/1/2026)")
    customer_name: Optional[str] = Field(default=None, description="Name of the customer company")
    address: Optional[str] = Field(default=None, description="Address of the customer")
    template_columns_present: Optional[Dict[str, bool]] = Field(
        default_factory=dict, description="Inventory of table headers present in the document template"
    )
    parts: List[PartInfo] = Field(default_factory=list, description="List of parts extracted from the quote tables")
    low_confidence_fields: List[str] = Field(
        default_factory=list, description="List of fields extracted with low confidence or illegibility"
    )
    is_exception: bool = Field(default=False, description="True if document contains an unhandled exception scenario")
    exception_reason: Optional[str] = Field(default=None, description="Description of the exception if is_exception is True")
    exception_codes: List[str] = Field(
        default_factory=list,
        description="Rule codes that triggered the exception (e.g. ['EX-001', 'EX-002']), per the EVCO Quote Validation Rules & Requirements spec's Exception Rules. Empty when is_exception is False.",
    )


class ExtractionRequest(BaseModel):
    file_name: str = Field(..., description="Name of the PDF file to process from folder_path")
    folder_path: str = Field(..., min_length=1, description="Absolute or relative folder containing the PDF to extract")
    scan_id: Optional[UUID] = Field(
        default=None,
        description="scan_id from a prior POST /monitoring/scan call, used to link this extraction back to its scan",
    )


class ProcessingResultRequest(BaseModel):
    processing_id: UUID = Field(..., description="processing_id returned by a prior POST /api/extract-quote call")


class CallbackExtractionStatusResponse(BaseModel):
    status: str = Field(default="accepted", description="The callback submission status.")
    message: str = Field(
        default="Quote extraction started. Result will be sent to the callback URL.",
        description="Status message for the callback-style request.",
    )
    callback_url: Optional[str] = Field(default=None, description="Callback URL used to receive the extraction result.")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend config — replaces load_env() and raw os.environ from standalone script.
# These module-level globals match what the standalone script expects.
# ---------------------------------------------------------------------------
client = OpenAI(
    api_key=settings.IBM_API_KEY,
    base_url=settings.IBM_BASE_URL,
    timeout=240.0,
    max_retries=2,
)
model_name = settings.MODEL_NAME

CHUNK_PAGE_SIZE = 1

# Token budgets per call stage. The old single-stage prompt asked one LLM
# call to read the page, decide which lines are real table rows, resolve
# merged/blank cells, group rows into pricing tiers, AND build the final
# nested JSON - a raw-row extraction (see build_raw_row_prompt below) needs
# far fewer output tokens than the old full-schema call, since it no longer
# nests pricing_tiers or repeats the document header per chunk.
# Sized from observed real responses: a 24-row dense page's raw-row JSON
# (one object per row, full column-label keys repeated per row) runs
# 6000-11000+ characters - 4000 tokens truncated it mid-response on a real
# test page. 10000 covers the densest real page in this project's own test
# corpus (max 29 data rows on one page, across all 6 dummy quotes - checked
# directly) with real margin, while staying meaningfully below the old
# single-stage prompt's 16000-token budget (which also had to fit a
# repeated document header AND fully-nested parts/pricing_tiers per call,
# neither of which this lightweight response includes at all).
RAW_ROW_MAX_TOKENS = 10000

# Step 9 asks for a dense table to be split into smaller batches rather than
# raising the token limit further. _split_table_markdown_into_batches below
# implements exactly that and is kept in place - but splitting a table
# mid-way was directly observed, in testing, to sometimes lose the row(s)
# nearest the split point (the model loses continuation context across the
# cut), which is a worse outcome than the truncation it prevents. Given the
# real max row density measured across this project's whole test corpus is
# 29 (see above) and RAW_ROW_MAX_TOKENS=10000 already covers that with
# margin, this threshold is set high enough that splitting never triggers
# for any file in this project today - the mechanism stays available for a
# future page dense enough to need it, rather than being deleted.
MAX_TABLE_ROWS_PER_CALL = 40
# The targeted ambiguous-row call (Step 8) answers 3 fixed questions about
# one row using minimal context - it never needs anywhere near this budget,
# but a naturally small ceiling is still safer than an unbounded default.
AMBIGUOUS_ROW_MAX_TOKENS = 800

logger.info(f"PDFExtractor using model: {model_name}")


# ===========================================================================
# ==================== EXTRACTION CODE START ================================
# ===========================================================================
# Paste your standalone extraction functions below this line.
# Everything between START and END markers is your exact code.
# ===========================================================================

# ---------------------------------------------------------------------------
# Color / annotation detection
#
# The Price Quotation PDFs are sometimes hand-annotated by EVCO staff using
# colored vector drawings on top of the original quote. Per the SOW these
# markups carry specific meaning and must be interpreted, not just extracted
# as literal text:
#
#   - A RED line drawn across a table row (usually paired with a small
#     red-outlined callout box) marks the ENTIRE ROW as ignored/not valid.
#   - A PINK line drawn across a table row marks the ENTIRE ROW as
#     ignored/not valid.
#   - Red text under "PRICE EFFECTIVE DATE" reading "Effective to <date>"
#     REPLACES the printed Price Effective Date.
#   - A small red-outlined box drawn on top of a table cell contains text
#     that REPLACES the value of the cell directly behind/under it.
#   - Larger red/pink-outlined callout boxes contain free-form notes that
#     are NOT part of the quote data and must be ignored entirely.
#   - Fill color on table cells carries no meaning and is ignored.
# ---------------------------------------------------------------------------

def _classify_color(rgb):
    """Return 'red', 'pink', or None for a PyMuPDF (r,g,b) 0..1 color tuple."""
    if rgb is None:
        return None
    r, g, b = rgb
    if r > 0.6 and g < 0.35 and b < 0.35:
        return "red"
    if r > 0.7 and g < 0.6 and b > 0.6:
        return "pink"
    return None


def _rects_overlap_ratio(a, b):
    """Fraction of rect `a`'s area covered by its intersection with rect `b`."""
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(a.get_area(), 1e-6)
    return inter / area_a


def get_page_annotations(page):
    """
    Inspect a page's vector drawings and classify markup relevant to the
    SOW's redline/annotation business rules.

    Returns a dict with:
      strike_rows:   list of fitz.Rect for red/pink lines drawn across a row
                      (thin, wide rectangles/lines).
      override_boxes: list of fitz.Rect for small red-outlined boxes that
                      replace the value of the cell underneath them.
      ignore_boxes:  list of fitz.Rect for larger red/pink-outlined callout
                      boxes whose text content is not part of the quote data.
    """
    strike_rows, override_boxes, ignore_boxes = [], [], []
    for d in page.get_drawings():
        stroke = _classify_color(d.get("color"))
        fill = _classify_color(d.get("fill"))
        rect = d.get("rect")
        if rect is None or rect.width <= 0:
            continue
        color = stroke or fill
        if color is None:
            continue

        w, h = rect.width, rect.height
        # Thin, wide horizontal bar spanning most of the row width -> strike line.
        if h <= 4 and w >= 150:
            strike_rows.append(rect)
        # Small cell-sized box with a colored outline -> value override.
        elif 6 <= h <= 30 and 6 <= w <= 60:
            override_boxes.append(rect)
        # Anything else colored red/pink and box-shaped -> free-form callout, ignore.
        elif w >= 60 and h >= 6:
            ignore_boxes.append(rect)
    return {
        "strike_rows": strike_rows,
        "override_boxes": override_boxes,
        "ignore_boxes": ignore_boxes,
    }


def _words_in_rect(page, rect, pad=1.0):
    words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,word_no
    r = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    hits = []
    for w in words:
        wx0, wy0, wx1, wy1 = w[:4]
        cx, cy = (wx0 + wx1) / 2, (wy0 + wy1) / 2
        if r.contains(fitz.Point(cx, cy)):
            hits.append(w)
    hits.sort(key=lambda w: (round(w[1], 1), w[0]))
    return " ".join(w[4] for w in hits).strip()


def build_annotated_page_text(page, annotations):
    """
    Rebuild page text line-by-line (preserving reading order), inserting
    explicit markers for rows struck out by red/pink lines and dropping text
    that falls inside free-form ignore callouts. Returns the annotated text
    plus a human-readable list of detected rule applications (for logging /
    prompt context).
    """
    notes = []
    strike_rows = annotations["strike_rows"]
    ignore_boxes = annotations["ignore_boxes"]

    out_lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            lbbox = fitz.Rect(line["bbox"])
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue

            # Drop text that falls inside a free-form callout / comment box.
            if any(_rects_overlap_ratio(lbbox, ib) > 0.5 for ib in ignore_boxes):
                notes.append(f"Ignored annotation callout text (not quote data): {text!r}")
                continue

            struck = False
            for sr in strike_rows:
                # Line must vertically fall within the strike line's row band
                # and the strike line must horizontally cover a meaningful
                # portion of the line for it to count as "drawn across" it.
                y_mid = (lbbox.y0 + lbbox.y1) / 2
                if sr.y0 - 3 <= y_mid <= sr.y1 + 3:
                    x_overlap = min(lbbox.x1, sr.x1) - max(lbbox.x0, sr.x0)
                    if x_overlap > 0.3 * min(lbbox.width, sr.width if sr.width > 0 else lbbox.width):
                        struck = True
                        break
            if struck:
                out_lines.append(f"[ROW MARKED FOR EXCLUSION - DO NOT EXTRACT AS A PART]: {text}")
                notes.append(f"Row struck by red/pink line, excluded: {text!r}")
            else:
                out_lines.append(text)

    return "\n".join(out_lines), notes


def get_cell_overrides(page, annotations):
    """
    For each small red-outlined override box, extract its replacement text
    and, when identifiable, the original text it visually covers (based on
    stacking/insertion order of overlapping text spans on the page).
    Returns a list of human-readable override descriptions for the prompt.
    """
    overrides = []
    if not annotations["override_boxes"]:
        return overrides

    spans = []
    for b_idx, block in enumerate(page.get_text("dict")["blocks"]):
        if block.get("type") != 0:
            continue
        for l_idx, line in enumerate(block["lines"]):
            for s_idx, span in enumerate(line["spans"]):
                text = span["text"].strip()
                if text:
                    spans.append((b_idx, l_idx, s_idx, fitz.Rect(span["bbox"]), text))

    for box in annotations["override_boxes"]:
        covering = [s for s in spans if _rects_overlap_ratio(s[3], box) > 0.6]
        if not covering:
            continue
        covering.sort(key=lambda s: (s[0], s[1], s[2]))
        new_text = covering[-1][4]
        old_text = covering[-2][4] if len(covering) > 1 and covering[-2][4] != new_text else None
        if old_text:
            overrides.append(f"Cell value {old_text!r} is visually replaced by {new_text!r} (red-outlined override box) - use {new_text!r}.")
        else:
            overrides.append(f"A red-outlined override box replaces a nearby cell value with {new_text!r} - use {new_text!r} for that cell.")
    return overrides


EFFECTIVE_TO_RE = re.compile(
    r"Effective\s*to\D{0,10}(\d{4})\s*([A-Za-z]+)\s*(\d{1,2})", re.IGNORECASE
)


def find_effective_date_override(full_text):
    """
    Detect the SOW's 'Effective to <year><month><day>' red-text annotation
    that supersedes the printed PRICE EFFECTIVE DATE, e.g.
    '2026January, SAM: Effective to 2026May31' -> 'May 31, 2026'.
    """
    m = EFFECTIVE_TO_RE.search(full_text)
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    return f"{month} {day}, {year}"


# ---------------------------------------------------------------------------
# Deterministic header field extraction (quote #, type, dates, customer)
# These fields are printed in a fixed, labeled format across every observed
# template, so regex extraction is more reliable than an LLM guess.
# ---------------------------------------------------------------------------

def extract_header_fields(full_text):
    fields = {}

    # "Quote" and "#" can have a space between them ("Quote #:" vs
    # "Quote#:"), and the separator between the two number groups isn't
    # always a plain ASCII hyphen - some PDFs render it with an en-dash or
    # an embedded-font glyph that PyMuPDF can't map to a real character at
    # all (comes through as U+FFFD). \W matches any of those non-word
    # separators without over-matching into the digits on either side.
    m = re.search(r"Quote\s*#:?\s*([\d]{5,6})\s*\W\s*(\d{3})", full_text)
    if m:
        fields["quote_number"] = f"{m.group(1)}-{m.group(2)}"

    m = re.search(r"Type:\s*([A-Za-z]+)", full_text)
    if m:
        fields["type"] = m.group(1).strip()

    m = re.search(r"Issue Date:\s*([0-9/\-]+)", full_text)
    if m:
        fields["issue_date"] = m.group(1).strip()

    m = re.search(r"PRICE EFFECTIVE DATE:\s*([^\n]+)", full_text, re.IGNORECASE)
    if m:
        fields["price_effective_date"] = m.group(1).strip()

    m = re.search(
        r"PURCHASEORDERS@EVCOPLASTICS\.COM\s*\n?\s*([^\n]+)", full_text
    )
    if m:
        candidate = m.group(1).strip()
        if candidate:
            fields["customer_name"] = candidate

    override_date = find_effective_date_override(full_text)
    if override_date:
        fields["price_effective_date"] = override_date

    return fields


# ---------------------------------------------------------------------------
# Image-only table detection -> exception handling
#
# Per the SOW, PDFs are expected to be digitally generated, text-based
# documents. When the Part Pricing table region cannot be identified as
# text (e.g. it was rendered/scanned as an image), the SOW instructs the
# quote be marked as an exception and not processed, rather than guessed at.
# ---------------------------------------------------------------------------

def has_image_only_table(doc):
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_h = page.rect.height
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 1:
                continue
            bbox = fitz.Rect(block["bbox"])
            # A large image occupying a substantial part of the page, below
            # the header area, is almost certainly a rasterized pricing table.
            if bbox.width > 300 and bbox.height > 100 and bbox.y0 > page_h * 0.15:
                if not page.find_tables().tables:
                    return True
    return False


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

schema_instruction = """
You are a precise data extraction system for EVCO Plastics Price Quotation PDFs.
You will receive the reconstructed text of a PDF (in reading order), a structured
table rendering (Markdown) extracted independently from the same pages, and a list
of detected color-annotation rules already applied to the text. Extract the
following fields exactly and return them as a single JSON object.

Every field below is a STRING. Do not use numbers, integers, floats, or nulls
anywhere in the output - even values that look numeric (quantities, prices,
line numbers) must be written as plain text strings exactly as they appear in
the source (e.g. "2,760" stays "2,760", a price stays "$10.31" or "10.31" as
printed, a missing value is an empty string "", not null). The goal is
faithful extraction of the data as printed, not numeric normalization.

Only extract a part from an actual row of the Part Pricing table (the table
that has columns like Mold/EVCO PN/Customer PN/Description/Box Qty/MOQ/Price -
cross-check against the supplied Markdown table rendering, which contains
ONLY real table rows). Do not create a part from any other text on the page,
even if it mentions the same words. In particular, ignore things like:
- Footer/signature blocks such as "Plant: MED  #Cavities: See Table  Mold
  Number: See Table" or "Sales: ... CS: ... Engineer: ...".
- Quote Comments, notes, resin/colorant/machine-class reference tables,
  credit terms, shipping & handling text, and terms & conditions boilerplate.
- Any sentence that merely uses a column-like word (Mold, MOQ, Price, Qty)
  in prose rather than as an actual table row with real values.
If a line of text is not a genuine row of the pricing table with real part
data in it, it must never appear in `parts`.

JSON Schema:
{
  "quote_number": "string (extract from 'Quote#:', remove spaces, keep format NNNNN-NNN)",
  "type": "string (extract from 'Type:')",
  "issue_date": "string (extract from 'Issue Date:')",
  "price_effective_date": "string (extract from 'PRICE EFFECTIVE DATE:', unless an 'Effective to' override date is noted, in which case use that override date instead)",
  "customer_name": "string (the company name being quoted, usually the line directly under the EVCO address/contact block)",
  "parts": [
    {
      "line_number": "string (or empty string if not specified, starting from 1 for each part in the table)",
      "mold_number": "string (extract from Mold column)",
      "evco_part_number": "string (extract ONLY from a column literally labeled EVCO PN / EVCO Part # / EVCO # - if no such column exists in this table, leave this empty string, even if EVCO MFG # is present)",
      "manufacturing_bom_number": "string (extract from EVCO MFG # / BOM # column if it exists, otherwise empty string. IMPORTANT: EVCO MFG # and EVCO PN are DIFFERENT columns that may both appear, or only one may appear. Never copy an EVCO MFG # value into evco_part_number just because it is the only EVCO identifier column present in this table - match strictly by header text.)",
      "customer_part_number": "string (extract from Customer PN / JD PN / RA Part # column if it exists, otherwise empty string)",
      "part_description": "string (extract from Part Description column)",
      "box_quantity": "string (extract from Parts/Box / Box Quantity / Box Qty column, or empty string)",
      "pricing_tiers": [
        {
          "moq": "string (extract from MOQ / MRQ / Min Order Qty / Monthly MOQ / Release Qty column)",
          "price": "string (extract from Price Each / New Price / Quote Price column, as printed, e.g. '$3.19' or '3.19'. If both a 'Previous Quote Price'/'Previous Price' column AND a current dated Quote Price column exist (e.g. '10/1/25 Quote Price'), always use the CURRENT one - the one whose date matches price_effective_date, or the rightmost one if undated. Never use a column literally prefixed 'Previous'.)"
        }
      ]
    }
  ]
}

Table structure and merged-cell rules (these templates use vertically merged
cells that repeat across several printed rows):
- Use the Markdown table to determine correct column-to-value alignment; use
  the reading-order text to resolve any ambiguity or wrapped text.
- Some templates print more than one quantity column (e.g. both "Run
  Quantity"/"Run Qty"/"Plant Run Qty" AND "MOQ"/"Min Release Qty"/"Minimum
  Order Quantity"/"Release Qty"/"Annual Purchase Qty"). These are NOT the
  same thing. Always take `moq` from the column literally labeled MOQ / MRQ /
  Min Order Qty / Min Release Qty / Monthly MOQ / Release Qty - never from a
  "Run Qty"/"Run Quantity"/"Plant Run Qty"/"Annual Purchase Qty" column, even
  if it sits closer to the price.
- If a table row has blank cells for mold/EVCO PN/EVCO MFG#/customer PN/part
  description/box quantity, carry forward ("fill down") the last non-blank
  value from the row(s) above for that column ONLY IF an earlier row in
  the same table actually had a non-blank value there (this is a visually
  merged cell continuing downward, not missing data). If there is no
  earlier row with a value for that column (e.g. this is the FIRST row of
  the table and its Mold cell is genuinely blank), the cell is simply
  empty - leave it as an empty string "".
- EXCEPTION to the fill-down rule above, for pages where NO Markdown table
  was detected at all (reading-order text only, no table grid in the
  original PDF): on these pages a bare tier line - just a quantity and a
  price, with no other row data - can appear ABOVE the full data row it
  actually belongs to, not below it. This happens because the extra
  price-tier line for a two-tier row is sometimes positioned, in the PDF's
  own reading order, closer to the END of the PRECEDING row than to the
  START of its own row. Do not assume a bare tier line always belongs to
  the nearest row above it in this case. Telltale sign: if attaching a bare
  tier line to the row above it would leave the very next full data row
  with ZERO pricing tiers (its own line ends with no quantity/price at
  all), the bare tier line almost certainly belongs to that NEXT row
  instead - reassign it down, not up. Every part row must end up with at
  least one pricing tier; a row left with an empty pricing_tiers list while
  an unclaimed bare tier line sits immediately above it in the text is a
  strong signal of this exact misattribution - fix it before finalizing.
- CRITICAL - never let a blank cell shift the row: a blank/empty cell
  must stay in its own column and be extracted as "". It must NEVER cause
  every value after it to slide left into the wrong field. Column
  position is determined by each cell's position between the pipe (|)
  delimiters in the Markdown table row, not by counting how many
  non-empty values the row happens to have. For example, if a row's Mold
  cell is blank and its cells read `| | 2548071 | N/A | 87413139 | ... |`,
  the correct mapping is mold_number="", evco_part_number="2548071",
  manufacturing_bom_number="N/A", customer_part_number="87413139" - NOT
  mold_number="2548071" with everything shifted left by one column.
- Two consecutive rows belong to the SAME part (merge into one `parts` entry
  with multiple `pricing_tiers`) ONLY when they share the same EVCO part
  number AND the same mold number AND the same box quantity - i.e. they are
  genuinely just quantity-break price tiers of one identical row, whether
  stacked within one cell, spread across physical rows, or split by shipping
  terms/plant.
- If the EVCO part number repeats but the mold number, box quantity, or a
  qualifier in the description differs (e.g. "3885-F" vs plain "3885 (Only)",
  a "(Service)" variant, a "Seeding" vs "Service" row, or any other visibly
  distinct row) - these are SEPARATE parts, each with their own entry and
  their own box_quantity, even though they share an EVCO part number. Do not
  collapse them into one part's pricing_tiers; that silently discards the
  distinguishing data.
- When genuinely uncertain whether two rows are tiers of one part or two
  distinct parts, prefer keeping them as separate parts - losing a
  duplicate-looking row is far less harmful than silently merging away a
  real distinction.
- Some multi-page price lists print the column header ONLY ONCE, on an
  earlier page, and never repeat it on later pages - later pages are pure
  data rows in the exact same column order. If a "Master table header"
  block is supplied below, it is that document's true column order and
  applies to every data row you are given, even on pages that show no
  header at all. Never guess column meaning from position alone when the
  master header is available - use it.
- A page break can orphan the table's very last row onto its own page by
  itself, with nothing else around it - this is common for a long
  multi-page price list. Because that row has no other rows near it, the
  PDF's own table-grid detector may fail to recognize it as a table, so
  it may appear ONLY in the reading-order text below, with no
  accompanying entry in the Structured Table Rendering section (a
  per-page note will flag this explicitly when it happens). Do NOT treat
  the absence of a Markdown table entry as proof a line isn't real data:
  if the master table header is supplied and a reading-order line's
  token count and trailing price ($X.XX) match that header's column
  pattern, extract it as a genuine row using the master header's column
  order - the same as any other data row.
- Do not confuse a plant/site/location/cavity-count column (small codes or
  low integers, e.g. an "EVCO SITE" or "Plant" column) with an actual
  quantity column. Only columns literally labeled with a quantity term
  (MOQ, Min Order Qty, Box Qty, Parts/Box, etc., per the master header)
  feed box_quantity or moq.
- When two quantity values sit adjacent to each other with no clear
  separator in the reading-order text (e.g. a Min Order Qty number
  immediately followed by a Box Qty number, such as "900 100"), do NOT
  guess by proximity or by which one "looks more familiar" - resolve each
  number's identity from its own cell position in that row of the
  Markdown table, matched left-to-right against the master header's
  column order. The Markdown table's per-row cell layout is the source of
  truth for which adjacent number belongs to which column.
- Wide "master price list" style tables often print several dollar-amount
  columns per row that are cost-buildup components (e.g. base cost,
  packaging cost, material price, component baseline/price, price
  adjustments) leading up to a final column. Only the LAST such column -
  the current/most-recent total selling price, typically named like
  "<Month Year> Part Pricing", "Quote Price", "New Price", or "Price
  Each" - is the `price` to extract. Never use an intermediate cost or
  adjustment column as the price.

Color-annotation / redline rules (already partially applied to the supplied
text, but apply your own judgment as well):
- Lines prefixed with "[ROW MARKED FOR EXCLUSION - DO NOT EXTRACT AS A PART]"
  were struck out with a red or pink line in the original PDF. Do NOT create
  a part or pricing tier from these lines under any circumstances.
- If the detected-annotations list reports a cell value override (a
  red-outlined box drawn over a table cell with replacement text), use the
  replacement value instead of the original value for that specific field.
- If the detected-annotations list reports an "Effective to" date override,
  use that date as price_effective_date instead of the printed
  "PRICE EFFECTIVE DATE" value.
- Ignore any free-form comment/callout text noted as an ignored annotation -
  it is not quote data.
- Cell background/fill colors carry no meaning and must be ignored.

Important details:
- Return ONLY a valid JSON object matching the schema. No explanations, no markdown formatting blocks.
- If no parts table can be reliably identified at all, return "parts": [].
"""


def build_prompt(annotated_text, table_markdown, detected_notes, master_header=None):
    parts = [schema_instruction]
    if master_header:
        parts.append("\n--- Master table header (true column order for ALL data rows in this document, even pages with no header of their own) ---\n")
        parts.append(master_header)
    parts.append("\n--- Reconstructed PDF Text (reading order) ---\n")
    parts.append(annotated_text)
    if table_markdown:
        parts.append("\n--- Structured Table Rendering (Markdown, independently extracted) ---\n")
        parts.append(table_markdown)
    if detected_notes:
        parts.append("\n--- Detected Color-Annotation Rule Applications ---\n")
        parts.append("\n".join(f"- {n}" for n in detected_notes))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Lightweight raw-row extraction prompt (performance refactor)
#
# schema_instruction/build_prompt above are UNCHANGED and still define the
# full single-call contract; they are kept for reference and as a fallback
# call shape (call_llm's signature is unchanged besides the new optional
# max_tokens param) but are no longer what extract_pdf_data sends per chunk.
#
# The old prompt made every LLM call responsible for: identifying real table
# rows, resolving blank/merged cells (fill-down), grouping rows into pricing
# tiers, AND constructing the final nested parts/pricing_tiers/header JSON -
# all in one shot, once per CHUNK_PAGE_SIZE-page chunk. That is a lot of
# reasoning crammed into one large prompt/response pair.
#
# This lightweight prompt asks the LLM for ONLY what genuinely requires
# visual/positional judgment it has and Python does not (which lines are
# real pricing-table rows vs. footer/comment/resin-table noise, and which
# raw value belongs in which master-header column, honoring the existing
# blank-cell/merged-cell/multi-price-column rules already documented in
# schema_instruction above - those rules are copied here verbatim where they
# affect column IDENTIFICATION, since that judgment call still has to happen
# somewhere and Python cannot see the page's visual layout). It explicitly
# does NOT ask the model to fill down blank cells, group rows into pricing
# tiers, or build the final nested schema - _normalize_raw_rows() below does
# all of that deterministically in Python once the raw per-row values are in
# hand, which is exactly the "reduce what the LLM has to do" goal.
# ---------------------------------------------------------------------------

raw_row_instruction = """
You are extracting RAW pricing-table rows from one small section of an EVCO
Price Quotation PDF. This is a narrower task than a full extraction - you are
NOT building the final answer, only reporting what is physically printed in
each table row, one row at a time.

Use the supplied master table header to determine which column each value
belongs to - it is the true column order for this document, even on pages
that show no header of their own.

Extract ONLY genuine rows of the Part Pricing table (the table with columns
like Mold/EVCO PN/Customer PN/Description/Box Qty/MOQ/Price - cross-check
against the supplied Markdown table rendering, which contains ONLY real
table rows). Do NOT extract:
- comments, footer/signature blocks, terms & conditions, shipping/credit text
- resin/material/colorant reference tables
- any sentence that merely uses a column-like word (Mold, MOQ, Price, Qty)
  in prose rather than as an actual table row with real values

Column-identification rules (apply these, since only you can see the row's
original visual/table layout - Python cannot):
- Preserve every cell exactly as printed, as a STRING. Do not normalize,
  reformat, or coerce numbers - "$3.19" stays "$3.19", "2,760" stays "2,760".
- Preserve blank cells: if a cell is genuinely blank in this row, return an
  empty string "" for that column. NEVER shift later values left to fill a
  blank cell's slot - column position comes from the cell's position in the
  Markdown table between its pipe (|) delimiters, never from how many
  non-empty values the row happens to have.
- Do NOT fill down a blank cell from a previous row yourself - report this
  row's own printed value only (or "" if genuinely blank here). Fill-down is
  handled afterward, not by you.
- Some templates print more than one quantity column (e.g. "Run Qty" AND
  "MOQ"). Only place a value under "MOQ"/"MRQ"/"Min Order Qty"/"Release Qty"
  if it is the column literally labeled that way in the master header - never
  a "Run Qty"/"Run Quantity"/"Annual Purchase Qty" column, even if it sits
  closer to the price.
- Wide "master price list" tables often print several dollar-amount columns
  per row (cost-buildup components) leading up to a final selling price.
  Only the LAST such column - the current/most-recent total selling price
  (e.g. "<Month Year> Part Pricing", "Quote Price", "New Price", "Price
  Each") - is the "Price" value. Never report an intermediate
  cost/build-up/adjustment column as "Price". Never use a column literally
  prefixed "Previous".
- Do NOT report a plant/site/location/cavity-count column (small codes or
  low integers) as a quantity column.
- Do NOT group rows into pricing tiers and do NOT decide which rows belong
  to the same part - report each physical row exactly as printed, one row
  per JSON object in "rows". Row-grouping happens afterward, not by you.
- If you are genuinely uncertain which column a value belongs to, or
  whether a line is a real data row at all, still include it and set
  "ambiguous": true with a short "ambiguous_reason" - do not silently guess
  or silently drop it.

Color-annotation / redline rules (already partially applied to the supplied
text):
- Lines prefixed with "[ROW MARKED FOR EXCLUSION - DO NOT EXTRACT AS A PART]"
  were struck out with a red or pink line in the original PDF. Do NOT report
  a row from these lines under any circumstances.
- If the detected-annotations list reports a cell value override (a
  red-outlined box drawn over a table cell with replacement text), use the
  replacement value instead of the original value for that specific cell.
- Ignore any free-form comment/callout text noted as an ignored annotation.
- Cell background/fill colors carry no meaning and must be ignored.

Return ONLY a valid JSON object of this exact shape, matching column names to
the master header's own column labels (use the header's exact text as each
key, e.g. if the header says "EVCO MFG (BOM)" use that as the key, not a
paraphrase):
{
  "rows": [
    {
      "cells": { "<master header column label>": "<printed value or \\"\\">", ... },
      "ambiguous": false,
      "ambiguous_reason": ""
    }
  ]
}

If no genuine pricing-table rows are found in this section, return {"rows": []}.
Return ONLY the JSON object - no explanations, no markdown formatting blocks.
"""


def build_raw_row_prompt(annotated_text, table_markdown, detected_notes, master_header=None):
    parts = [raw_row_instruction]
    if master_header:
        parts.append("\n--- Master table header (true column order - use these exact labels as JSON keys) ---\n")
        parts.append(master_header)
    parts.append("\n--- Reconstructed PDF Text (reading order) ---\n")
    parts.append(annotated_text)
    if table_markdown:
        parts.append("\n--- Structured Table Rendering (Markdown, independently extracted) ---\n")
        parts.append(table_markdown)
    if detected_notes:
        parts.append("\n--- Detected Color-Annotation Rule Applications ---\n")
        parts.append("\n".join(f"- {n}" for n in detected_notes))
    return "\n".join(parts)


_AMBIGUOUS_ROW_PROMPT_TEMPLATE = """
You are resolving ONE ambiguous pricing-table row from an EVCO Price
Quotation PDF. Answer only the 3 questions below - do not re-extract the
whole document.

Master table header (true column order):
{master_header}

Previous row (already resolved): {prev_row}
Current row (ambiguous - reason given): {current_row}
Reason flagged ambiguous: {ambiguous_reason}
Next row (already resolved, may be empty if this is the last row): {next_row}
Relevant annotation note, if any: {relevant_note}

Answer exactly these 3 questions:
1. Is this current row a NEW part (not a pricing tier of the previous row)?
2. Is this current row a pricing tier of the SAME part as the previous row
   (same EVCO part number, same mold number, same box quantity)?
3. Which master-header column does each of this row's printed values belong to?

Return ONLY a valid JSON object of this exact shape:
{{
  "is_new_part": true,
  "is_pricing_tier_of_previous": false,
  "cells": {{ "<master header column label>": "<printed value or \\"\\">", ... }}
}}
Return ONLY the JSON object - no explanations, no markdown formatting blocks.
"""


def build_ambiguous_row_prompt(master_header, prev_row, current_row, ambiguous_reason, next_row, relevant_note):
    return _AMBIGUOUS_ROW_PROMPT_TEMPLATE.format(
        master_header=master_header or "(none detected)",
        prev_row=json.dumps(prev_row) if prev_row else "(none - this is the first row)",
        current_row=json.dumps(current_row),
        ambiguous_reason=ambiguous_reason or "(unspecified)",
        next_row=json.dumps(next_row) if next_row else "(none - this is the last row)",
        relevant_note=relevant_note or "(none)",
    )


_PLACEHOLDER_HEADER_CELL_RE = re.compile(r"^Col\d+$")
_GARBLED_HEADER_PLACEHOLDER_RATIO = 0.15


def _table_header_is_garbled(markdown_table: str) -> bool:
    """
    PyMuPDF names a column 'ColN' when a header cell's text wraps across
    multiple physical lines (e.g. a narrow "EVCO PN" column prints "EVCO" on
    one line and "PN" on the next) and its table-grid detector mistakes the
    wrapped header fragments for extra data rows instead of merging them
    back into one header cell. The column's own data values still come
    through correctly on every real row - only its header label is lost -
    so trusting this table's header (for column-presence checks, or as
    "ground truth" shown to the LLM) would incorrectly suggest a real,
    populated column is missing.

    A header only counts as "garbled" once a meaningful FRACTION of its
    cells are placeholders, not just any single one: a wide, dense table
    (e.g. 18 columns) can have one unrelated trailing "ColN" - a stray
    parsing artifact from something at the table's edge - while every
    required column (Mold, EVCO PN, Customer PN, etc.) is still cleanly
    labeled. Withholding a whole otherwise-good table over one irrelevant
    column throws away real, correctly-aligned data and forces the LLM
    onto messier raw reading-order text instead, which - on a large table -
    tends to produce WORSE extractions than just keeping the table (this
    was observed directly: an 18-column table with only "Col18" garbled
    still had "RA Part #" perfectly labeled, but withholding it caused the
    LLM to lose track of that column in the reading-order text). Several
    placeholder cells clustered together, in contrast, is the real failure
    mode this guards against - wrapped header text torn out of the row
    entirely, corrupting the columns that data actually depends on.
    """
    header_line = markdown_table.split("\n", 1)[0]
    cells = [c.strip() for c in header_line.split("|") if c.strip()]
    if not cells:
        return False
    placeholder_count = sum(1 for c in cells if _PLACEHOLDER_HEADER_CELL_RE.match(c))
    return placeholder_count > 0 and (placeholder_count / len(cells)) >= _GARBLED_HEADER_PLACEHOLDER_RATIO


def _degroup_repeated_markdown_columns(markdown_table: str) -> Optional[str]:
    """
    Repair a different PyMuPDF corruption pattern from the one
    _table_header_is_garbled guards against: instead of a wrapped header
    cell's text being lost entirely into phantom rows, the whole table
    gets split into a fixed-size repeating group per real column (seen
    directly: a 3-line-tall header row turned an 8-column table into 24
    columns, with every real value tripled in place). The real header
    label ends up sitting among ColN placeholders within its group, and
    every data row's group holds N identical copies of that column's one
    true value - discarding this table as "too garbled" would throw away
    data that's still perfectly, correctly aligned; it just needs its
    groups collapsed back to one cell each.

    This is detected, not assumed: for a candidate group size (any exact
    divisor of the column count, checked from 2 to 4), the header's every
    group must contain at most one non-placeholder cell, AND every data
    row's every group must hold cells that are all identical. Only when
    both hold for the whole table is it collapsed - a table that doesn't
    match this exact repeating-group shape is left untouched (returns
    None) rather than risk mangling a real, differently-corrupted table.

    One further wrinkle, also observed directly: a row whose real FIRST
    column is genuinely blank can come back short by a whole group
    instead of holding an empty group - e.g. 21 cells instead of 24, with
    the group for the blank leading column dropped entirely rather than
    represented as ["", "", ""]. Such a row is left-padded back to the
    full width with blank groups before the checks above run, since a
    blank leading cell (never a blank trailing one - every real row must
    end in a price) is the far more plausible place for cells to have
    been dropped outright.
    """
    lines = [ln for ln in markdown_table.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None

    rows = []
    for line in lines:
        stripped = line.strip().strip("|")
        if stripped and set(stripped.replace("|", "").replace(" ", "")) <= set("-:"):
            continue  # markdown separator row (---|---|...)
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    if len(rows) < 2:
        return None

    header = rows[0]
    total_cols = len(header)
    if total_cols < 6:
        return None

    for group_size in range(2, 5):
        if total_cols % group_size != 0:
            continue
        col_count = total_cols // group_size
        if col_count < 3:
            continue

        padded_rows = []
        rows_ok = True
        for row in rows:
            deficit = total_cols - len(row)
            if deficit == 0:
                padded_rows.append(row)
            elif deficit > 0 and deficit % group_size == 0:
                padded_rows.append([""] * deficit + row)
            else:
                rows_ok = False
                break
        if not rows_ok:
            continue
        def group_of(row, g, size=group_size):
            return row[g * size:(g + 1) * size]

        padded_header = padded_rows[0]
        header_ok = all(
            len({c for c in group_of(padded_header, g) if not _PLACEHOLDER_HEADER_CELL_RE.match(c)}) <= 1
            for g in range(col_count)
        )
        if not header_ok:
            continue

        data_ok = all(
            len(set(group_of(row, g))) <= 1
            for row in padded_rows[1:]
            for g in range(col_count)
        )
        if not data_ok:
            continue

        new_rows = []
        for row in padded_rows:
            new_row = []
            for g in range(col_count):
                group = group_of(row, g)
                non_placeholder = [c for c in group if not _PLACEHOLDER_HEADER_CELL_RE.match(c)]
                new_row.append(non_placeholder[0] if non_placeholder else group[0])
            new_rows.append(new_row)

        out_lines = ["|" + "|".join(new_rows[0]) + "|", "|" + "|".join(["---"] * col_count) + "|"]
        out_lines.extend("|" + "|".join(row) + "|" for row in new_rows[1:])
        return "\n".join(out_lines)

    return None


def _count_matched_required_columns(header_line: str) -> int:
    """How many distinct required Part Pricing columns this header row's
    cells can identify via the configured aliases. ColN placeholder cells
    simply match nothing (contributing 0), rather than disqualifying the
    header outright - see find_master_table_header() for why this matters."""
    cells_norm = [_normalize_header_cell(c) for c in header_line.split("|") if c.strip()]
    count = 0
    for alias_setting in REQUIRED_COLUMN_ALIASES.values():
        aliases = settings.parse_aliases(alias_setting)
        if aliases and any(_normalize_header_cell(a) in cell for a in aliases for cell in cells_norm):
            count += 1
    return count


def _spreadsheet_column_labels(n: int) -> List[str]:
    """['A', 'B', ... 'Z', 'AA', 'AB', ...] - the first `n` spreadsheet
    column labels, in order."""
    labels = []
    for i in range(n):
        s, x = "", i
        while True:
            s = chr(ord("A") + x % 26) + s
            x = x // 26 - 1
            if x < 0:
                break
        labels.append(s)
    return labels


def _reconstruct_header_from_column_letters(doc, max_pages_to_scan=8) -> Optional[str]:
    """
    Several EVCO 'master price list' templates print a spreadsheet-style
    column-letter guide row (A, B, C, ... Z, AA, AB ...) directly above the
    real column-label row - and those same templates tend to be the widest
    ones (25+ columns, header labels wrapped across 2-3 lines), which is
    exactly the shape that defeats PyMuPDF's table-grid parser and leaves
    the header full of meaningless 'ColN' placeholders (see
    _table_header_is_garbled / find_master_table_header). A ColN-riddled
    master header makes the LLM unable to map real, populated columns (e.g.
    a clearly-printed "EVCO #" column ends up hidden between "Col17" and
    "Col19"), which then surfaces downstream as a bogus "required column
    blank on most parts" partial-extraction failure.

    When the letter guide row IS present it nails down the true column
    count and each column's exact x-position, so the real header can be
    rebuilt straight from text positions instead of the broken table
    parse: every label line just below the guide row is snapped to its
    nearest letter-column x-anchor, and lines that snapped to the same
    column (one label that wrapped onto several physical lines) are
    stitched back together in visual order. Returns a single Markdown
    header line, or None when no usable letter guide row is found (in
    which case the caller falls back to the table-parse header as before).
    """
    for page_idx in range(min(len(doc), max_pages_to_scan)):
        page = doc[page_idx]
        lines = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                text = "".join(s["text"] for s in ln["spans"]).strip()
                if not text:
                    continue
                x0, y0 = ln["bbox"][0], ln["bbox"][1]
                lines.append((y0, x0, text))
        if not lines:
            continue

        # Find a row (lines sharing a y band) that is a run of consecutive
        # spreadsheet column letters starting at 'A' - the guide row.
        by_row: Dict[int, list] = {}
        for y0, x0, text in lines:
            by_row.setdefault(round(y0 / 3.0), []).append((x0, y0, text))

        anchors = None
        letters_bottom_y = None
        for _, row in sorted(by_row.items()):
            letter_cells = sorted(
                (c for c in row if re.fullmatch(r"[A-Z]{1,2}", c[2])),
                key=lambda c: c[0],
            )
            if len(letter_cells) < 6:
                continue
            if [c[2] for c in letter_cells] != _spreadsheet_column_labels(len(letter_cells)):
                continue
            anchors = [c[0] for c in letter_cells]
            letters_bottom_y = max(c[1] for c in letter_cells)
            break

        if not anchors or letters_bottom_y is None:
            continue

        # Collect label lines just below the guide row, stopping at the
        # first sizable vertical gap (that gap is the start of the data rows).
        label_lines = []
        prev_y = letters_bottom_y
        for y0, x0, text in sorted(lines, key=lambda c: c[0]):
            if y0 <= letters_bottom_y + 1:
                continue
            if y0 - prev_y > 25:
                break
            label_lines.append((y0, x0, text))
            prev_y = y0
        if not label_lines:
            continue

        columns: list = [[] for _ in anchors]
        for y0, x0, text in label_lines:  # label_lines is already y-sorted
            idx = min(range(len(anchors)), key=lambda i: abs(anchors[i] - x0))
            columns[idx].append((x0, text))

        cells = []
        for frags in columns:
            # Order a column's wrapped label fragments left-to-right; a
            # stable sort keeps top-to-bottom (y) order for fragments that
            # share an x. These templates typeset wrapped header lines with
            # non-monotonic y values, so x is the more reliable key.
            frags.sort(key=lambda f: round(f[0]))
            cells.append(" ".join(t for _, t in frags).strip())
        if sum(1 for c in cells if c) < len(cells) * 0.6:
            continue
        return "|" + "|".join(cells) + "|"

    return None


def find_master_table_header(doc, max_pages_to_scan=8):
    """
    Some multi-page price lists print their column header row only once,
    on an early page, and never repeat it. Find that header (as a Markdown
    header line) so it can be supplied to every chunk, including chunks
    whose pages never show a header of their own.

    A document can contain several tables on its early pages - the actual
    Part Pricing table, but also a materials/resin reference list, a terms
    table, etc. Picking the first header that merely LOOKS clean (no ColN
    placeholders) is a trap: a short reference table can pass that bar
    while the real Part Pricing table - wider, and more likely to have a
    wrapped header cell or two - gets skipped for being "garbled", handing
    back a header from entirely the wrong table (observed directly: a
    7-column material/resin reference table with generic column names like
    "Evco PN"/"MOQ"/"Price" was picked over the actual, partially garbled
    17-column Part Pricing table on an earlier page). Scoring every
    candidate by how many required columns its cells actually identify -
    tolerating placeholder cells rather than being disqualified by them -
    and keeping the best-scoring one picks the real table even when it's
    imperfectly parsed, since it still matches far more required columns
    than an unrelated reference table ever will.
    """
    best_header = None
    best_score = 0
    for page_idx in range(min(len(doc), max_pages_to_scan)):
        page = doc[page_idx]
        for t in page.find_tables().tables:
            try:
                md = t.to_markdown()
            except Exception:
                continue
            if not md:
                continue
            md = _degroup_repeated_markdown_columns(md) or md
            header_line = md.split("\n", 1)[0]
            if _header_has_placeholder_cells(header_line):
                repaired = _fill_garbled_header_from_layout(page, t)
                if repaired:
                    header_line = "|" + "|".join(repaired) + "|"
            score = _count_matched_required_columns(header_line)
            if score > best_score:
                best_score, best_header = score, header_line

    # When a spreadsheet column-letter guide row is present, a header
    # rebuilt from raw text positions is more trustworthy than the
    # table-grid parse (which, on these wide templates, tends to be the
    # ColN-riddled one). Use it only when it identifies at least as many
    # required columns as the best table-parse header did - so a
    # reconstruction that accidentally drops a column can never replace a
    # more complete table header.
    reconstructed = _reconstruct_header_from_column_letters(doc, max_pages_to_scan)
    if reconstructed:
        recon_score = _count_matched_required_columns(reconstructed)
        if recon_score >= best_score and recon_score >= 3:
            return reconstructed

    return best_header if best_score >= 3 else None


def call_llm(prompt, retries=2, max_tokens=16000):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
            )
            if not response.choices:
                raise RuntimeError("IBM API returned no choices (likely a transient backend error or an oversized prompt).")
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                raise RuntimeError("IBM API returned an empty response.")
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "length":
                # The response was cut off before the model finished writing
                # the JSON - this is a hard truncation, not a transient blip,
                # and json.loads() below would just fail with a confusing
                # parse error. Fail loudly with the real cause instead so
                # callers/retries/warnings reflect what actually happened.
                raise RuntimeError(
                    f"IBM API truncated the response at the max_tokens limit "
                    f"(finish_reason=length, {len(content)} chars returned). "
                    f"The table on this page is too large/dense for the "
                    f"current max_tokens budget."
                )
            return json.loads(content)
        except Exception as e:
            last_error = e
            if attempt < retries:
                delay = 3 * (2 ** attempt)
                print(f"    LLM call failed ({e}); retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise last_error
    raise last_error  # unreachable - the loop above always returns or raises


_BARE_TIER_LINE_RE = re.compile(r"^[\d,]+\s+\$\s?[\d,]+\.\d{1,2}$")
_FULL_ROW_START_RE = re.compile(r"^\d.*\|")


def _reorder_orphaned_tier_lines(text: str) -> str:
    """
    On pages with no detected table grid, a multi-tier part's earlier price
    tier(s) can appear, in the PDF's own reading order, ABOVE the full data
    row they belong to instead of below it - an artifact of how some quote
    templates vertically merge a part's identity cells (Mold/EVCO PN/
    Description) across its tier rows, anchoring that merged text near the
    bottom of the merged block instead of the top. A "bare" line - just a
    quantity and a price, nothing else - is reassigned to trail the next
    full data row rather than staying attached to whatever line precedes it,
    so it lands next to the row it actually belongs to.
    """
    lines = text.split("\n")
    result: list[str] = []
    pending_bare: list[str] = []
    for line in lines:
        if _BARE_TIER_LINE_RE.match(line.strip()):
            pending_bare.append(line)
            continue
        if pending_bare and _FULL_ROW_START_RE.match(line.strip()):
            result.append(line)
            result.extend(pending_bare)
            pending_bare = []
            continue
        if pending_bare:
            result.extend(pending_bare)
            pending_bare = []
        result.append(line)
    if pending_bare:
        result.extend(pending_bare)
    return "\n".join(result)


_MD_PRICE_CELL_RE = re.compile(r"^[*_]{0,2}\$?\s?[\d,]+\.\d+[*_]{0,2}$")


def _fill_garbled_header_from_layout(page, table) -> Optional[List[str]]:
    """
    A ColN-garbled table whose DATA rows are still cleanly column-aligned:
    PyMuPDF kept the row grid but lost some header labels to placeholders
    (a header cell whose text wraps or sits slightly outside the cell box
    it computed). Rebuild only the missing labels from the header region's
    reading-order text, snapped to the table's own column x-anchors, and
    trust the labels PyMuPDF did resolve. Returns one name per column, or
    None when a gap can't be filled confidently.
    """
    try:
        names = list(table.header.names)
        anchors = [c[0] for c in table.header.cells if c]
        y0h, y1h = table.header.bbox[1], table.header.bbox[3]
    except Exception:
        return None
    if not names or len(anchors) != len(names):
        return None

    empty_idx = {i for i, n in enumerate(names) if not (n or "").strip()}
    if not empty_idx:
        return [(n or "").replace("\n", " ").strip() for n in names]

    frags: Dict[int, list] = {i: [] for i in empty_idx}
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block["lines"]:
            text = "".join(s["text"] for s in ln["spans"]).strip()
            if not text:
                continue
            x0, y0 = ln["bbox"][0], ln["bbox"][1]
            # Stay strictly inside the header band. PyMuPDF's header.bbox can
            # run taller than the actual labels, so a loose lower bound would
            # vacuum up the first data row's text.
            if y0 < y0h - 6 or y0 > y1h + 2:
                continue
            idx = min(range(len(anchors)), key=lambda i: abs(anchors[i] - x0))
            if idx in empty_idx:
                frags[idx].append((y0, x0, text))

    out = []
    for i, n in enumerate(names):
        if i in empty_idx:
            parts = sorted(frags[i], key=lambda f: (round(f[0]), f[1]))
            out.append(" ".join(p[2] for p in parts).strip())
        else:
            out.append((n or "").replace("\n", " ").strip())
    if any(not c for c in out):
        return None
    return out


def _regrid_garbled_markdown(md: str, header_names: List[str]) -> Optional[str]:
    """
    Re-emit a ColN-garbled to_markdown() table with a clean reconstructed
    header, keeping only its genuine data rows (right cell count, last cell
    is a price - which drops the leftover bold/duplicated header-fragment
    rows PyMuPDF interleaves). Returns None when the data rows don't look
    consistent enough to trust the row grid.
    """
    data = []
    for line in md.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and not "".join(cells).strip("-: "):
            continue  # markdown separator row
        if len(cells) != len(header_names) or not cells:
            continue
        if not _MD_PRICE_CELL_RE.match(cells[-1].replace(" ", "")):
            continue
        data.append(cells)
    if len(data) < 2:
        return None
    lines = [
        "| " + " | ".join(header_names) + " |",
        "|" + "|".join(["---"] * len(header_names)) + "|",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in data)
    return "\n".join(lines)


def _collect_page_data(page):
    """Extract plain text, annotated text, table markdown, and notes for one page."""
    plain_text = page.get_text("text")
    annotations = get_page_annotations(page)
    annotated_text, notes = build_annotated_page_text(page, annotations)
    notes = notes + get_cell_overrides(page, annotations)
    markdowns = []
    had_garbled_table = False
    for t in page.find_tables().tables:
        try:
            md = t.to_markdown()
            if not md:
                continue
            md = _degroup_repeated_markdown_columns(md) or md
            if _table_header_is_garbled(md):
                # The table grid is intact but some header labels were lost
                # to ColN placeholders. If the data rows are still cleanly
                # aligned and the missing labels can be rebuilt from the
                # header region's text (snapped to the table's own column
                # x-anchors), keep the table with that repaired header -
                # withholding it entirely forces the LLM onto reading-order
                # text where a value that wrapped across lines (e.g. an
                # "Evco BOM" like "6682/9475227-\nCHIMEI") gets truncated.
                repaired_names = _fill_garbled_header_from_layout(page, t)
                regridded = _regrid_garbled_markdown(md, repaired_names) if repaired_names else None
                if regridded:
                    markdowns.append(regridded)
                    continue
                had_garbled_table = True
                continue
            markdowns.append(md)
        except Exception:
            pass

    if had_garbled_table:
        # This page's table grid was detected, but its header text wraps
        # across multiple physical lines and confused the table parser -
        # some header cells came back as meaningless "ColN" placeholders
        # instead of their real label (see _table_header_is_garbled). The
        # data ROWS in that markdown are still correctly column-aligned,
        # but showing the LLM a header row with fabricated placeholder
        # names would make it wrongly conclude a real, populated column is
        # missing (exactly the EX-002 failure this guards against) - so
        # that table's markdown is withheld entirely, forcing the model to
        # read column identity from the reading-order text below instead,
        # where the same header words appear correctly (if wrapped).
        notes.append(
            "This page's Part Pricing table header could not be reliably "
            "parsed into column names by the PDF's table-grid detector "
            "(its header text wraps across multiple lines, e.g. 'EVCO' "
            "and 'PN' printed on separate lines for one 'EVCO PN' "
            "column) - its Structured Table Rendering has been withheld "
            "for this reason. Determine each column's identity from the "
            "reading-order text's header block above the first data row "
            "instead, reassembling consecutive short header-fragment "
            "lines in left-to-right column order; do NOT conclude a "
            "required column is missing just because no Structured Table "
            "Rendering entry names it here."
        )

    if not markdowns and plain_text.strip():
        annotated_text = _reorder_orphaned_tier_lines(annotated_text)
        # No (usable) table grid was detected on this page at all. This is
        # the exact shape of the "orphaned last row" failure mode: a page
        # break can leave the table's final row sitting alone on its own
        # page, with no other rows nearby for the PDF's table-grid
        # detector to recognize as a table. Flag it explicitly so the
        # model doesn't silently drop this page's data just because it
        # has no accompanying Markdown table entry - see the schema
        # instruction's "orphaned last row" rule for how to handle it.
        notes.append(
            "No usable table grid was detected by the PDF parser on this "
            "page - if the reading-order text above contains a line "
            "matching the master table header's column pattern (see the "
            "'orphaned last row' rule), extract it as a genuine part row "
            "using the master header's column order; do not drop it just "
            "because this page has no Structured Table Rendering entry of "
            "its own."
        )

    return plain_text, annotated_text, markdowns, notes


# ---------------------------------------------------------------------------
# Centralized exception/rule code registry
#
# Single source of truth for every code that can appear in exception_codes.
# All exception-producing code paths reference these constants instead of
# hardcoding string literals, so a typo becomes a NameError at import time
# instead of a silently wrong/inconsistent code in the output.
# ---------------------------------------------------------------------------

class ExceptionCode:
    # From the EVCO Quote Validation Rules & Requirements spec's Exception
    # Rules section only - no Business Requirements (BRM) codes included.
    EX_001 = "EX-001"    # Exception Rules - quote is not active
    EX_002 = "EX-002"    # Exception Rules - required quote information is missing (header or column)
   # EX_003 = "EX-003"    # Exception Rules - required column header information is missing (merged into EX-002)

    # Extractor-internal codes - not part of the SOW's rule numbering, used
    # for failure modes the spec doesn't assign a business rule code to.
    IMAGE_ONLY_TABLE = "IMAGE_ONLY_TABLE"    # Part Pricing table is a rendered image, not text
    EXTRACTION_FAILED = "EXTRACTION_FAILED"  # All pages failed LLM extraction after retries


EXCEPTION_CODE_DESCRIPTIONS = {
    ExceptionCode.EX_001: (
        "Quote is not active - must not be used to update AKA pricing or "
        "Sales Orders (Exception Rules)"
    ),
    ExceptionCode.EX_002: (
        "Required quote information is missing: quote number, quote type, "
        "effective date, customer name, or a required column header (Mold, "
        "EVCO part number, manufacturing/BOM number, customer part number, "
        "Description, quantity, MOQ, or price) - stops automatic "
        "processing (Exception Rules)"
    ),
    ExceptionCode.IMAGE_ONLY_TABLE: "Part Pricing table is image-based, not machine-readable text (extractor-internal, not an SOW rule code)",
    ExceptionCode.EXTRACTION_FAILED: "All pages failed LLM extraction after retries (extractor-internal, not an SOW rule code)",
}


# ---------------------------------------------------------------------------
# Required-field validation
#
# Per the EVCO Quote Validation Rules & Requirements spec's Exception
# Rules section - no BRM codes, only EX codes are emitted:
#   EX-001  - quote is not active; must not be used to update AKA pricing
#             or Sales Orders.
#   EX-002  - any required quote information is missing, including both
#             the header (quote number, quote type, effective date,
#             customer name) and required column headers (Mold, EVCO
#             part number, manufacturing/BOM number, customer part
#             number, Description, quantity, MOQ, or price - all
#             independently required, not an either/or between EVCO Part
#             No. and Manufacturing/BOM No.). There is no separate EX-003
#             code - column-header failures are reported as EX-002 too.
# If any of these fail, the entire quote must be excluded from automated
# processing and routed for manual review - not partially processed.
# ---------------------------------------------------------------------------

REQUIRED_HEADER_FIELDS = {
    "quote_number": "Quote Number",
    "type": "Type",
    "price_effective_date": "Price Effective Date",
    "customer_name": "Customer Name",
}

REQUIRED_LINE_FIELDS = {
    "mold_number": "Mold Number",
    "evco_part_number": "EVCO Part Number",
    "manufacturing_bom_number": "EVCO Manufacturing/BOM Number",
    "customer_part_number": "Customer Part Number",
    "part_description": "Part Description",
    "box_quantity": "Box Quantity (Parts/Box)",
}

# Column-name aliases (config.py) used to check for each required column's
# presence directly in a detected table header row - no LLM call needed.
REQUIRED_COLUMN_ALIASES = {
    "mold_number": settings.EXTRACTION_ALIAS_MOLD,
    "evco_part_number": settings.EXTRACTION_ALIAS_EVCO_PN,
    "manufacturing_bom_number": settings.EXTRACTION_ALIAS_EVCO_MFG,
    "customer_part_number": settings.EXTRACTION_ALIAS_CUSTOMER_PN,
    "part_description": settings.EXTRACTION_ALIAS_PART_DESC,
    "box_quantity": settings.EXTRACTION_ALIAS_BOX_QTY,
    "moq": settings.EXTRACTION_ALIAS_MOQ,
    "price": settings.EXTRACTION_ALIAS_PRICING,
}

REQUIRED_COLUMN_LABELS = {**REQUIRED_LINE_FIELDS, "moq": "MOQ", "price": "Price"}


def _has_required_value(value: Any) -> bool:
    """Return True when a required extracted value is actually present."""
    if value is None:
        return False
    return bool(str(value).strip())


def _normalize_header_cell(text: str) -> str:
    """
    Collapse a markdown table header cell down to a whitespace-free,
    lowercase canonical form. A header cell that wraps onto multiple
    physical lines but was still correctly kept as ONE cell (e.g.
    "EVCO<br>Part #" or "Parts/<br>Box") comes back from to_markdown() with
    a literal "<br>" joining the wrapped fragments - contrast with
    _table_header_is_garbled(), where the wrap defeats the parser entirely
    and the cell is lost to a "ColN" placeholder. Here the real text is
    present, but naively turning "<br>" into a single space is unreliable:
    whether the true wrap point had a space (e.g. "EVCO Part #") or not
    (e.g. "Parts/Box") varies per document, so a fixed choice breaks one
    case or the other. Stripping ALL whitespace instead sidesteps the
    guess entirely - both sides of a comparison collapse to the same form
    regardless of where the PDF happened to wrap.
    """
    return re.sub(r"\s+", "", text.replace("<br>", " ")).lower()


def _master_header_missing_columns(master_header: str) -> List[Tuple[str, str]]:
    """
    Compare a detected Markdown table header line's cell text against the
    configured column-name aliases. Returns (field, label) for every
    required column whose aliases cannot be found in any header cell.
    """
    cells_norm = [_normalize_header_cell(c) for c in master_header.split("|") if c.strip()]
    missing = []
    for field, alias_setting in REQUIRED_COLUMN_ALIASES.items():
        aliases = settings.parse_aliases(alias_setting)
        if not aliases:
            continue
        if not any(_normalize_header_cell(alias) in cell for alias in aliases for cell in cells_norm):
            missing.append((field, REQUIRED_COLUMN_LABELS[field]))
    return missing


def _header_has_placeholder_cells(header_line: str) -> bool:
    """True if any cell in this header row is a PyMuPDF 'ColN' placeholder."""
    cells = [c.strip() for c in header_line.split("|") if c.strip()]
    return any(_PLACEHOLDER_HEADER_CELL_RE.match(c) for c in cells)


def validate_before_extraction(header_fields: Dict[str, Any], master_header: Optional[str]):
    """
    Fail-fast pass run BEFORE the LLM table-data extraction. Both checks
    here are fully deterministic - no LLM call involved - so a document
    that's going to fail EX-001/EX-002 anyway never pays for a full
    parts/pricing-tier extraction:
      - EX-001 / EX-002 header fields: quote_number/type/price_effective_date/
        customer_name are already regex-extracted by extract_header_fields()
        before this runs.
      - EX-002 required column headers: only checked when a table grid WAS
        detected (master_header is not None) - the absence of a detected
        header doesn't prove a column is missing, so that case is left to
        the existing post-extraction validate_required_fields() instead of
        risking a false fail-fast.
    Returns the same {"codes": [...], "message": ...} failure list shape as
    validate_required_fields().
    """
    failures = []

    def fail(code, message):
        failures.append({"codes": [code], "message": f"{code}: {message}"})

    quote_type = str(header_fields.get("type") or "").strip()
    if quote_type and quote_type.lower() != "active":
        fail(
            ExceptionCode.EX_001,
            f"quote Type is '{quote_type}', not Active - inactive quotes "
            "must not be used to update AKA pricing or Sales Orders.",
        )

    missing_header = [
        label
        for field, label in REQUIRED_HEADER_FIELDS.items()
        if not _has_required_value(header_fields.get(field))
    ]
    if missing_header:
        fail(
            ExceptionCode.EX_002,
            f"missing required quote header field(s): {', '.join(missing_header)}.",
        )

    if master_header:
        missing_columns = _master_header_missing_columns(master_header)
        if missing_columns:
            # MOQ/Price are never trusted from the header check alone, even
            # with a perfectly clean-looking header (no placeholder cells
            # at all): observed directly on a real document whose table
            # parsed with 9 fully-labeled columns and simply had no Price
            # column at all - a silent, traceless loss distinct from every
            # other corruption pattern here (no ColN to catch it on). A
            # required Part Pricing table missing MOQ or Price entirely
            # has never once turned out to be a genuine business case
            # across this whole corpus - unlike BOM, it's essentially
            # always a parsing artifact - so both are always deferred to
            # the post-extraction check instead of being asserted here.
            missing_columns = [
                (field, label) for field, label in missing_columns
                if field not in ("moq", "price")
            ]
            if missing_columns and _header_has_placeholder_cells(master_header):
                # A placeholder cell means the parser lost track of some
                # column's real label - a "missing" claim about most fields
                # is then unreliable (the column could be hiding behind
                # that very placeholder; see find_master_table_header) and
                # is deferred to the post-extraction check instead, which
                # judges by the LLM's actual extracted data rather than a
                # header that's already known to be unreliable here.
                #
                # manufacturing_bom_number is exempted from that deferral,
                # but ONLY when it's the single field this check flagged: a
                # genuinely absent BOM/MFG# column is by far the most common
                # reason this fires even alongside unrelated corruption
                # elsewhere in the header (e.g. one stray trailing column),
                # and confidently catching that case for free is worth
                # keeping (observed directly avoiding a token-truncation-
                # prone full extraction on a large multi-page document). But
                # when OTHER required fields are ALSO missing from the same
                # header, that's a sign the corruption sits right where
                # BOM's own column would be too, not somewhere unrelated -
                # trusting it then produced a real false positive (a real
                # "Evco BOM" column whose label was torn away into the very
                # placeholder cell sitting next to it). So BOM only bypasses
                # the defer when nothing else on the header is also unread.
                if len(missing_columns) == 1 and missing_columns[0][0] == "manufacturing_bom_number":
                    pass  # keep the sole BOM claim, trust it
                else:
                    missing_columns = []
            if missing_columns:
                labels = [label for _, label in missing_columns]
                fail(
                    ExceptionCode.EX_002,
                    f"required column(s) missing from this document's Part "
                    f"Pricing table header: {', '.join(labels)}.",
                )

    return failures


def validate_required_fields(result):
    """
    Validate all required quote information.

    EX-001:
        Quote is not active - must not be used to update AKA pricing or
        Sales Orders.

    EX-002:
        Any required quote information is missing, including:
        - Required quote header (quote number, quote type, effective
          date, or customer name)
        - Required column header (Mold, EVCO part number,
          manufacturing/BOM number, customer part number, Description,
          quantity, MOQ, or price)

    If any validation fails, the entire quote is excluded from
    automated processing and routed for manual review.
    """

    # Client callbacks may wrap the quote in ``extracted_data``
    # and rename ``type`` to ``quote_type``.
    extracted_data = result.get("extracted_data")
    if isinstance(extracted_data, dict):
        result = extracted_data

    failures = []

    def fail(code, message):
        failures.append({
            "codes": [code],
            "message": f"{code}: {message}",
        })

    # ---------------------------------------------------------------
    # EX-001 - quote must be Active
    # ---------------------------------------------------------------
    quote_type = str(
        result.get("type") or result.get("quote_type") or ""
    ).strip()

    if quote_type and quote_type.lower() != "active":
        fail(
            ExceptionCode.EX_001,
            f"quote Type is '{quote_type}', not Active - inactive quotes "
            "must not be used to update AKA pricing or Sales Orders.",
        )

    # ---------------------------------------------------------------
    # EX-002 - required quote header fields
    # ---------------------------------------------------------------
    missing_header = []

    for field, label in REQUIRED_HEADER_FIELDS.items():
        value = result.get(field)

        if field == "type" and not _has_required_value(value):
            value = result.get("quote_type")

        if not _has_required_value(value):
            missing_header.append(label)

    if missing_header:
        fail(
            ExceptionCode.EX_002,
            f"missing required quote header field(s): "
            f"{', '.join(missing_header)}.",
        )

    # ---------------------------------------------------------------
    # EX-002 - required column header information
    # ---------------------------------------------------------------
    parts = result.get("parts") or []

    if not parts:
        fail(
            ExceptionCode.EX_002,
            "no parts could be extracted from the Part Pricing table.",
        )
        return failures

    # Required line-level fields. A field failing for EVERY part usually
    # means the column genuinely doesn't exist in this template - but a
    # field blank for most (not all) parts is a different, worse signal:
    # the column exists (some rows have it) and extraction is silently
    # dropping it on most rows - e.g. a large multi-chunk document where
    # only some chunks came back with the field populated. That case
    # produced no exception at all before (only "all blank" was checked),
    # letting a document with 73% of its parts missing evco_part_number
    # through as a silent "success" - so any field blank on a MAJORITY of
    # parts is now flagged too, not just a field blank on all of them.
    for field, label in REQUIRED_LINE_FIELDS.items():
        blank_count = sum(1 for p in parts if not _has_required_value(p.get(field)))
        if blank_count == len(parts):
            fail(
                ExceptionCode.EX_002,
                f"required column '{label}' is missing from this "
                f"document's Part Pricing table.",
            )
        elif blank_count > len(parts) / 2:
            fail(
                ExceptionCode.EX_002,
                f"required column '{label}' is blank on {blank_count} of "
                f"{len(parts)} parts in this document's Part Pricing "
                f"table - likely a partial extraction failure, not a "
                f"genuinely absent column.",
            )

    # ---------------------------------------------------------------
    # EX-002 - MOQ and Price
    # ---------------------------------------------------------------
    all_tiers = [
        tier
        for part in parts
        for tier in (part.get("pricing_tiers") or [])
    ]

    if not all_tiers:
        fail(
            ExceptionCode.EX_002,
            "no MOQ/pricing data could be extracted from the "
            "Part Pricing table.",
        )
    else:
        if all(not _has_required_value(t.get("moq")) for t in all_tiers):
            fail(
                ExceptionCode.EX_002,
                "required column 'MOQ' is missing from this "
                "document's Part Pricing table.",
            )

        if all(not _has_required_value(t.get("price")) for t in all_tiers):
            fail(
                ExceptionCode.EX_002,
                "required column 'Price' is missing from this "
                "document's Part Pricing table.",
            )

    return failures


# ---------------------------------------------------------------------------
# Deterministic raw-row normalization (performance refactor)
#
# Takes the flat, ungrouped rows returned by the lightweight raw-row LLM
# call (build_raw_row_prompt) and applies, in pure Python, the same
# fill-down and pricing-tier-grouping rules the old single-stage prompt used
# to ask the LLM to reason about on every call. Neither rule requires visual
# judgment once the per-row raw values are already known - both are simple,
# deterministic comparisons - so moving them here removes that reasoning
# burden from the LLM without changing what the rules actually do.
# ---------------------------------------------------------------------------

def _master_header_column_labels(master_header: Optional[str]) -> List[str]:
    """Pipe-delimited master header line -> ordered list of column label
    strings, exactly as printed (used only to validate/order raw-row keys;
    the canonical field mapping below is alias-based, not position-based,
    since the LLM already resolved position when it read the row)."""
    if not master_header:
        return []
    return [c.strip() for c in master_header.split("|") if c.strip()]


def _canonical_field_for_label(label: str) -> Optional[str]:
    """Map one raw column label (as the LLM echoed it back, e.g. 'EVCO MFG
    (BOM)') to one of this project's canonical part/tier field names, using
    the exact same alias lists (REQUIRED_COLUMN_ALIASES/settings.EXTRACTION_
    ALIAS_*) the rest of this file already uses for header validation - no
    new alias configuration is introduced."""
    normalized_label = _normalize_header_cell(label)
    if not normalized_label:
        return None
    for field, alias_setting in REQUIRED_COLUMN_ALIASES.items():
        for alias in settings.parse_aliases(alias_setting):
            if _normalize_header_cell(alias) in normalized_label:
                return field
    return None


_PART_LEVEL_FIELDS = (
    "mold_number", "evco_part_number", "manufacturing_bom_number",
    "customer_part_number", "part_description", "box_quantity",
)
_TIER_LEVEL_FIELDS = ("moq", "price")


def _split_stacked_tiers(moq_value: str, price_value: str) -> List[Dict[str, str]]:
    """
    Some templates print more than one quantity-break tier stacked inside a
    SINGLE printed table row's MOQ/Price cells (e.g. one row whose MOQ cell
    reads "150\\n300" and whose Price cell reads "$21.99\\n$18.91" - two
    tiers of the same physical row, not two separate rows). The old
    single-stage prompt built the final nested JSON directly, so it split
    these itself; the lightweight raw-row prompt intentionally reports a
    row's cells exactly as printed (see raw_row_instruction) without doing
    that grouping/splitting judgment call, so it has to happen here instead.

    Splits on embedded newlines. If the MOQ and Price cell don't split into
    the SAME number of lines, splitting would risk pairing the wrong price
    with the wrong quantity - safer to keep the raw, unsplit value as a
    single tier than to fabricate a possibly-wrong pairing.
    """
    moq_lines = [ln.strip() for ln in (moq_value or "").split("\n")]
    price_lines = [ln.strip() for ln in (price_value or "").split("\n")]
    if len(moq_lines) <= 1 and len(price_lines) <= 1:
        return [{"moq": moq_value, "price": price_value}]
    if len(moq_lines) != len(price_lines):
        return [{"moq": moq_value, "price": price_value}]
    return [{"moq": m, "price": p} for m, p in zip(moq_lines, price_lines)]


_MARKDOWN_SEPARATOR_ROW_RE = re.compile(r"^\|[\s:\-|]+\|$")


def _split_markdown_table_by_row_count(markdown_block: str, max_rows: int) -> List[str]:
    """
    Step 9 ("split again instead of increasing the token limit"): a single
    detected table can have far more data rows than fit comfortably in one
    raw-row LLM call. PyMuPDF's to_markdown() output is always
    header-row / separator-row ("|---|---|...") / data-rows - split the
    DATA rows into groups of at most max_rows, re-attaching the SAME header
    and separator line to every group so each sub-call still has full
    column-identity context (master_header is also supplied separately, but
    this keeps each group independently readable too).

    Returns [markdown_block] unchanged if it doesn't match the expected
    header/separator/data shape (e.g. a non-standard table), or if it
    already has max_rows or fewer data rows - never risk mangling a table
    this function doesn't confidently recognize.
    """
    lines = markdown_block.split("\n")
    if len(lines) < 3 or not _MARKDOWN_SEPARATOR_ROW_RE.match(lines[1].strip()):
        return [markdown_block]
    header_line, separator_line = lines[0], lines[1]
    data_lines = [ln for ln in lines[2:] if ln.strip()]
    if len(data_lines) <= max_rows:
        return [markdown_block]
    groups = []
    for i in range(0, len(data_lines), max_rows):
        group_lines = data_lines[i:i + max_rows]
        groups.append("\n".join([header_line, separator_line] + group_lines))
    return groups


def _split_table_markdown_into_batches(table_markdown: str, max_rows: int) -> List[str]:
    """Apply _split_markdown_table_by_row_count to each independently
    detected table block within one page's combined table_markdown (blocks
    are joined by a blank line - see run_chunk/_collect_page_data), then
    flatten. A page with no oversized table returns [table_markdown]
    unchanged (the common case - most pages need no splitting at all)."""
    if not table_markdown.strip():
        return [table_markdown]
    blocks = table_markdown.split("\n\n")
    batches: List[str] = []
    any_split = False
    for block in blocks:
        if not block.strip():
            continue
        sub_blocks = _split_markdown_table_by_row_count(block, max_rows)
        if len(sub_blocks) > 1:
            any_split = True
        batches.extend(sub_blocks)
    return batches if any_split else [table_markdown]


def _map_raw_row_cells(cells: Dict[str, Any]) -> Dict[str, str]:
    """Map one raw row's {column_label: value} dict to {canonical_field:
    value}, for every label this project recognizes. A label that matches no
    known alias is simply not part-of/tier-of anything this system tracks
    (e.g. a decoy/annual-volume column some templates print) and is dropped,
    same as the old prompt implicitly did by only ever emitting known keys."""
    mapped: Dict[str, str] = {}
    if not isinstance(cells, dict):
        return mapped
    for label, value in cells.items():
        field = _canonical_field_for_label(label)
        if field is None:
            continue
        # If the same canonical field is matched by more than one column in
        # this row (rare, but possible with a decoy column whose label
        # loosely resembles a real one), keep the first non-blank match
        # rather than letting a later, wrong match silently overwrite it.
        if field in mapped and _has_required_value(mapped[field]):
            continue
        mapped[field] = "" if value is None else str(value)
    return mapped


def _resolve_ambiguous_rows_sequentially(
    raw_rows: List[dict], master_header: Optional[str], notes: List[str], stats: Optional[dict] = None,
) -> List[dict]:
    """
    Step 8: walk raw_rows in order and replace every LLM-flagged-ambiguous
    row with a resolved one, via a small targeted call containing only the
    already-resolved previous row, the ambiguous row, the (raw) next row,
    and any relevant annotation note - never the whole chunk/document.

    Runs BEFORE _fill_down_and_group_rows, and sequentially (not in
    parallel), because each resolution needs the PREVIOUS row's already-
    resolved value as context, and its own resolved output becomes the
    "previous row" context for whichever row comes after it.

    A resolved ambiguous row is folded back into the exact same
    {"cells": {...}} shape normal rows use, plus an internal-only
    "_is_pricing_tier_of_previous" hint consumed by the grouping pass right
    after this function returns - so downstream code has one uniform row
    shape to work with regardless of whether a row started ambiguous.
    """
    resolved: List[dict] = []
    relevant_note = notes[0] if notes else None
    for i, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, dict):
            continue
        if not raw_row.get("ambiguous"):
            resolved.append(raw_row)
            continue

        prev_row = resolved[-1] if resolved else None
        next_row = raw_rows[i + 1] if i + 1 < len(raw_rows) else None
        prompt = build_ambiguous_row_prompt(
            master_header, prev_row, raw_row, raw_row.get("ambiguous_reason"), next_row, relevant_note,
        )
        try:
            started = time.perf_counter()
            answer = call_llm(prompt, retries=1, max_tokens=AMBIGUOUS_ROW_MAX_TOKENS)
            elapsed = time.perf_counter() - started
            if stats is not None:
                stats["ambiguous_calls"] = stats.get("ambiguous_calls", 0) + 1
                stats["llm_time_s"] = stats.get("llm_time_s", 0.0) + elapsed
            resolved.append({
                "cells": answer.get("cells") or {},
                "_is_pricing_tier_of_previous": bool(answer.get("is_pricing_tier_of_previous")),
            })
        except Exception as e:
            # Last resort: keep the row's own best-guess cells rather than
            # dropping it entirely - losing a row silently is worse than
            # carrying forward its original (still possibly correct) values.
            print(f"    Ambiguous-row resolution failed ({e}); keeping original cell values for this row.")
            resolved.append({"cells": raw_row.get("cells") or {}})
    return resolved


def _fill_down_and_group_rows(raw_rows: List[dict]) -> List[dict]:
    """
    Pure-Python replacement for the reasoning the old prompt asked the LLM
    to do on every call:
      - fill-down: a blank cell carries forward the last non-blank value
        seen earlier in this same chunk for that column, UNLESS this is the
        first row (matches schema_instruction's existing fill-down rule).
      - pricing-tier grouping: two consecutive rows belong to the same part
        only when mold_number AND evco_part_number AND box_quantity all
        match (matches schema_instruction's existing grouping rule) -
        otherwise each row starts a new part.
    Expects raw_rows to already have ambiguity resolved (see
    _resolve_ambiguous_rows_sequentially) - this function assumes every row
    is a normal, mappable {"cells": {...}} dict. Returns parts in the exact
    shape the rest of extract_pdf_data already expects.
    """
    parts: List[dict] = []
    last_values: Dict[str, str] = {}
    current_part: Optional[dict] = None
    current_key: Optional[Tuple[str, str, str, str]] = None

    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue

        mapped = _map_raw_row_cells(raw_row.get("cells") or {})

        # Fill-down: only for part-level identity columns, only when THIS
        # row's own value is blank and an earlier row already had one -
        # exactly the existing rule, never inventing a value for the very
        # first row of the table.
        for field in _PART_LEVEL_FIELDS:
            value = mapped.get(field, "")
            if not _has_required_value(value) and _has_required_value(last_values.get(field)):
                mapped[field] = last_values[field]
            if _has_required_value(mapped.get(field)):
                last_values[field] = mapped[field]

        row_tiers = _split_stacked_tiers(mapped.get("moq", ""), mapped.get("price", ""))

        row_key = (
            mapped.get("evco_part_number", ""),
            mapped.get("mold_number", ""),
            mapped.get("box_quantity", ""),
            mapped.get("manufacturing_bom_number", ""),
        )
        # Two rows are the SAME part only when part number AND mold AND box
        # quantity all match - identical to the existing grouping rule.
        # manufacturing_bom_number is included in the key too: two resin-
        # variant rows of the same physical part (e.g. mfg# ending
        # "-CHIMEI" vs "-RAVAGO") share identical EVCO part number, mold,
        # AND box quantity but are genuinely SEPARATE parts per
        # schema_instruction's own fuller rule ("if ... a qualifier in the
        # description differs ... these are SEPARATE parts") - mfg#/BOM is
        # the reliable, already-present field that carries that distinction,
        # so it must be part of the merge key or two real, distinct parts
        # would silently collapse into one part's pricing_tiers.
        # A fully-empty key never merges with anything (avoids accidentally
        # collapsing multiple genuinely-blank-keyed rows into one part).
        is_same_part = (
            current_part is not None
            and row_key == current_key
            and any(_has_required_value(v) for v in row_key)
        )
        if is_same_part:
            assert current_part is not None, "is_same_part already required current_part is not None"
            current_part["pricing_tiers"].extend(row_tiers)
        else:
            if current_part is not None:
                parts.append(current_part)
            current_part = {
                "line_number": "",
                "mold_number": mapped.get("mold_number", ""),
                "evco_part_number": mapped.get("evco_part_number", ""),
                "manufacturing_bom_number": mapped.get("manufacturing_bom_number", ""),
                "customer_part_number": mapped.get("customer_part_number", ""),
                "part_description": mapped.get("part_description", ""),
                "box_quantity": mapped.get("box_quantity", ""),
                "pricing_tiers": list(row_tiers),
            }
            current_key = row_key

    if current_part is not None:
        parts.append(current_part)

    # Line numbers are assigned once grouping is final, matching the old
    # prompt's own "starting from 1 for each part in the table" behavior,
    # per-chunk (the outer chunk loop already concatenates chunks in page
    # order, same as before).
    for idx, part in enumerate(parts, start=1):
        part["line_number"] = str(idx)

    return parts


def _dedupe_adjacent_parts(parts: list) -> list:
    """
    Drop a part if it's an exact duplicate of the one immediately before it
    in the combined list. This targets a specific artifact of chunked
    extraction (CHUNK_PAGE_SIZE pages per LLM call): a table row that sits
    right at a chunk boundary can get included by both the chunk that ends
    there and the chunk that starts there, producing back-to-back identical
    entries once every chunk's parts are concatenated. Only ADJACENT,
    fully-identical entries are removed - two genuinely distinct parts that
    happen to be identical but are NOT next to each other are left alone,
    since collapsing non-adjacent repeats could silently drop a real,
    intentionally-duplicated row instead of a boundary artifact.
    """
    def _key(part):
        if not isinstance(part, dict):
            return None
        tiers = part.get("pricing_tiers") or []
        tiers_key = tuple((t.get("moq"), t.get("price")) for t in tiers if isinstance(t, dict))
        return (
            part.get("mold_number"),
            part.get("evco_part_number"),
            part.get("manufacturing_bom_number"),
            part.get("customer_part_number"),
            part.get("part_description"),
            part.get("box_quantity"),
            tiers_key,
        )

    deduped: list = []
    prev_key = object()
    for part in parts:
        key = _key(part)
        if key is not None and key == prev_key:
            continue
        deduped.append(part)
        prev_key = key
    return deduped


# ---------------------------------------------------------------------------
# Main per-document extraction
# ---------------------------------------------------------------------------

def extract_pdf_data(pdf_path):
    print(f"Processing: {os.path.basename(pdf_path)}")
    doc = fitz.open(pdf_path)

    if has_image_only_table(doc):
        print(f"  -> Part Pricing table is image-based (not machine-readable text); marking as EXCEPTION per SOW.")
        return {
            "status": "exception",
            "exception_codes": [ExceptionCode.IMAGE_ONLY_TABLE],
            "exception_reason": "Required pricing data is not identifiable as text (table rendered as an image); PDF does not meet the digitally-generated, text-based document requirement.",
            "parts": [],
        }

    pages = [doc[i] for i in range(len(doc))]
    per_page = [_collect_page_data(p) for p in pages]
    master_header = find_master_table_header(doc)

    full_plain_text = "\n".join(p[0] for p in per_page)
    header_fields = extract_header_fields(full_plain_text)

    # Fail fast, before spending an LLM call on the (potentially multi-page,
    # multi-chunk) parts/pricing-tier extraction: both checks here are
    # deterministic (regex header fields + detected table header aliases),
    # so a document that's going to be excluded per EX-001/EX-002 anyway
    # never pays for the full table-data extraction.
    precheck_failures = validate_before_extraction(header_fields, master_header)
    if precheck_failures:
        exception_codes = sorted({c for f in precheck_failures for c in f["codes"]})
        exception_reason = " | ".join(f["message"] for f in precheck_failures)
        print(f"  -> Pre-extraction validation failed for {os.path.basename(pdf_path)}: {exception_codes}")
        return {
            "status": "exception",
            "exception_codes": exception_codes,
            "exception_reason": exception_reason,
            "quote_number": header_fields.get("quote_number"),
            "type": header_fields.get("type"),
            "issue_date": header_fields.get("issue_date"),
            "price_effective_date": header_fields.get("price_effective_date"),
            "customer_name": header_fields.get("customer_name"),
            "parts": [],
        }

    all_parts = []
    header_result = None
    warnings = []

    # Performance stats (Step 10) - one dict for the whole document, mutated
    # by run_chunk/the ambiguous-row resolver as calls happen, printed as a
    # summary once extraction finishes below.
    extraction_started_at = time.perf_counter()
    stats = {
        "total_pages": len(per_page),
        "pricing_pages": 0,
        "skipped_pages": 0,
        "total_chunks": 0,
        "total_llm_calls": 0,
        "ambiguous_calls": 0,
        "llm_time_s": 0.0,
    }

    def _chunk_has_no_extractable_content(chunk_pages) -> bool:
        """Cheap, conservative pre-filter (Step 4): a chunk is only skipped
        when NONE of its pages have a detected table AND NONE of their text
        contains a single digit anywhere - i.e. pure prose (comments,
        signatures, terms & conditions) with no possibility of pricing-table
        data. Any digit at all keeps the page in play, since a genuine
        orphaned last row (see _collect_page_data's own handling of that
        case) is reading-order text with no accompanying Markdown table but
        very much still has real digits/prices in it - this filter must
        never be the thing that drops that case."""
        for _, _, markdowns, _ in chunk_pages:
            if markdowns:
                return False
        combined_text = "\n".join(c[0] for c in chunk_pages)
        return not any(ch.isdigit() for ch in combined_text)

    def run_chunk(chunk_pages, page_start, page_end, retries=2):
        """Try to extract one page-range. Returns (parts_result, error_str) - result is None on failure.

        Internals only (per the performance refactor): build the lightweight
        raw-row prompt instead of the old full-schema one, call the LLM for
        raw rows only, resolve any LLM-flagged-ambiguous rows with a small
        targeted follow-up call (Step 8), then group/fill-down in pure
        Python (_fill_down_and_group_rows). The return shape - {"parts": [...]},
        error-or-None - is unchanged, so the surrounding chunk/retry loop
        below (including its per-page fallback on failure) needs no changes.
        """
        annotated_text = "\n".join(c[1] for c in chunk_pages)
        table_markdown = "\n\n".join(md for c in chunk_pages for md in c[2])
        notes = [n for c in chunk_pages for n in c[3]]

        stats["total_chunks"] += 1

        if _chunk_has_no_extractable_content(chunk_pages):
            stats["skipped_pages"] += len(chunk_pages)
            print(f"[EXTRACTION] Chunk pages {page_start}-{page_end}: skipped (no pricing-table content detected)")
            return {"parts": []}, None

        stats["pricing_pages"] += len(chunk_pages)

        def _extract_rows_from_batch(markdown_batch: str) -> Tuple[List[dict], int, int, float]:
            """One LLM call for one table-markdown batch (a whole page's
            table, or one row-count-limited slice of it - see
            MAX_TABLE_ROWS_PER_CALL/_split_table_markdown_into_batches).
            Returns (raw_rows, prompt_chars, response_chars, elapsed_s)."""
            batch_prompt = build_raw_row_prompt(annotated_text, markdown_batch, notes, master_header=master_header)
            call_started = time.perf_counter()
            raw_response = call_llm(batch_prompt, retries=retries, max_tokens=RAW_ROW_MAX_TOKENS)
            call_elapsed = time.perf_counter() - call_started
            stats["total_llm_calls"] += 1
            stats["llm_time_s"] += call_elapsed

            raw_rows_value = raw_response.get("rows") if isinstance(raw_response, dict) else None
            batch_rows: List[dict] = []
            if isinstance(raw_rows_value, list):
                malformed = 0
                for r in raw_rows_value:
                    if isinstance(r, dict):
                        batch_rows.append(r)
                    else:
                        # A malformed (non-object) row entry still represents
                        # one real physical row the model attempted to
                        # report - dropping it silently would lose that
                        # row's data outright. Route it through the same
                        # targeted ambiguous-row recovery path as Step 8
                        # (with neighbor context) instead of discarding it.
                        malformed += 1
                        batch_rows.append({
                            "cells": {},
                            "ambiguous": True,
                            "ambiguous_reason": f"malformed row entry from model (type {type(r).__name__}, not an object)",
                            # Preserved so the targeted ambiguous-row recovery
                            # prompt (build_ambiguous_row_prompt) has the
                            # model's original (malformed-shape) attempt to
                            # work from, instead of nothing at all.
                            "_raw_malformed_value": r,
                        })
                if malformed:
                    print(f"    Warning: {malformed} malformed (non-object) row entr{'y' if malformed == 1 else 'ies'} "
                          f"routed to ambiguous-row recovery instead of being dropped.")
            elif raw_rows_value is not None:
                print(f"    Warning: response 'rows' was type {type(raw_rows_value).__name__}, not a list - treating as 0 rows for this batch.")

            return batch_rows, len(batch_prompt), len(json.dumps(raw_response)), call_elapsed

        try:
            # Step 9: split a genuinely dense page's table into row-count-
            # limited batches instead of ever growing RAW_ROW_MAX_TOKENS to
            # cover it in one call - most pages have exactly one batch
            # (the whole page, unchanged) since most tables are well under
            # MAX_TABLE_ROWS_PER_CALL rows.
            markdown_batches = _split_table_markdown_into_batches(table_markdown, MAX_TABLE_ROWS_PER_CALL)

            all_raw_rows: List[dict] = []
            total_prompt_chars = 0
            total_response_chars = 0
            total_elapsed = 0.0
            for batch_index, markdown_batch in enumerate(markdown_batches):
                # Each batch is isolated: one batch failing after its own
                # retries must not discard rows another batch on this SAME
                # page already extracted successfully - that would violate
                # the "only the failed piece is retried/lost, not
                # everything else" rule this refactor is required to keep.
                # A failed batch's rows are recorded as a warning (page-
                # level data loss for just that slice), not a raised
                # exception that would abort the whole page.
                try:
                    batch_rows, prompt_chars, response_chars, elapsed = _extract_rows_from_batch(markdown_batch)
                    all_raw_rows.extend(batch_rows)
                    total_prompt_chars += prompt_chars
                    total_response_chars += response_chars
                    total_elapsed += elapsed
                except Exception as batch_error:
                    print(f"  Chunk pages {page_start}-{page_end} batch {batch_index + 1}/{len(markdown_batches)} failed: {batch_error}")
                    warnings.append(
                        f"Page {page_start}-{page_end} batch {batch_index + 1}/{len(markdown_batches)} could not be "
                        f"extracted after retries; its rows are missing. Last error: {batch_error}"
                    )

            resolved_rows = _resolve_ambiguous_rows_sequentially(all_raw_rows, master_header, notes, stats=stats)
            parts = _fill_down_and_group_rows(resolved_rows)

            batch_note = f" ({len(markdown_batches)} batches)" if len(markdown_batches) > 1 else ""
            print(
                f"[EXTRACTION] Chunk pages {page_start}-{page_end}{batch_note}\n"
                f"  Prompt chars: {total_prompt_chars}\n"
                f"  Response chars: {total_response_chars}\n"
                f"  LLM time: {total_elapsed:.1f} sec\n"
                f"  Rows extracted: {len(all_raw_rows)}\n"
                f"  Parts after grouping: {len(parts)}"
            )
            if all_raw_rows and not parts:
                # Rows came back but grouping produced nothing usable - this
                # should not normally happen (see _fill_down_and_group_rows)
                # and hiding it as a quiet 0-part success would silently
                # lose this page's data with no warning at all.
                print(f"    Warning: {len(all_raw_rows)} row(s) were extracted but 0 parts resulted after grouping - "
                      f"treating as a failure so this chunk is retried rather than silently accepted empty.")
                raise RuntimeError(
                    f"{len(all_raw_rows)} raw rows were parsed but grouping produced 0 parts - "
                    f"likely a malformed row shape from the model; sample: {json.dumps(all_raw_rows[0])[:300]}"
                )
            return {"parts": parts}, None
        except Exception as e:
            print(f"  Chunk pages {page_start}-{page_end} failed: {e}")
            return None, str(e)

    for chunk_start in range(0, len(per_page), CHUNK_PAGE_SIZE):
        chunk = per_page[chunk_start:chunk_start + CHUNK_PAGE_SIZE]
        page_end = chunk_start + len(chunk) - 1
        chunk_result, chunk_error = run_chunk(chunk, chunk_start, page_end)

        if chunk_result is None and len(chunk) > 1:
            # The whole chunk failed even after retries. Rather than silently
            # dropping this page range's data, fall back to one LLM call per
            # page - smaller prompts are less likely to hit the same failure
            # (e.g. a max_tokens truncation on a dense page-heavy table), and
            # we only lose the data we truly can't recover.
            print(f"  Retrying pages {chunk_start}-{page_end} individually...")
            recovered_parts = []
            for i, page_data in enumerate(chunk):
                page_no = chunk_start + i
                # Last line of defense before this page's data is lost for
                # good - retry harder than the chunk-level attempt.
                single, single_error = run_chunk([page_data], page_no, page_no, retries=4)
                if single is None:
                    warnings.append(
                        f"Page {page_no} could not be extracted after retries; "
                        f"its data is missing. Last error: {single_error}"
                    )
                else:
                    if header_result is None:
                        header_result = single
                    recovered_parts.extend(single.get("parts") or [])
            all_parts.extend(recovered_parts)
            continue

        if chunk_result is None:
            warnings.append(
                f"Page {chunk_start} could not be extracted after retries; "
                f"its data is missing. Last error: {chunk_error}"
            )
            continue

        if header_result is None:
            header_result = chunk_result
        all_parts.extend(chunk_result.get("parts") or [])

    def _log_extraction_summary(final_parts: list) -> None:
        """Step 10: end-of-document performance summary."""
        total_tiers = sum(len(p.get("pricing_tiers") or []) for p in final_parts if isinstance(p, dict))
        total_time = time.perf_counter() - extraction_started_at
        print(
            "[EXTRACTION SUMMARY]\n"
            f"  Total pages: {stats['total_pages']}\n"
            f"  Pricing pages: {stats['pricing_pages']}\n"
            f"  Skipped pages (no content): {stats['skipped_pages']}\n"
            f"  Total chunks: {stats['total_chunks']}\n"
            f"  Total LLM calls: {stats['total_llm_calls']}\n"
            f"  Ambiguous-row calls: {stats['ambiguous_calls']}\n"
            f"  Total LLM time: {stats['llm_time_s']:.1f} sec\n"
            f"  Total extraction time: {total_time:.1f} sec\n"
            f"  Parts extracted: {len(final_parts)}\n"
            f"  Pricing tiers extracted: {total_tiers}"
        )

    if header_result is None:
        # Every chunk and every per-page retry failed. Still emit a record
        # (with deterministic header fields and an explicit warning) instead
        # of silently producing no output at all for this document.
        print(f"Error processing {os.path.basename(pdf_path)}: all chunks failed.")
        _log_extraction_summary([])
        result = {
            "status": "extraction_failed",
            "exception_codes": [ExceptionCode.EXTRACTION_FAILED],
            "extraction_warnings": ["All pages failed LLM extraction after retries; no part data could be recovered."],
            "parts": [],
        }
        result.update(header_fields)
        return result

    result = dict(header_result)
    result["parts"] = _dedupe_adjacent_parts(all_parts)
    if warnings:
        result["extraction_warnings"] = warnings
    _log_extraction_summary(result["parts"])

    # Deterministic header fields are more reliable than the LLM's read of
    # loosely-formatted header text; overlay them onto the LLM's result.
    result.update(header_fields)

    # Enforce EX-001/EX-002 exception rules. A failure here excludes the ENTIRE quote from
    # automated processing (per the spec - not a partial/best-effort
    # result), citing the specific rule code(s) that failed.
    validation_failures = validate_required_fields(result)
    if validation_failures:
        exception_codes = sorted({c for f in validation_failures for c in f["codes"]})
        exception_reason = " | ".join(f["message"] for f in validation_failures)
        print(f"  -> Required-field validation failed for {os.path.basename(pdf_path)}: {exception_codes}")
        return {
            "status": "exception",
            "exception_codes": exception_codes,
            "exception_reason": exception_reason,
            "quote_number": result.get("quote_number"),
            "type": result.get("type"),
            "issue_date": result.get("issue_date"),
            "price_effective_date": result.get("price_effective_date"),
            "customer_name": result.get("customer_name"),
            "parts": [],
        }

    return result


# ===========================================================================
# ==================== EXTRACTION CODE END ==================================
# ===========================================================================


# ===========================================================================
# PDFExtractor — Backend integration wrapper
#
# This thin class connects extract_pdf_data() to the backend's service layer.
# service.py calls: PDFExtractor().extract_quote(pdf_path) -> QuoteExtractionResponse
# ===========================================================================

def _as_str(val) -> Optional[str]:
    """
    Pass a value through as a plain string, exactly as extract_pdf_data()
    produced it - no numeric coercion. Distinguishes "missing key" (-> None)
    from "extracted as empty string" (-> "", preserved as-is) since the
    schema instruction deliberately uses "" to mean "field not present on
    this row", not null.
    """
    if val is None:
        return None
    return str(val)


class PDFExtractor:
    def __init__(self):
        logger.info(f"PDFExtractor ready (model: {model_name})")

    def extract_quote(self, pdf_path: Path) -> QuoteExtractionResponse:
        """
        Extract quote data from a PDF file and return a QuoteExtractionResponse.
        This is the main entry point called by the ExtractionService.
        """
        raw_result = extract_pdf_data(str(pdf_path))

        # Convert raw dict to QuoteExtractionResponse
        is_exception = raw_result.get("status") in ("exception", "extraction_failed")
        exception_reason: Optional[str] = None
        if raw_result.get("exception_reason"):
            exception_reason = str(raw_result["exception_reason"])
        elif raw_result.get("status") == "extraction_failed":
            raw_warnings = raw_result.get("extraction_warnings", [])
            if isinstance(raw_warnings, list):
                exception_reason = "; ".join(str(w) for w in raw_warnings)

        raw_exception_codes = raw_result.get("exception_codes", [])
        exception_codes = [str(c) for c in raw_exception_codes] if isinstance(raw_exception_codes, list) else []

        # Surface any per-page extraction_warnings even on an otherwise
        # "successful" result (e.g. some pages recovered, others didn't) -
        # these were previously dropped silently, hiding missing-parts data
        # from the caller even though the extractor knew about it.
        raw_warnings = raw_result.get("extraction_warnings", [])
        low_confidence_fields = [str(w) for w in raw_warnings] if isinstance(raw_warnings, list) else []

        # Build typed PartInfo list from raw parts dicts
        parts: List[PartInfo] = []
        raw_parts_list = raw_result.get("parts", [])
        if not isinstance(raw_parts_list, list):
            raw_parts_list = []
        for idx, raw_part in enumerate(raw_parts_list, start=1):
            if not isinstance(raw_part, dict):
                continue
            pricing_tiers: List[PricingTier] = []
            raw_tiers = raw_part.get("pricing_tiers", [])
            if not isinstance(raw_tiers, list):
                raw_tiers = []
            for tier in raw_tiers:
                if not isinstance(tier, dict):
                    continue
                pricing_tiers.append(PricingTier(
                    moq=_as_str(tier.get("moq")),
                    price=_as_str(tier.get("price")),
                ))

            parts.append(PartInfo(
                line_number=_as_str(raw_part.get("line_number")) or str(idx),
                mold_number=_as_str(raw_part.get("mold_number")),
                evco_part_number=_as_str(raw_part.get("evco_part_number")),
                manufacturing_bom_number=_as_str(raw_part.get("manufacturing_bom_number")),
                customer_part_number=_as_str(raw_part.get("customer_part_number")),
                part_description=_as_str(raw_part.get("part_description")),
                box_quantity=_as_str(raw_part.get("box_quantity")),
                pricing_tiers=pricing_tiers,
            ))

        response = QuoteExtractionResponse(
            quote_number=_as_str(raw_result.get("quote_number")),
            type=_as_str(raw_result.get("type")),
           # issue_date=_as_str(raw_result.get("issue_date")),
            price_effective_date=_as_str(raw_result.get("price_effective_date")),
            customer_name=_as_str(raw_result.get("customer_name")),
          # address=None,
            #template_columns_present=None,
            parts=parts,
            low_confidence_fields=low_confidence_fields,
            is_exception=is_exception,
            exception_reason=exception_reason,
            exception_codes=exception_codes,
        )

        logger.info(f"Successfully extracted {len(parts)} parts from '{pdf_path.name}'")
        return response


# ===========================================================================
# OLD CODE — COMMENTED OUT
# The entire previous 2-stage image-OCR extraction is preserved below for
# reference. It has been fully replaced by the PyMuPDF-based approach above.
# ===========================================================================

# import base64
# import io
# import shutil
# import tempfile
# from pdf2image import convert_from_path
# from PIL import Image as PILImage
# from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam
# from app.modules.extraction.prompts import USER_PROMPT, build_stage2_prompt
# from app.modules.extraction.schemas import (
#     BoundingBox,
#     Image,
#     Page,
#     Section,
#     Table,
# )
#
#
# class MarkdownParser:
#     ... (old 2-stage OCR code removed for brevity — see git history)
#
#
# class OldPDFExtractor:
#     ... (old 2-stage OCR code removed for brevity — see git history)
#
#
# class ExtractionParser:
#     ... (old code removed for brevity — see git history)
