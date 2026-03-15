"""
Core data models for the lab results automation pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class MatchStatus(Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class Language(Enum):
    EN = "en"
    FR = "fr"
    UNKNOWN = "unknown"


@dataclass
class Patient:
    """Patient info extracted from a lab result PDF."""
    full_name: str
    date_of_birth: Optional[date] = None
    health_card_number: Optional[str] = None
    requisition_number: Optional[str] = None
    province: Optional[str] = None
    raw_id_line: Optional[str] = None  # original text line for manual review


@dataclass
class MarkerResult:
    """A single lab test marker with its result."""
    # Raw values from PDF
    raw_name: str           # e.g. "Hémoglobine" or "Hemoglobin"
    value: Optional[str] = None  # numeric string, e.g. "142"
    unit: Optional[str] = None   # e.g. "g/L"
    reference_range: Optional[str] = None  # e.g. "120-160"
    flag: Optional[str] = None   # "H", "L", "*", "ÉLEVÉ", etc.
    # Resolved values after matching
    coral_field_name: Optional[str] = None  # matched name in coral.app
    match_status: MatchStatus = MatchStatus.NOT_FOUND
    match_score: float = 0.0
    # Panel membership
    in_expected_panel: bool = False
    panel_name: Optional[str] = None

    @property
    def is_numeric(self) -> bool:
        if self.value is None:
            return False
        try:
            float(self.value.replace(",", "."))
            return True
        except ValueError:
            return False

    @property
    def numeric_value(self) -> Optional[float]:
        if not self.is_numeric:
            return None
        return float(self.value.replace(",", "."))


@dataclass
class LabResult:
    """Complete parsed result from one lab report PDF."""
    source_file: str
    patient: Patient
    collection_date: Optional[date] = None
    report_date: Optional[date] = None
    lab_name: Optional[str] = None
    province: Optional[str] = None
    language: Language = Language.UNKNOWN
    markers: list[MarkerResult] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def missing_markers(self) -> list[str]:
        """Markers expected for the panel but not found in the PDF."""
        return [m.raw_name for m in self.markers if not m.in_expected_panel]

    @property
    def unmatched_markers(self) -> list[MarkerResult]:
        """Markers found in PDF but not matched to any coral.app field."""
        return [m for m in self.markers if m.match_status == MatchStatus.NOT_FOUND]

    @property
    def ready_to_submit(self) -> bool:
        """True only when patient is matched and all markers have coral fields."""
        return all(
            m.match_status in (MatchStatus.EXACT, MatchStatus.FUZZY)
            for m in self.markers
            if m.is_numeric
        )


@dataclass
class CoralMember:
    """A member record from coral.app."""
    member_id: str
    full_name: str
    date_of_birth: Optional[date] = None
    health_card_number: Optional[str] = None
    profile_url: Optional[str] = None


@dataclass
class MissingMarkerReport:
    """Report of markers present in PDF but absent from coral.app fields."""
    lab_result_file: str
    patient_name: str
    missing_from_coral: list[str] = field(default_factory=list)
    missing_from_panel: list[str] = field(default_factory=list)   # expected in panel, not in PDF
    extra_markers: list[str] = field(default_factory=list)         # in PDF, not in any panel def

    def has_issues(self) -> bool:
        return bool(self.missing_from_coral or self.missing_from_panel or self.extra_markers)
