"""
Parser for LifeLabs (BC, Ontario) English lab reports.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, Patient
from .base_parser import BaseParser


class LifeLabsParser(BaseParser):
    province = "ON"   # Also used for BC — province overridden after detection
    language = "en"
    lab_name_hint = "LifeLabs"

    # LifeLabs digital format (2+ spaces): "Hemoglobin  142  g/L  120-160"
    _MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-z][\w\s\(\),\-\/\.]+?)"
        r"\s{2,}"
        r"(?P<value>[<>]?\s*\d+[.,]?\d*)"
        r"\s*(?P<flag>[HhLlCc\*]+)?"
        r"\s*(?P<unit>[\w/\.\^\%µ]+)?"
        r"\s*(?P<ref>[\d.,<>\-\s]+)?$",
    )

    # LifeLabs OCR/fax format (single-space, col order: value → ref → unit → flag):
    # "Hemoglobin 126 115--155 g/L VRL"
    # "ALT 11 <36 U/L VRL"
    # "PROGESTERONE 0.7 nmol/L VRL"
    _OCR_MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-z][A-Za-z\s\-\/\(\),\.]+?)"
        r"\s+"
        r"(?P<value>[<>]?\d+\.?\d*)"
        r"(?:\s+(?P<ref>(?:[\d.,]+--[\d.,]+|[\d.,]+\s*-\s*[\d.,]+|[<>=]+\s*[\d.,]+)))?"
        r"(?:\s+(?P<unit>[a-zA-Z/%µ][/\w\.\^\%µ]*))?"
        r"(?:\s+(?P<flag>[A-Z]{2,}))?"
        r"\s*$",
    )

    _NAME_RE = re.compile(r"(?:Patient|Name)\s*:\s*([A-Za-z,\s\-']+)", re.IGNORECASE)
    _DOB_RE = re.compile(r"(?:DOB|Date of Birth)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}|\w+ \d+,? \d{4})", re.IGNORECASE)
    _HC_RE = re.compile(r"(?:PHN|Health Card|BC PHN)\s*[:#]?\s*(\d[\d\s]{7,12})", re.IGNORECASE)
    _COLLECT_DATE_RE = re.compile(r"(?:Collected|Collection|Date Collected)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})", re.IGNORECASE)

    def can_parse(self, text: str) -> bool:
        return "lifelabs" in text.lower()

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        # Determine province from text
        prov = "BC" if "bc biomedical" in text.lower() or "british columbia" in text.lower() else "ON"

        return LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="LifeLabs",
            province=prov,
            language=Language.EN,
            markers=markers,
        )

    def _extract_patient(self, text: str) -> Patient:
        full_name = "UNKNOWN"
        dob = None
        hc = None

        m = self._NAME_RE.search(text)
        if m:
            raw = m.group(1).strip()
            if "," in raw:
                parts = [p.strip() for p in raw.split(",", 1)]
                full_name = f"{parts[1]} {parts[0]}"
            else:
                full_name = self._normalise_name(raw)

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
            province="ON",
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
            m = self._MARKER_LINE_RE.match(line) or self._OCR_MARKER_LINE_RE.match(line)
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
