"""
Marker matcher — maps raw marker names from lab PDFs to coral.app field names.

Handles:
- Bilingual aliases (French ↔ English)
- Common abbreviation variants
- Fuzzy matching for typos / OCR noise
- Configurable synonym dictionary (config/marker_aliases.yaml)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

try:
    from rapidfuzz import fuzz, process
    _FUZZY_AVAILABLE = True
except ImportError:
    _FUZZY_AVAILABLE = False

from .models import MarkerResult, MatchStatus


# Minimum fuzzy score to accept a match automatically
_AUTO_ACCEPT_THRESHOLD = 88
# Minimum score to show as a candidate (for user selection)
_CANDIDATE_THRESHOLD = 65


@dataclass
class MarkerMatchResult:
    raw_name: str
    coral_field: Optional[str]
    score: float          # 0.0 – 1.0
    status: MatchStatus
    candidates: list[tuple[str, float]] = field(default_factory=list)  # (field_name, score)


class MarkerMatcher:
    """
    Matches raw marker names to coral.app field names.

    Usage:
        matcher = MarkerMatcher(coral_fields, aliases_path="config/marker_aliases.yaml")
        result = matcher.match("Hémoglobine")
        # result.coral_field  -> "Hemoglobin"
        # result.status       -> MatchStatus.EXACT or MatchStatus.FUZZY
    """

    def __init__(
        self,
        coral_fields: list[str],
        aliases_path: Optional[str | Path] = None,
    ):
        """
        Args:
            coral_fields: list of field names as they appear in coral.app
            aliases_path: path to YAML file with synonym/alias mappings
        """
        self.coral_fields = coral_fields
        self._aliases: dict[str, str] = {}  # normalised alias -> coral field name
        self._norm_to_field: dict[str, str] = {}  # normalised coral name -> original

        # Build lookup from coral field names
        for f in coral_fields:
            self._norm_to_field[_norm(f)] = f

        # Load aliases file if provided
        if aliases_path:
            self._load_aliases(Path(aliases_path))

        # Build combined lookup
        self._all_normalised: dict[str, str] = {**self._norm_to_field, **self._aliases}

    def match(self, raw_name: str) -> MarkerMatchResult:
        """Match a single raw marker name to a coral.app field."""
        norm = _norm(raw_name)

        # 1. Exact match on normalised name
        if norm in self._all_normalised:
            coral_field = self._all_normalised[norm]
            return MarkerMatchResult(
                raw_name=raw_name,
                coral_field=coral_field,
                score=1.0,
                status=MatchStatus.EXACT,
            )

        # 2. Fuzzy match
        if not _FUZZY_AVAILABLE or not self._all_normalised:
            return MarkerMatchResult(
                raw_name=raw_name,
                coral_field=None,
                score=0.0,
                status=MatchStatus.NOT_FOUND,
            )

        choices = list(self._all_normalised.keys())
        results = process.extract(
            norm,
            choices,
            scorer=fuzz.token_sort_ratio,
            limit=5,
        )

        if not results:
            return MarkerMatchResult(
                raw_name=raw_name,
                coral_field=None,
                score=0.0,
                status=MatchStatus.NOT_FOUND,
            )

        best_norm, best_score, _ = results[0]
        best_field = self._all_normalised[best_norm]

        candidates = [
            (self._all_normalised[norm_r], score / 100.0)
            for norm_r, score, _ in results
            if score >= _CANDIDATE_THRESHOLD
        ]

        if best_score >= _AUTO_ACCEPT_THRESHOLD:
            # Check for ambiguity — two candidates very close to each other
            if len(results) > 1 and (results[0][1] - results[1][1]) < 5:
                return MarkerMatchResult(
                    raw_name=raw_name,
                    coral_field=best_field,
                    score=best_score / 100.0,
                    status=MatchStatus.AMBIGUOUS,
                    candidates=candidates,
                )
            return MarkerMatchResult(
                raw_name=raw_name,
                coral_field=best_field,
                score=best_score / 100.0,
                status=MatchStatus.FUZZY,
                candidates=candidates,
            )

        return MarkerMatchResult(
            raw_name=raw_name,
            coral_field=None,
            score=best_score / 100.0,
            status=MatchStatus.NOT_FOUND,
            candidates=candidates,
        )

    def match_all(self, markers: list[MarkerResult]) -> list[MarkerResult]:
        """
        Match all markers in a LabResult and update them in-place.
        Returns the updated list.
        """
        for marker in markers:
            result = self.match(marker.raw_name)
            marker.coral_field_name = result.coral_field
            marker.match_status = result.status
            marker.match_score = result.score
        return markers

    def add_alias(self, raw_name: str, coral_field: str) -> None:
        """Dynamically register a new alias (e.g. after user confirms a fuzzy match)."""
        self._aliases[_norm(raw_name)] = coral_field
        self._all_normalised[_norm(raw_name)] = coral_field

    def save_aliases(self, path: str | Path) -> None:
        """Persist current aliases back to the YAML file."""
        path = Path(path)
        # Load existing file to merge
        existing: dict = {}
        if path.exists():
            with open(path) as f:
                existing = yaml.safe_load(f) or {}

        # Merge in new aliases (denormalised form not preserved, so we store raw)
        existing.setdefault("aliases", {})
        for norm_alias, coral_field in self._aliases.items():
            existing["aliases"][norm_alias] = coral_field

        with open(path, "w") as f:
            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False)

    def _load_aliases(self, path: Path) -> None:
        if not path.exists():
            return
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for alias, coral_field in data.get("aliases", {}).items():
            self._aliases[_norm(alias)] = coral_field
        # Also load per-language synonym lists
        for lang_block in ["en_synonyms", "fr_synonyms"]:
            for coral_field, synonyms in data.get(lang_block, {}).items():
                if isinstance(synonyms, list):
                    for s in synonyms:
                        self._aliases[_norm(s)] = coral_field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    """
    Normalise a marker name for comparison.
    - lowercase
    - remove accents
    - remove punctuation / special chars
    - collapse whitespace
    """
    name = name.strip().lower()
    # Remove accents
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    # Collapse non-alphanumeric to space
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name
