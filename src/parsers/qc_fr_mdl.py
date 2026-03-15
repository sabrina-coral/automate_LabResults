"""
Parser for MDL (Laboratoires médicaux Biochimique / MDL) Quebec French reports.
Also covers generic CIUSSS/CISSS Quebec hospital lab formats.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, Patient
from .base_parser import BaseParser


class MDLParser(BaseParser):
    province = "QC"
    language = "fr"
    lab_name_hint = "MDL / CIUSSS QC"

    # MDL format often puts value right after a colon or in a fixed column
    # e.g. "Glycémie .................. 5.2   mmol/L   [3.9-5.5]"
    _MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-zÀ-ÿ][\w\s\(\)\-\/\.]+?)"
        r"[\s\.]{3,}"                            # dots or spaces used as filler
        r"(?P<value>[<>]?\s*\d+[.,]?\d*)"
        r"\s*(?P<flag>[HhLlÉ\*]+)?"
        r"\s*(?P<unit>[\w/\.\^\%µ]+)?"
        r"\s*[\[\(]?(?P<ref>[\d.,<>\-\s]+)?[\]\)]?$",
        re.UNICODE,
    )

    _NAME_RE = re.compile(r"(?:Patient|Nom du patient)\s*[:\-]\s*([A-Za-zÀ-ÿ,\s\-]+)", re.IGNORECASE)
    _DOB_RE = re.compile(r"(?:Né\(e\) le|D\.N\.|DDN|Date naiss\.?)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2})", re.IGNORECASE)
    _HC_RE = re.compile(r"(?:NAM|N\.A\.M\.)\s*[:\-]?\s*([\d\s]{12,14})", re.IGNORECASE)
    _COLLECT_DATE_RE = re.compile(r"(?:Date de collect|Prélèv\.?)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2})", re.IGNORECASE)

    def can_parse(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in [
            "mdl", "médico-légaux", "biochem", "ciusss", "cisss", "chum", "chus", "chuq",
        ]) and any(kw in text_lower for kw in ["nam", "prénom", "prélèv"])

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        return LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="MDL / CIUSSS QC",
            province="QC",
            language=Language.FR,
            markers=markers,
        )

    def _extract_patient(self, text: str) -> Patient:
        full_name = "UNKNOWN"
        dob = None
        hc = None

        m = self._NAME_RE.search(text)
        if m:
            # MDL often returns "LASTNAME, Firstname" — normalise to "Firstname LASTNAME"
            raw = m.group(1).strip()
            if "," in raw:
                parts = [p.strip() for p in raw.split(",", 1)]
                full_name = f"{parts[1]} {parts[0]}"
            else:
                full_name = raw

        m = self._DOB_RE.search(text)
        if m:
            dob = self._parse_date(m.group(1))

        m = self._HC_RE.search(text)
        if m:
            hc = self._clean_health_card(m.group(1))

        return Patient(
            full_name=full_name,
            date_of_birth=dob,
            health_card_number=hc,
            province="QC",
        )

    def _extract_collection_date(self, text: str) -> Optional[date]:
        m = self._COLLECT_DATE_RE.search(text)
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
