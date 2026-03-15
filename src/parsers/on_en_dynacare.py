"""
Parser for Dynacare (Ontario / Western Canada) English lab reports.

Dynacare format typically:
- Patient block: "Patient Name:", "Date of Birth:", "Health Card:"
- Results table: test name | result | units | reference interval | flag
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, Patient
from .base_parser import BaseParser


class DynacareParser(BaseParser):
    province = "ON"
    language = "en"
    lab_name_hint = "Dynacare"

    # Dynacare result line:
    # "Hemoglobin                  142    g/L       120-160"
    # "Glucose, Fasting            5.2    mmol/L    3.9-6.1    "
    _MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-z][\w\s\(\),\-\/\.]+?)"
        r"\s{2,}"
        r"(?P<value>[<>]?\s*\d+[.,]?\d*)"
        r"\s*(?P<flag>[HhLl\*]+)?"
        r"\s*(?P<unit>[\w/\.\^\%µ]+)?"
        r"\s*(?P<ref>[\d.,<>\-\s]+)?$",
    )

    _NAME_RE = re.compile(r"(?:Patient\s+Name|Name)\s*[:\-]\s*([A-Za-z,\s\-']+)", re.IGNORECASE)
    _DOB_RE = re.compile(r"(?:Date of Birth|DOB|D\.O\.B\.)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})", re.IGNORECASE)
    _HC_RE = re.compile(r"(?:Health Card|HCN|OHIP)\s*[:#]?\s*(\d[\d\s\-]{7,14}[A-Z]{0,2})", re.IGNORECASE)
    _REQ_RE = re.compile(r"(?:Requisition|Req\.?|Accession)\s*[:#]?\s*(\w+)", re.IGNORECASE)
    _COLLECT_DATE_RE = re.compile(r"(?:Collection Date|Collected|Date Collected)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})", re.IGNORECASE)

    def can_parse(self, text: str) -> bool:
        text_lower = text.lower()
        return "dynacare" in text_lower or "gamma-dynacare" in text_lower

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        return LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="Dynacare",
            province="ON",
            language=Language.EN,
            markers=markers,
        )

    def _extract_patient(self, text: str) -> Patient:
        full_name = "UNKNOWN"
        dob = None
        hc = None
        req = None

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

        m = self._REQ_RE.search(text)
        if m:
            req = m.group(1).strip()

        return Patient(
            full_name=full_name,
            date_of_birth=dob,
            health_card_number=hc,
            requisition_number=req,
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
