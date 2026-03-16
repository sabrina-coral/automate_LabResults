"""
Parser for Dynacare lab reports in French (Quebec).

Format characteristics:
- Two-column layout: HÉMATOLOGIE on the left, BIOCHIMIE/ENDOCRINOLOGIE on the right
- pdfplumber merges both columns onto the same line
- Test names are ALL CAPS (may contain #, ., -, /)
- Line format: NAME VALUE [↑↓*] [REF_RANGE] UNIT  (repeated twice on same line)
- Units: x10^9/L, x10^12/L, mmol/L, g/L, fL, pg, %, etc.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, Patient
from .base_parser import BaseParser


class DynacareQCFrParser(BaseParser):
    province = "QC"
    language = "fr"
    lab_name_hint = "Dynacare"

    # Matches one result entry within a line.
    # Name: starts with uppercase, may include spaces, #, ., -, / (for multi-word names).
    # Value: optional < or > prefix, then digits with optional decimal.
    # Flag: single ↑ ↓ or * (optional).
    # Ref: single-bound (<N or >N) or range (N-N), both optional.
    # Unit: handles x10^9/L, x10^12/L, mmol/L, fL, %, L/L, etc.
    _ENTRY_RE = re.compile(
        r"([A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ#][A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ0-9\s#.\-/]*?)"
        r"\s+"
        r"([<>]?\s*\d+[.,]?\d*)"                                    # value
        r"(?:\s+[↑↓*])?"                                             # optional flag
        r"(?:\s+([<>]?[\d.,]+(?:[-–][<>]?[\d.,]+)?))?"              # optional ref
        r"\s+"
        r"(x10\^?\d+/[A-Za-z]+|[A-Za-zµ%][A-Za-z0-9/.\^%µ²³¹*]*)",  # unit
        re.UNICODE,
    )

    _COLLECT_DATE_RE = re.compile(
        r"Prélevé\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE
    )
    # "1975-08-02 50 Ans" pattern (DOB followed by age)
    _DOB_RE = re.compile(
        r"\b(\d{4}-\d{2}-\d{2})\s+\d+\s+Ans\b", re.IGNORECASE
    )
    # Quebec RAMQ: 4 uppercase letters + 8 digits, e.g. PATJ75580217
    _RAMQ_RE = re.compile(r"\b([A-Z]{4}\d{8})\b")
    # Patient name: two back-to-back LASTNAME, FIRSTNAME patterns on same line.
    # The first is the doctor; the second is the patient.
    _NAME_PAIR_RE = re.compile(
        r"([A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ][A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ\-]+,"
        r"\s*[A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ\-]+)",
        re.UNICODE,
    )

    # Section/header words that appear alone on a line – skip these entirely.
    _SECTION_RE = re.compile(
        r"^\s*(?:HÉMATOLOG|BIOCHIM|ENDOCRIN|LIPIDES|VITAMINES|"
        r"Analyses\s+Résultat|Requérant|Patient|Médecin|Compte|"
        r"CONTRACTS|RAPPORT|LÉGENDE|Anormal|Alerte|Critique|"
        r"Les analyses|D\.\s+Gauthier|A\.\s+Ibrahim|"
        r"Date\s+Reçu|Finalisé|Imprimé|REQUÊTE|Final|Copie|"
        r"Page\s+\d|Faxed\s+by|Dynacare\s*$|"
        r"boul\.|^\d{3}[-\s]|Tel\s*:|Tél\s*:|Fax\s*:|"
        r"Prélevé\s+\d|MONTREAL|Laval|130-|1172\s+RUE|301-)",
        re.IGNORECASE | re.UNICODE,
    )

    # Lines that contain ONLY commentary (no test name at the start).
    # NOTE: applied only to lines where the entire content looks like commentary,
    # not as a blanket line-skip. Two-column lines may have commentary in the
    # right column but a valid test in the left column — the regex handles that.
    _PURE_COMMENTARY_RE = re.compile(
        r"^\s*(?:Valeurs\s+de\s+références?|Folliculaire\s*:|Ovulatoire\s*:|"
        r"Lutéale\s*:|Post-?ménopaus|Risque\s+(?:faible|modéré|élevé)|"
        r"Cible\s+de\s+traitement|Arthritis\s+Care|"
        r"testostérone libre est estimée|LES ANALYSES RÉFÉRÉES|"
        r"La testostérone libre est estimée)",
        re.IGNORECASE | re.UNICODE,
    )

    def can_parse(self, text: str) -> bool:
        text_lower = text.lower()
        is_dynacare = "dynacare" in text_lower or "gamma-dynacare" in text_lower
        is_french = "prélevé" in text_lower or "hématologie" in text_lower
        return is_dynacare and is_french

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        return LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="Dynacare",
            province="QC",
            language=Language.FR,
            markers=markers,
        )

    def _extract_patient(self, text: str) -> Patient:
        full_name = "UNKNOWN"
        dob = None
        ramq = None

        # DOB: "1975-08-02 50 Ans"
        m = self._DOB_RE.search(text)
        if m:
            dob = self._parse_date(m.group(1))

        # RAMQ e.g. PATJ75580217
        m = self._RAMQ_RE.search(text)
        if m:
            ramq = m.group(1)

        # Patient name: find all LASTNAME, FIRSTNAME patterns in the header block.
        # The doctor's name appears first; the patient's name is second.
        header = text[:1000]
        names = self._NAME_PAIR_RE.findall(header)
        if len(names) >= 2:
            raw = names[1].strip()
            parts = [p.strip() for p in raw.split(",", 1)]
            full_name = f"{parts[1]} {parts[0]}" if len(parts) == 2 else raw
        elif len(names) == 1:
            raw = names[0].strip()
            parts = [p.strip() for p in raw.split(",", 1)]
            full_name = f"{parts[1]} {parts[0]}" if len(parts) == 2 else raw

        return Patient(
            full_name=full_name,
            date_of_birth=dob,
            health_card_number=ramq,
            province="QC",
        )

    def _extract_collection_date(self, text: str) -> Optional[date]:
        m = self._COLLECT_DATE_RE.search(text)
        if m:
            return self._parse_date(m.group(1))
        return None

    def _extract_markers(self, text: str) -> list[MarkerResult]:
        markers: list[MarkerResult] = []
        seen: set[str] = set()

        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if self._SECTION_RE.match(line):
                continue
            if self._PURE_COMMENTARY_RE.match(line):
                continue

            # finditer handles two-column lines: finds all non-overlapping matches
            for m in self._ENTRY_RE.finditer(line):
                raw_name = m.group(1).strip()
                # Remove trailing punctuation / stray dots
                raw_name = raw_name.rstrip(". ").strip()
                if len(raw_name) < 2:
                    continue
                # Skip if name looks like a pure number or is already seen
                norm = raw_name.lower()
                if norm in seen:
                    continue

                value = m.group(2).replace(" ", "").replace(",", ".")
                ref = (m.group(3) or "").replace(",", ".").strip() or None
                unit = m.group(4).strip()

                seen.add(norm)
                markers.append(MarkerResult(
                    raw_name=raw_name,
                    value=value,
                    unit=unit or None,
                    reference_range=ref,
                ))

        return markers
