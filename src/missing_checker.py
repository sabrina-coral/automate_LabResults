"""
Missing marker checker.

Compares extracted markers against:
1. The panel definition (expected markers for a given panel type)
2. The list of known coral.app fields

Produces a MissingMarkerReport for each LabResult.
"""
from __future__ import annotations

import unicodedata
import re
from pathlib import Path
from typing import Optional

import yaml

from .models import LabResult, MissingMarkerReport


class MissingChecker:
    """
    Checks a LabResult against panel definitions and coral.app fields.

    Usage:
        checker = MissingChecker(
            panels_path="config/panels.yaml",
            coral_fields=["Hemoglobin", "WBC", ...],
        )
        report = checker.check(lab_result, panel_name="CBC")
    """

    def __init__(
        self,
        panels_path: str | Path,
        coral_fields: list[str],
    ):
        self.panels: dict[str, list[str]] = {}
        self.coral_fields_norm: set[str] = {_norm(f) for f in coral_fields}
        self._load_panels(Path(panels_path))

    def check(
        self,
        lab_result: LabResult,
        panel_name: Optional[str] = None,
    ) -> MissingMarkerReport:
        """
        Build a MissingMarkerReport for the given LabResult.

        Args:
            lab_result: the parsed lab result
            panel_name: optional panel name to check against; if None,
                        tries to auto-detect from markers
        """
        report = MissingMarkerReport(
            lab_result_file=lab_result.source_file,
            patient_name=lab_result.patient.full_name,
        )

        # Build a set of what was found, using matched coral field names where available,
        # falling back to raw names. This ensures panel comparison works across languages.
        found_names_norm: set[str] = set()
        for m in lab_result.markers:
            found_names_norm.add(_norm(m.raw_name))
            if m.coral_field_name:
                found_names_norm.add(_norm(m.coral_field_name))

        # --- 1. Markers in PDF not found in coral.app ---
        report.missing_from_coral = [
            m.raw_name
            for m in lab_result.markers
            if m.coral_field_name is None
        ]

        # --- 2. Panel completeness check ---
        resolved_panel = panel_name or self._detect_panel(lab_result)
        if resolved_panel and resolved_panel in self.panels:
            expected = self.panels[resolved_panel]
            report.missing_from_panel = [
                expected_marker
                for expected_marker in expected
                if _norm(expected_marker) not in found_names_norm
            ]

        # --- 3. Markers not in any panel definition ---
        all_panel_markers_norm: set[str] = set()
        for markers in self.panels.values():
            all_panel_markers_norm.update(_norm(m) for m in markers)

        report.extra_markers = [
            m.raw_name
            for m in lab_result.markers
            if _norm(m.raw_name) not in all_panel_markers_norm
            and (m.coral_field_name is None or _norm(m.coral_field_name) not in all_panel_markers_norm)
        ]

        return report

    def get_panel_names(self) -> list[str]:
        """Return all defined panel names."""
        return list(self.panels.keys())

    def get_panel_markers(self, panel_name: str) -> list[str]:
        """Return expected markers for a given panel."""
        return self.panels.get(panel_name, [])

    # ------------------------------------------------------------------ #

    def _load_panels(self, path: Path) -> None:
        if not path.exists():
            return
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for panel_name, panel_data in data.get("panels", {}).items():
            markers = panel_data.get("markers", [])
            if isinstance(markers, list):
                self.panels[panel_name] = markers

    def _detect_panel(self, lab_result: LabResult) -> Optional[str]:
        """
        Try to identify which panel was ordered by counting overlap
        between found markers and each panel definition.
        Uses coral field names (post-matching) for reliable cross-language comparison.
        """
        if not self.panels:
            return None

        # Use both raw names and matched coral field names
        found_norm: set[str] = set()
        for m in lab_result.markers:
            found_norm.add(_norm(m.raw_name))
            if m.coral_field_name:
                found_norm.add(_norm(m.coral_field_name))

        best_panel = None
        best_overlap = 0

        # Check more specific panels first (PANEL_AB should rank below PANEL_A+PANEL_B individually)
        for panel_name, expected in self.panels.items():
            expected_norm = {_norm(e) for e in expected}
            overlap = len(found_norm & expected_norm)
            # Require at least 60% of expected markers to claim a match
            threshold = len(expected_norm) * 0.60
            if overlap > best_overlap and overlap >= threshold:
                best_overlap = overlap
                best_panel = panel_name

        return best_panel


def _norm(name: str) -> str:
    name = name.strip().lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name
