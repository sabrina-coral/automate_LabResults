"""
Basic tests for the parser pipeline.
Run with: python -m pytest tests/ -v
"""
import pytest
from src.parsers.generic_en import GenericEnParser
from src.parsers.generic_fr import GenericFrParser
from src.parsers.qc_fr_biron import BironQCParser
from src.parsers.parser_factory import get_parser
from src.marker_matcher import MarkerMatcher
from src.patient_matcher import match_patient
from src.models import CoralMember, MatchStatus
from datetime import date


# ── Sample lab report text ────────────────────────────────────────────────────

SAMPLE_EN = """
DYNACARE LABORATORY SERVICES
Patient Name: DOE, John
Date of Birth: 1980-05-15
Health Card: 1234 567 890 AB
Requisition: REQ12345
Collection Date: 2024-03-01

TEST                         RESULT    UNITS      REFERENCE
Hemoglobin                   142       g/L        120-160
WBC                          7.2       10E9/L     4.0-11.0
Platelets                    250       10E9/L     150-400
Glucose, Fasting             5.1       mmol/L     3.9-6.1
TSH                          2.1       mIU/L      0.4-4.0
"""

SAMPLE_FR = """
Biron Groupe Santé
Nom: TREMBLAY
Prénom: Marie
Date de naissance: 1975-08-22
NAM: 1234 5678 9012
Dossier: DOS98765
Date de prélèvement: 2024-03-10

Analyse                      Valeur    Unité    Valeurs de référence
Hémoglobine                  135       g/L      120-160
Leucocytes                   6.8       10E9/L   4.0-11.0
Plaquettes                   210       10E9/L   150-400
Glucose à jeun               4.9       mmol/L   3.9-6.1
TSH                          1.8       mIU/L    0.4-4.0
"""


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_generic_en_parser_detects_markers():
    parser = GenericEnParser()
    assert parser.can_parse(SAMPLE_EN)
    result = parser.parse(SAMPLE_EN, source_file="test.pdf")
    assert len(result.markers) >= 3
    names = [m.raw_name.lower() for m in result.markers]
    assert any("hemoglobin" in n for n in names)


def test_biron_parser_selected_for_biron_text():
    parser = get_parser(SAMPLE_FR)
    assert isinstance(parser, BironQCParser)


def test_generic_fr_parser_detects_markers():
    parser = GenericFrParser()
    assert parser.can_parse(SAMPLE_FR)
    result = parser.parse(SAMPLE_FR, source_file="test.pdf")
    assert len(result.markers) >= 3


def test_biron_parser_extracts_patient():
    parser = BironQCParser()
    result = parser.parse(SAMPLE_FR, source_file="test.pdf")
    assert "tremblay" in result.patient.full_name.lower() or "marie" in result.patient.full_name.lower()
    assert result.patient.date_of_birth == date(1975, 8, 22)
    assert result.patient.health_card_number is not None


def test_generic_en_parser_extracts_patient():
    parser = GenericEnParser()
    result = parser.parse(SAMPLE_EN, source_file="test.pdf")
    assert "doe" in result.patient.full_name.lower() or "john" in result.patient.full_name.lower()


def test_marker_matcher_exact():
    matcher = MarkerMatcher(coral_fields=["Hemoglobin", "WBC", "TSH", "Glucose"])
    result = matcher.match("Hemoglobin")
    assert result.status == MatchStatus.EXACT
    assert result.coral_field == "Hemoglobin"


def test_marker_matcher_fuzzy_french():
    matcher = MarkerMatcher(coral_fields=["Hemoglobin", "WBC", "TSH"])
    result = matcher.match("Hémoglobine")
    # Should fuzzy-match to Hemoglobin (after accent normalization)
    assert result.status in (MatchStatus.EXACT, MatchStatus.FUZZY)
    assert "hemoglobin" in (result.coral_field or "").lower()


def test_patient_matcher_exact_health_card():
    from src.models import Patient
    patient = Patient(full_name="John Doe", health_card_number="1234567890AB")
    members = [
        CoralMember(member_id="1", full_name="John Doe", health_card_number="1234567890AB"),
        CoralMember(member_id="2", full_name="Jane Smith", health_card_number="9999999999"),
    ]
    result = match_patient(patient, members)
    assert result.member is not None
    assert result.member.member_id == "1"
    assert result.match_method == "health_card"
    assert result.confidence == 1.0


def test_patient_matcher_no_match():
    from src.models import Patient
    patient = Patient(full_name="Unknown Person")
    members = [
        CoralMember(member_id="1", full_name="John Doe"),
    ]
    result = match_patient(patient, members)
    assert result.member is None or result.confidence < 0.5
