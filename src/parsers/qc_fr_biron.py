"""
Parser for Biron Groupe Santé lab reports (Quebec, French).

Biron reports are typically:
- Structured in tables: marker | value | unit | reference range | flag
- Date format: YYYY-MM-DD
- Patient block at the top with "Nom:", "Prénom:", "Date de naissance:", "Dossier:"
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, MatchStatus, Patient
from .base_parser import BaseParser


class BironQCParser(BaseParser):
    province = "QC"
    language = "fr"
    lab_name_hint = "Biron"

    # ------------------------------------------------------------------ #
    # Marker line pattern for Biron format:
    # "Hémoglobine          142      g/L      130-170"
    # "Leucocytes           6.8  H   10^9/L   4.0-11.0"
    # ------------------------------------------------------------------ #
    _MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-zÀ-ÿ][\w\s\(\)\-\/\.]+?)"  # marker name (may contain spaces)
        r"\s{2,}"                                       # 2+ spaces separating name from value
        r"(?P<value>[<>]?\s*\d+[.,]?\d*)"              # numeric value
        r"\s*(?P<flag>[HhLl\*]+)?"                     # optional flag
        r"\s*(?P<unit>[\w/\.\^\%µ]+)?"                 # optional unit
        r"\s*(?P<ref>[\d.,<>\-\s]+)?$",                # optional reference range
        re.UNICODE,
    )

    # Patient header patterns
    _NAME_RE = re.compile(r"(?:Nom\s*:\s*)([A-Za-zÀ-ÿ\s\-]+)", re.IGNORECASE)
    _FIRSTNAME_RE = re.compile(r"(?:Pr[eé]nom\s*:\s*)([A-Za-zÀ-ÿ\s\-]+)", re.IGNORECASE)
    _DOB_RE = re.compile(r"(?:Date de naissance|DDN)\s*:\s*(\d{4}[-/]\d{2}[-/]\d{2})", re.IGNORECASE)
    _HC_RE = re.compile(r"(?:NAM|Carte soleil|No\.\s*ass\.)\s*[:\#]?\s*([\d\s]{12,16})", re.IGNORECASE)
    _DOSSIER_RE = re.compile(r"(?:Dossier|Réq\.?|Requisition)\s*[:\#]?\s*(\w+)", re.IGNORECASE)
    _DATE_RE = re.compile(r"(?:Date de pr[eé]l[eè]vement|Prélèvement|Date rapport)\s*:\s*(\d{4}[-/]\d{2}[-/]\d{2})", re.IGNORECASE)

    def can_parse(self, text: str) -> bool:
        text_lower = text.lower()
        return "biron" in text_lower or (
            "groupe santé" in text_lower and
            any(kw in text_lower for kw in ["prénom", "dossier", "prélèvement"])
        )

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        result = LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="Biron Groupe Santé",
            province="QC",
            language=Language.FR,
            markers=markers,
        )
        return result

    # ------------------------------------------------------------------ #

    def _extract_patient(self, text: str) -> Patient:
        last_name = ""
        first_name = ""
        dob = None
        hc = None
        req = None

        m = self._NAME_RE.search(text)
        if m:
            last_name = self._normalise_name(m.group(1))

        m = self._FIRSTNAME_RE.search(text)
        if m:
            first_name = self._normalise_name(m.group(1))

        full_name = f"{first_name} {last_name}".strip() or "UNKNOWN"

        m = self._DOB_RE.search(text)
        if m:
            dob = self._parse_date(m.group(1))

        m = self._HC_RE.search(text)
        if m:
            hc = self._clean_health_card(m.group(1))

        m = self._DOSSIER_RE.search(text)
        if m:
            req = m.group(1).strip()

        return Patient(
            full_name=full_name,
            date_of_birth=dob,
            health_card_number=hc,
            requisition_number=req,
            province="QC",
        )

    def _extract_collection_date(self, text: str) -> Optional[date]:
        m = self._DATE_RE.search(text)
        if m:
            return self._parse_date(m.group(1))
        return None

    def _extract_markers(self, text: str) -> list[MarkerResult]:
        markers = []
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 5:
                continue
            m = self._MARKER_LINE_RE.match(line)
            if m:
                name = self._normalise_name(m.group("name"))
                value = m.group("value").replace(" ", "").replace(",", ".")
                unit = m.group("unit") or None
                ref = (m.group("ref") or "").strip() or None
                flag = (m.group("flag") or "").upper() or None
                if name and value:
                    markers.append(MarkerResult(
                        raw_name=name,
                        value=value,
                        unit=unit,
                        reference_range=ref,
                        flag=flag,
                    ))
        return markers
