"""
Parser factory — auto-selects the right parser based on text content.

Usage:
    parser = get_parser(text)
    result = parser.parse(text, source_file="path/to/file.pdf")
"""
from __future__ import annotations

from ..models import Language
from .base_parser import BaseParser

# Import all available parsers
from .qc_fr_biron import BironQCParser
from .qc_fr_dcml import DCMLParser
from .qc_fr_dynacare import DynacareQCFrParser
from .qc_fr_mdl import MDLParser
from .on_en_dynacare import DynacareParser
from .on_en_lifelabs import LifeLabsParser
from .generic_en import GenericEnParser
from .generic_fr import GenericFrParser

# Ordered list — more specific parsers first, generic fallbacks last
_PARSER_REGISTRY: list[BaseParser] = [
    BironQCParser(),
    DCMLParser(),           # DCML Montérégie (QC public lab) — before generic FR
    DynacareQCFrParser(),   # Dynacare QC French — before the English Dynacare parser
    MDLParser(),
    DynacareParser(),
    LifeLabsParser(),
    GenericFrParser(),      # French fallback
    GenericEnParser(),      # English fallback
]


def get_parser(text: str) -> BaseParser:
    """
    Return the best parser for the given text.
    Falls back to GenericEnParser if nothing matches.
    """
    for parser in _PARSER_REGISTRY:
        if parser.can_parse(text):
            return parser
    # Should never reach here because GenericEnParser always returns True
    return GenericEnParser()


def list_parsers() -> list[dict]:
    """Return metadata about all registered parsers."""
    return [
        {
            "class": type(p).__name__,
            "province": p.province,
            "language": p.language,
            "lab": p.lab_name_hint,
        }
        for p in _PARSER_REGISTRY
    ]
