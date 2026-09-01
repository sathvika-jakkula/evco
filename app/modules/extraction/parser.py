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

CHUNK_PAGE_SIZE = 4

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


def call_llm(prompt, retries=2):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=16000,
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

    def run_chunk(chunk_pages, page_start, page_end, retries=2):
        """Try to extract one page-range. Returns (parts_result, error_str) - result is None on failure."""
        annotated_text = "\n".join(c[1] for c in chunk_pages)
        table_markdown = "\n\n".join(md for c in chunk_pages for md in c[2])
        notes = [n for c in chunk_pages for n in c[3]]
        prompt = build_prompt(annotated_text, table_markdown, notes, master_header=master_header)
        try:
            chunk_result = call_llm(prompt, retries=retries)
            return chunk_result, None
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

    if header_result is None:
        # Every chunk and every per-page retry failed. Still emit a record
        # (with deterministic header fields and an explicit warning) instead
        # of silently producing no output at all for this document.
        print(f"Error processing {os.path.basename(pdf_path)}: all chunks failed.")
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
