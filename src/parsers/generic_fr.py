"""
Generic French fallback parser for Quebec/French-Canadian lab reports.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from ..models import LabResult, Language, MarkerResult, Patient
from .base_parser import BaseParser


class GenericFrParser(BaseParser):
    province = None
    language = "fr"
    lab_name_hint = "Generic FR"

    _MARKER_LINE_RE = re.compile(
        r"^(?P<name>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\(\),\-\/\.]{2,50}?)"
        r"[\s\.:\|]{1,10}"
        r"(?P<value>[<>]?\s*\d+[.,]?\d*)"
        r"\s*(?P<flag>[HhLlÉÈ\*]+)?"
        r"\s*(?P<unit>[A-Za-zÀ-ÿ/%µ][A-Za-z0-9/\.\^\%µ]*)?"
        r"(?:\s+(?P<ref>[\d.,<>\-]+\s*[-–]\s*[\d.,<>]+))?$",
        re.UNICODE,
    )

    _NAME_RE = re.compile(
        r"(?:Patient|Nom\s+du\s+patient|Nom)\s*[:\-]\s*([A-Za-zÀ-ÿ,\s\-'\.]{3,50})",
        re.IGNORECASE | re.UNICODE,
    )
    _DOB_RE = re.compile(
        r"(?:DDN|Date de naissance|Né\(e\) le|D\.N\.)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})",
        re.IGNORECASE,
    )
    _COLLECT_DATE_RE = re.compile(
        r"(?:Prélèvement|Date de prélèvement|Date de collecte)\s*[:\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})",
        re.IGNORECASE,
    )
    _SKIP_PATTERNS = re.compile(
        r"^\s*(?:page|plage de référence|normale?|unité|analyse|résultat|"
        r"drapeau|laboratoire|rapport|confidentiel|imprimé|téléc|tél)\s*$",
        re.IGNORECASE,
    )

    def can_parse(self, text: str) -> bool:
        text_lower = text.lower()
        fr_indicators = ["résultat", "analyse", "prélèvement", "médecin", "prénom"]
        return sum(1 for kw in fr_indicators if kw in text_lower) >= 2

    def parse(self, text: str, source_file: str) -> LabResult:
        patient = self._extract_patient(text)
        collection_date = self._extract_collection_date(text)
        markers = self._extract_markers(text)

        result = LabResult(
            source_file=source_file,
            patient=patient,
            collection_date=collection_date,
            lab_name="Inconnu (générique FR)",
            language=Language.FR,
            markers=markers,
        )
        if not markers:
            result.parse_warnings.append(
                "Analyseur générique FR : aucun marqueur trouvé — "
                "ce format nécessite peut-être un analyseur personnalisé."
            )
        return result

    def _extract_patient(self, text: str) -> Patient:
        full_name = "INCONNU"
        dob = None

        m = self._NAME_RE.search(text)
        if m:
            raw = m.group(1).strip().rstrip(",")
            if "," in raw:
                parts = [p.strip() for p in raw.split(",", 1)]
                full_name = f"{parts[1]} {parts[0]}"
            else:
                full_name = self._normalise_name(raw)

        m = self._DOB_RE.search(text)
        if m:
            dob = self._parse_date(m.group(1))

        return Patient(full_name=full_name, date_of_birth=dob)

    def _extract_collection_date(self, text: str) -> Optional[date]:
        m = self._COLLECT_DATE_RE.search(text)
        if m:
            return self._parse_date(m.group(1))
        return None

    def _extract_markers(self, text: str) -> list[MarkerResult]:
        markers = []
        seen_names: set[str] = set()

        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if self._SKIP_PATTERNS.match(line):
                continue
            m = self._MARKER_LINE_RE.match(line)
            if m:
                name = self._normalise_name(m.group("name"))
                if not name or name.lower() in seen_names:
                    continue
                value = m.group("value").replace(" ", "").replace(",", ".")
                unit = m.group("unit") or None
                ref = (m.group("ref") or "").strip() or None
                flag = (m.group("flag") or "").upper() or None
                if name and value:
                    seen_names.add(name.lower())
                    markers.append(MarkerResult(
                        raw_name=name,
                        value=value,
                        unit=unit,
                        reference_range=ref,
                        flag=flag,
                    ))
        return markers
