"""
Patient matcher — matches a Patient extracted from a PDF against coral.app members.

Matching strategy (in order of confidence):
1. Health card number (exact) — most reliable
2. Full name + date of birth — high confidence
3. Full name only — medium confidence, may be ambiguous
4. Fuzzy full name + DOB — low confidence, always prompts for confirmation

Returns a MatchResult with:
- matched member (if found)
- confidence score (0.0 – 1.0)
- whether manual confirmation is required
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

try:
    from rapidfuzz import fuzz
    _FUZZY_AVAILABLE = True
except ImportError:
    _FUZZY_AVAILABLE = False

from .models import CoralMember, Patient


# Minimum fuzzy score (0-100) to accept a name match
_NAME_FUZZY_THRESHOLD = 82


@dataclass
class PatientMatchResult:
    patient: Patient
    member: Optional[CoralMember]
    confidence: float            # 0.0 – 1.0
    match_method: str            # "health_card", "name_dob", "name_only", "fuzzy", "none"
    needs_confirmation: bool     # always True if not exact health_card match
    alternatives: list[CoralMember]  # other near-matches for manual selection


def match_patient(
    patient: Patient,
    members: list[CoralMember],
    *,
    require_confirmation_below: float = 0.95,
) -> PatientMatchResult:
    """
    Find the best matching CoralMember for a Patient.

    Args:
        patient: Patient from the parsed PDF
        members: list of CoralMember records from coral.app
        require_confirmation_below: confidence threshold below which the user
                                    must confirm the match

    Returns:
        PatientMatchResult
    """
    if not members:
        return PatientMatchResult(
            patient=patient,
            member=None,
            confidence=0.0,
            match_method="none",
            needs_confirmation=True,
            alternatives=[],
        )

    # --- 1. Exact health card match ---
    if patient.health_card_number:
        for m in members:
            if (
                m.health_card_number
                and _norm_hc(m.health_card_number) == _norm_hc(patient.health_card_number)
            ):
                return PatientMatchResult(
                    patient=patient,
                    member=m,
                    confidence=1.0,
                    match_method="health_card",
                    needs_confirmation=False,
                    alternatives=[],
                )

    # --- 2. Exact name + DOB ---
    if patient.date_of_birth:
        dob_matches = [
            m for m in members
            if m.date_of_birth == patient.date_of_birth
        ]
        for m in dob_matches:
            if _norm_name(m.full_name) == _norm_name(patient.full_name):
                return PatientMatchResult(
                    patient=patient,
                    member=m,
                    confidence=0.97,
                    match_method="name_dob",
                    needs_confirmation=False,
                    alternatives=[],
                )

    # --- 3. Exact name only ---
    name_exact = [
        m for m in members
        if _norm_name(m.full_name) == _norm_name(patient.full_name)
    ]
    if len(name_exact) == 1:
        return PatientMatchResult(
            patient=patient,
            member=name_exact[0],
            confidence=0.80,
            match_method="name_only",
            needs_confirmation=True,
            alternatives=[],
        )

    # --- 4. Fuzzy name matching ---
    if _FUZZY_AVAILABLE:
        scored = _fuzzy_score_members(patient.full_name, members)
        if scored:
            best_score, best_member = scored[0]
            if best_score >= _NAME_FUZZY_THRESHOLD:
                alternatives = [m for _, m in scored[1:4]]
                confidence = best_score / 100.0 * 0.75  # cap at 0.75 for fuzzy
                return PatientMatchResult(
                    patient=patient,
                    member=best_member,
                    confidence=confidence,
                    match_method="fuzzy",
                    needs_confirmation=True,
                    alternatives=alternatives,
                )

    # --- No match ---
    return PatientMatchResult(
        patient=patient,
        member=None,
        confidence=0.0,
        match_method="none",
        needs_confirmation=True,
        alternatives=[],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    """Normalise a name for comparison: lowercase, strip accents, collapse spaces."""
    name = name.strip().lower()
    # Remove accents
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    # Collapse whitespace and punctuation
    name = re.sub(r"[^a-z\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _norm_hc(hc: str) -> str:
    """Normalise health card number: strip spaces, dashes, uppercase."""
    return re.sub(r"[\s\-]", "", hc).upper()


def _fuzzy_score_members(
    patient_name: str,
    members: list[CoralMember],
) -> list[tuple[int, CoralMember]]:
    """Return members sorted by fuzzy name similarity score (descending)."""
    norm_patient = _norm_name(patient_name)
    scored = []
    for m in members:
        score = fuzz.token_sort_ratio(norm_patient, _norm_name(m.full_name))
        scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored
