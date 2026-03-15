"""
PDF text extraction with automatic fallback to OCR for scanned documents.

Strategy:
1. Try pdfplumber (native text layer) — fast, accurate for digital PDFs
2. If text is sparse/empty, fall back to OCR via pdf2image + pytesseract
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


# Minimum characters per page to consider native text layer usable
_MIN_TEXT_THRESHOLD = 100


def extract_text(pdf_path: str | Path, lang_hint: Optional[str] = None) -> tuple[str, bool]:
    """
    Extract text from a PDF file.

    Returns:
        (text, used_ocr): full extracted text, and whether OCR was used.

    Args:
        pdf_path: path to the PDF file
        lang_hint: ISO 639 language hint ('en', 'fr') to guide Tesseract
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text = _extract_native(pdf_path)
    if _text_is_usable(text):
        return text, False

    # Native text layer was absent or too sparse — use OCR
    if not OCR_AVAILABLE:
        if text:
            return text, False  # use whatever we got
        raise RuntimeError(
            "PDF appears to be a scanned image but pdf2image / pytesseract are not installed.\n"
            "Run: pip install pdf2image pytesseract\n"
            "Also ensure Tesseract is installed: https://github.com/tesseract-ocr/tesseract"
        )

    text = _extract_ocr(pdf_path, lang_hint=lang_hint)
    return text, True


def _extract_native(pdf_path: Path) -> str:
    """Extract text using pdfplumber (native text layer)."""
    if pdfplumber is None:
        return ""
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
    except Exception as exc:
        # Corrupted or encrypted PDF — fall through to OCR
        return ""
    return "\n".join(pages)


def _extract_ocr(pdf_path: Path, lang_hint: Optional[str] = None) -> str:
    """Convert PDF pages to images and run Tesseract OCR."""
    # Map our language codes to Tesseract language codes
    tesseract_lang_map = {
        "fr": "fra",
        "en": "eng",
    }
    # Use both English and French by default for Canadian lab results
    if lang_hint and lang_hint in tesseract_lang_map:
        tess_lang = tesseract_lang_map[lang_hint]
    else:
        tess_lang = "eng+fra"

    # Use 300 DPI for good OCR quality on lab results
    images = convert_from_path(str(pdf_path), dpi=300)

    pages = []
    for img in images:
        config = f"--oem 3 --psm 6 -l {tess_lang}"
        page_text = pytesseract.image_to_string(img, config=config)
        pages.append(page_text)

    return "\n".join(pages)


def _text_is_usable(text: str) -> bool:
    """Return True if the extracted text looks like real lab data (not garbage)."""
    if not text or len(text.strip()) < _MIN_TEXT_THRESHOLD:
        return False
    # Check for at least some numeric content (lab results always have numbers)
    numeric_count = len(re.findall(r"\d+[.,]?\d*", text))
    return numeric_count >= 5


def detect_language(text: str) -> str:
    """
    Detect whether the lab report is in French or English.
    Uses simple keyword frequency — no external libraries required.
    """
    fr_keywords = [
        "résultat", "analyse", "valeur", "référence", "unité", "patient",
        "date", "médecin", "hémoglobine", "globules", "leucocytes",
        "plaquettes", "créatinine", "glycémie", "cholestérol",
    ]
    en_keywords = [
        "result", "analysis", "value", "reference", "unit", "patient",
        "date", "physician", "hemoglobin", "white blood", "platelets",
        "creatinine", "glucose", "cholesterol",
    ]
    text_lower = text.lower()
    fr_score = sum(1 for kw in fr_keywords if kw in text_lower)
    en_score = sum(1 for kw in en_keywords if kw in text_lower)

    if fr_score > en_score:
        return "fr"
    elif en_score > fr_score:
        return "en"
    return "unknown"


def detect_province(text: str) -> Optional[str]:
    """
    Detect Canadian province from lab report text.
    Uses lab names, addresses, and common provincial lab identifiers.
    """
    text_lower = text.lower()

    province_patterns: list[tuple[str, list[str]]] = [
        ("QC", ["québec", "quebec", "lspq", "chum", "ciusss", "cisss",
                "bio clinical", "biron", "mdl", "chus", "chuq"]),
        ("ON", ["ontario", "dynacare", "lifelabs", "gamma-dynacare",
                "mount sinai", "sunnybrook", "uhn"]),
        ("BC", ["british columbia", "bc biomedical", "lifelabs bc",
                "providence health", "phsa"]),
        ("AB", ["alberta", "dynalife", "covenant health", "ahs lab"]),
        ("SK", ["saskatchewan", "sask", "rqhealth", "prairie north"]),
        ("MB", ["manitoba", "dynacare mb", "diagnostics for the real world"]),
        ("NS", ["nova scotia", "nsha", "izaak walton killam"]),
        ("NB", ["new brunswick", "nouveau-brunswick", "horizon health",
                "vitalité"]),
        ("NL", ["newfoundland", "labrador", "eastern health", "nlhs"]),
        ("PE", ["prince edward island", "pei", "qeii"]),
    ]
    for province, keywords in province_patterns:
        if any(kw in text_lower for kw in keywords):
            return province

    return None
