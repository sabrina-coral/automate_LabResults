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

    # Dynacare result line (digital / 2-space format):
    # "Hemoglobin                  142    g/L       120-160"
    _MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-z][\w\s\(\),\-\/\.]+?)"
        r"\s{2,}"
        r"(?P<value>[<>]?\s*\d+[.,]?\d*)"
        r"\s*(?P<flag>[HhLl\*]+)?"
        r"\s*(?P<unit>[\w/\.\^\%µ]+)?"
        r"\s*(?P<ref>[\d.,<>\-\s]+)?$",
    )

    # Dynacare fax/OCR result line (single-space, ALL-CAPS names):
    # "GLUCOSE SERUM FASTING 4.8 mmol/L"
    # "CREATININE 84. 50 - 100 umol/L"
    # "25 HYDROXY VITAMIN D 70. DEFICIENCY: < 25 nmol/L"
    _FAX_MARKER_LINE_RE = re.compile(
        r"^(?P<name>[\dA-Za-z][\w\s\(\),\-\/\.]+?)"
        r"\s+"
        r"(?P<value>[<>]?\d+\.?\d*)\.?"
        r"(?:\s+(?P<ref>(?:[<>=]+\s*[\d.,]+(?:\s*[-—]+\s*[\d.,]+)?|[\d.,]+\s*[-—]+\s*[\d.,]+)))?"
        r"(?:\s+(?P<unit>[a-zA-Z/%µ][/\w\.\^\%µ]*))?"
        r".*$",
    )

    # Lines in fax/OCR reports that are metadata, not results
    _FAX_SKIP_RE = re.compile(
        r"^\d{4}[/\-]\d{2}[/\-]\d{2}"          # date header e.g. 2025/09/26
        r"|^(page\s+\d|posting\b|faxed by)"      # fax footer/header keywords
        r"|collection time"
        r"|hours fasting"
        r"|sender at"
        r"|customer service"
        r"|refer to (can|the \d|can j)"
        r"|arthritis care"
        r"|\d{4}[-/]\d{2}[-/]\d{2}",             # embedded date anywhere
        re.IGNORECASE,
    )

    _NAME_RE = re.compile(r"(?:Patient\s+Name|Name)\s*[:\-]\s*([A-Za-z,\s\-']+)", re.IGNORECASE)
    # Fax format: "LASTNAME, FIRSTNAME  1234567890 AL  DR. ..."
    # Use ' +' (spaces only, no newline) to avoid crossing lines
    _FAX_NAME_RE = re.compile(r"^([A-Z]{2,}(?:[ \-][A-Z]+)*), +([A-Z]{2,}(?:[ \-][A-Z]+)*) +\d", re.MULTILINE)
    _DOB_RE = re.compile(r"(?:Date of Birth|DOB|D\.O\.B\.)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})", re.IGNORECASE)
    # Fax format DOB: YYYY/MM/DD followed by a street number then a word (address line)
    # e.g. "1989/02/13 1172 RUE SHERBROOKE" — not "2025/09/26 2025/09/26"
    _FAX_DOB_RE = re.compile(r"^(\d{4}/\d{2}/\d{2}) +\d{3,4} +[A-Z]{2,}", re.MULTILINE)
    _HC_RE = re.compile(r"(?:Health Card|HCN|OHIP)\s*[:#]?\s*(\d[\d\s\-]{7,14}[A-Z]{0,2})", re.IGNORECASE)
    _REQ_RE = re.compile(r"(?:Requisition|Req\.?|Accession)\s*[:#]?\s*(\w+)", re.IGNORECASE)
    _COLLECT_DATE_RE = re.compile(r"(?:Collection Date|Collected|Date Collected)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})", re.IGNORECASE)
    # Fax format collection date: "COLLECTION TIME 10-XXXXXXX FINAL\n YYYY/MM/DD HH:MM"
    _FAX_COLLECT_DATE_RE = re.compile(r"COLLECTION TIME\s+\S+\s+FINAL\s+(\d{4}/\d{2}/\d{2})", re.IGNORECASE)

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
        else:
            # Fax format: "LASTNAME, FIRSTNAME  <accession> AL  DR. ..."
            m = self._FAX_NAME_RE.search(text)
            if m:
                full_name = f"{m.group(2).strip()} {m.group(1).strip()}"

        m = self._DOB_RE.search(text)
        if m:
            dob = self._parse_date(m.group(1))
        else:
            m = self._FAX_DOB_RE.search(text)
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
        m = self._FAX_COLLECT_DATE_RE.search(text)
        if m:
            return self._parse_date(m.group(1))
        return None

    def _extract_markers(self, text: str) -> list[MarkerResult]:
        markers = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or len(line) < 5:
                continue

            m = self._MARKER_LINE_RE.match(line)
            if not m:
                # Skip obvious fax metadata / footnote lines
                if self._FAX_SKIP_RE.search(line):
                    continue
                # Fax/OCR format: preprocess then try fax regex.
                # Strip leading non-letter/digit chars (e.g. ". MCH" → "MCH")
                line = re.sub(r"^[^\w\d]+", "", line)
                # If line contains ": " and right side starts with a word char,
                # take only the right side (e.g. "RBC INDICES: MCV 82." → "MCV 82.")
                if ": " in line:
                    right = line.split(": ", 1)[1]
                    if right and right[0].isalpha():
                        line = right
                m = self._FAX_MARKER_LINE_RE.match(line)

            if m:
                name = self._normalise_name(m.group("name"))
                raw_val = m.group("value").replace(" ", "").replace(",", ".")
                # Strip trailing period (OCR artifact)
                value = raw_val.rstrip(".")
                unit = m.group("unit") or None
                ref = (m.group("ref") or "").strip() or None
                flag = (m.group("flag") if "flag" in m.groupdict() else None)
                flag = (flag or "").upper() or None
                if name and value:
                    markers.append(MarkerResult(
                        raw_name=name,
                        value=value,
                        unit=unit,
                        reference_range=ref,
                        flag=flag,
                    ))
        return markers
