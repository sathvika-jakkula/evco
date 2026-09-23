# English data for local header OCR

`eng.traineddata` is the official Tesseract `tessdata_fast` English model,
vendored so extraction does not download data or require a separate executable
at runtime. PyMuPDF provides the OCR integration. Include this directory when
packaging the backend.

Source: https://github.com/tesseract-ocr/tessdata_fast/blob/main/eng.traineddata
Downloaded: 2026-09-23
License: Apache-2.0 (see LICENSE)
SHA-256: `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`

This fallback runs only when no usable master header exists, the page is
image-backed with existing BOM/price text, and no native table grid exists.
It OCRs isolated header cells from a dark filled table header. The parser accepts
only a header that identifies every required column. It does not OCR numeric
rows, enable general scanned-document extraction, or change validation rules.
Unsupported layouts or OCR failures retain the existing exception behavior.
