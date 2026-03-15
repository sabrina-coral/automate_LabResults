"""
Abstract base parser.  All province/lab-specific parsers extend this.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Optional

from ..models import LabResult, MarkerResult, Patient


# ---------------------------------------------------------------------------
# Common regex patterns shared across parsers
# ---------------------------------------------------------------------------

# Date formats encountered in Canadian lab reports
_DATE_PATTERNS = [
    r"(\d{4}[-/]\d{2}[-/]\d{2})",           # 2024-03-15 or 2024/03/15
    r"(\d{2}[-/]\d{2}[-/]\d{4})",           # 15-03-2024 or 15/03/2024
    r"(\d{1,2}\s+\w+\s+\d{4})",             # 15 March 2024 / 15 mars 2024
    r"(\w+\s+\d{1,2},?\s+\d{4})",           # March 15, 2024 / mars 15, 2024
]

# Health card patterns by province
_HC_PATTERNS = {
    "QC": r"\b(\d{4}\s?\d{4}\s?\d{4})\b",                       # 1234 5678 9012
    "ON": r"\b(\d{4}[-\s]?\d{3}[-\s]?\d{3}[-\s]?[A-Z]{2})\b",  # 1234-567-890-AB
    "BC": r"\b(\d{10})\b",
    "AB": r"\b(\d{9})\b",
    "DEFAULT": r"\b([A-Z]{0,4}\d{6,12}[A-Z]{0,2})\b",
}

# Numeric value with optional comma decimal (French-style) and sign
_VALUE_RE = re.compile(r"[<>]?\s*\d+[.,]?\d*")

# Flag characters in lab reports
_FLAG_PATTERNS = re.compile(
    r"\b(H|L|HH|LL|HIGH|LOW|ÉLEVÉ|ÉLEVÉE|FAIBLE|ANORMAL|ABNORMAL|CRITIQUE|CRITICAL|\*+)\b",
    re.IGNORECASE,
)


class BaseParser(ABC):
    """
    Base class for lab result parsers.

    Subclasses must implement:
      - `can_parse(text) -> bool`
      - `parse(text, source_file) -> LabResult`

    And may override:
      - `extract_patient(text) -> Patient`
      - `extract_markers(text) -> list[MarkerResult]`
    """

    # Subclasses set these to identify themselves
    province: Optional[str] = None
    language: str = "en"
    lab_name_hint: Optional[str] = None  # e.g. "Dynacare", "Biron"

    @abstractmethod
    def can_parse(self, text: str) -> bool:
        """Return True if this parser recognizes the given text."""

    @abstractmethod
    def parse(self, text: str, source_file: str) -> LabResult:
        """Parse the full text and return a LabResult."""

    # ------------------------------------------------------------------
    # Shared helpers available to all subclasses
    # ------------------------------------------------------------------

    def _parse_date(self, text: str) -> Optional[date]:
        """Try multiple date formats; return the first successful parse."""
        fr_months = {
            "janvier": "January", "février": "February", "mars": "March",
            "avril": "April", "mai": "May", "juin": "June",
            "juillet": "July", "août": "August", "septembre": "September",
            "octobre": "October", "novembre": "November", "décembre": "December",
        }
        # Normalise French month names
        normalised = text.lower()
        for fr, en in fr_months.items():
            normalised = normalised.replace(fr, en.lower())

        formats = [
            "%Y-%m-%d", "%Y/%m/%d",
            "%d-%m-%Y", "%d/%m/%Y",
            "%d %B %Y", "%B %d, %Y", "%B %d %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(normalised.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _find_dates(self, text: str) -> list[date]:
        """Find all dates in a block of text."""
        found = []
        for pattern in _DATE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                d = self._parse_date(match.group(1))
                if d:
                    found.append(d)
        return found

    def _extract_value_and_unit(self, token: str) -> tuple[Optional[str], Optional[str]]:
        """
        Split a token like '142 g/L' or '3.5mmol/L' into (value, unit).
        """
        token = token.strip()
        m = re.match(r"([<>]?\s*\d+[.,]?\d*)\s*(.*)", token)
        if not m:
            return None, None
        value = m.group(1).strip()
        unit = m.group(2).strip() or None
        return value, unit

    def _extract_flag(self, line: str) -> Optional[str]:
        """Extract abnormal flag from a result line."""
        m = _FLAG_PATTERNS.search(line)
        return m.group(0).upper() if m else None

    def _normalise_name(self, name: str) -> str:
        """Normalise a patient/marker name for comparison."""
        name = name.strip()
        name = re.sub(r"\s+", " ", name)
        # Remove parenthetical qualifiers like "(Routine)" or "(Urgence)"
        name = re.sub(r"\(.*?\)", "", name).strip()
        return name

    def _clean_health_card(self, raw: str, province: Optional[str] = None) -> str:
        """Remove formatting from health card numbers."""
        return re.sub(r"[\s\-]", "", raw).upper()
