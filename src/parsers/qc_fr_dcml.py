"""
Parser for DCML Montérégie (Direction des services de laboratoire de la Montérégie)
lab reports — French Quebec public lab.

Format characteristics:
- Each result line ends with a validation timestamp: YYYY/MM/DD HH:MM <tech> <site>
- Columns: Examen(s) | Résultat(s) | [Flag] | [Val. Réf.] | Unités | D/H Validation ...
- Decimal separator: comma (e.g. 3,7 instead of 3.7) — mixed with dots in ref ranges
- Patient name appears after "Requérant: DOCTOR_NAME (ID) PATIENT_NAME"
- Collection date: "Prélèvement: YYYY/MM/DD"
- RAMQ pattern: AAAAMMJJNN (4 letters + 8 digits)
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, Patient
from .base_parser import BaseParser


class DCMLParser(BaseParser):
    province = "QC"
    language = "fr"
    lab_name_hint = "DCML Montérégie"

    # A valid result line ends with a date YYYY/MM/DD (validation timestamp).
    # Line structure: NAME VALUE [FLAG] [REF] UNIT DATE ...
    _RESULT_RE = re.compile(
        r"^(?P<name>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\(\)/\-\.]*?)"
        r"\s+"
        r"(?P<value>[<>]?\s*[\d,\.]+)"
        r"(?:\s+(?P<flag>[A-Z]{1,3}))?"          # optional flag: AH, H, BL, etc.
        r"(?:\s+(?P<ref>[\d.,]+\s*[-–]\s*[\d.,]+))?"  # optional reference range
        r"\s+(?P<unit>\S+)"
        r"\s+\d{4}/\d{2}/\d{2}",                  # anchor: validation date
        re.UNICODE,
    )

    # Collection date: "Prélèvement: 2026/02/20 10:00"
    _COLLECT_DATE_RE = re.compile(
        r"Prélèvement\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",
        re.IGNORECASE,
    )
    # DOB from context "1979/05/18 F (âge: 46)"
    _DOB_RE = re.compile(
        r"(\d{4}/\d{2}/\d{2})\s+[FMH]\s*\(?âge",
        re.IGNORECASE,
    )
    # Quebec RAMQ: 4 uppercase letters + 8 digits
    _RAMQ_RE = re.compile(r"\bRAMQ\s*[:\-]?\s*([A-Z]{4}\d{8})\b", re.IGNORECASE)
    # Patient name from "Requérant: DOCTOR (ID) PATIENT"
    _PATIENT_NAME_RE = re.compile(
        r"Requérant\s*:.*?\(\d+\)\s+"
        r"([A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ][A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ\-]+,"
        r"\s*[A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ\-]+)",
        re.UNICODE | re.IGNORECASE,
    )

    # Lines to always skip
    _SKIP_RE = re.compile(
        r"^\s*(?:BIOCHIMIE|HÉMATOLOG|ENDOCRIN|IMMUNOLOG|VITAMINES|"
        r"SPÉCIMEN|Examen\(s\)|Résultat\(s\)|Val\.\s+Réf\.|"
        r"D/H Validation|Commentaire|HEURE DU|Feb\s+\d+|Destinataire|"
        r"Santé|Services sociaux|Laboratoire serveur|QuébecE|"
        r"Téléphone|Téléc|No\s+Requête|No\s+Dossier|No\s+Séjour|"
        r"Prélèvement|Requérant|Provenance|D/H Commandée|FINAL|Mère|"
        r"Estimé selon|Selon l'étude)",
        re.IGNORECASE | re.UNICODE,
    )

    def can_parse(self, text: str) -> bool:
        text_lower = text.lower()
        return (
            "dcml" in text_lower
            or "montérégie" in text_lower
            or "laboratoire serveur de la mont" in text_lower
        )

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        return LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="DCML Montérégie",
            province="QC",
            language=Language.FR,
            markers=markers,
        )

    def _extract_patient(self, text: str) -> Patient:
        full_name = "INCONNU"
        dob = None
        ramq = None

        # RAMQ
        m = self._RAMQ_RE.search(text)
        if m:
            ramq = m.group(1)

        # DOB
        m = self._DOB_RE.search(text)
        if m:
            dob = self._parse_date(m.group(1).replace("/", "-"))

        # Patient name: second name in "Requérant: DOCTOR (ID) PATIENT"
        m = self._PATIENT_NAME_RE.search(text)
        if m:
            raw = m.group(1).strip()
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
            return self._parse_date(m.group(1).replace("/", "-"))
        return None

    def _extract_markers(self, text: str) -> list[MarkerResult]:
        markers: list[MarkerResult] = []
        seen: set[str] = set()

        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 8:
                continue
            if self._SKIP_RE.match(line):
                continue
            # Quick pre-filter: line must contain a date stamp to be a result line
            if not re.search(r"\d{4}/\d{2}/\d{2}", line):
                continue

            m = self._RESULT_RE.match(line)
            if not m:
                continue

            raw_name = m.group("name").strip()
            if len(raw_name) < 2:
                continue
            norm = raw_name.lower()
            if norm in seen:
                continue

            # Normalise value: replace comma decimal separator → dot
            value = m.group("value").replace(" ", "").replace(",", ".")
            unit = (m.group("unit") or "").strip()
            ref_raw = m.group("ref") or ""
            ref = ref_raw.replace(",", ".").strip() or None

            # Skip lines where unit looks like a date or timestamp (false positive)
            if re.match(r"^\d{4}/\d{2}/\d{2}$", unit):
                continue

            seen.add(norm)
            markers.append(MarkerResult(
                raw_name=raw_name,
                value=value,
                unit=unit or None,
                reference_range=ref,
                flag=(m.group("flag") or "").upper() or None,
            ))

        return markers
