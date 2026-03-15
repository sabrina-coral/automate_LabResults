#!/usr/bin/env python3
"""
Lab Results Automation — Main CLI
===================================
Processes scanned PDF lab results and enters values into coralhealth.app.

Usage:
    python main.py path/to/result.pdf
    python main.py path/to/results/folder/
    python main.py path/to/result.pdf --dry-run
    python main.py path/to/result.pdf --panel CBC
    python main.py path/to/result.pdf --report-only   # only generate missing markers report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

# ── Optional rich console for pretty output ──────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich import print as rprint
    console = Console()
    RICH = True
except ImportError:
    console = None
    RICH = False
    def rprint(*args, **kwargs):
        print(*args)

# ── Local imports ─────────────────────────────────────────────────────────────
from src.pdf_processor import extract_text, detect_language, detect_province
from src.parsers import get_parser
from src.patient_matcher import match_patient, PatientMatchResult
from src.marker_matcher import MarkerMatcher
from src.missing_checker import MissingChecker
from src.coral_automator import CoralAutomator, EntryItem
from src.models import LabResult, MatchStatus


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_settings(path: str = "config/settings.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_tests_and_index(tests_path: str) -> tuple[list[list[str]], dict[str, int]]:
    """
    Load tests.txt (compatible with colleague's format).
    Returns (tests, testindexes) where testindexes maps alias → tab position.
    """
    with open(tests_path, encoding="utf-8") as f:
        raw = f.read().split("\n")
    tests = []
    for line in raw:
        if line.strip():
            aliases = [a.strip().lower() for a in line.split(",") if a.strip()]
            tests.append(aliases)
        else:
            tests.append([])

    testindexes: dict[str, int] = {}
    for i, aliases in enumerate(tests):
        for alias in aliases:
            testindexes[alias] = i

    return tests, testindexes


def load_units(units_path: str) -> list[list[str]]:
    with open(units_path, encoding="utf-8") as f:
        raw = f.read().split("\n")
    units = []
    for line in raw:
        if line.strip():
            units.append([u.strip().lower() for u in line.split(",") if u.strip()])
        else:
            units.append([])
    return units


def build_coral_fields_from_tests(tests: list[list[str]]) -> list[str]:
    """Return the primary (first) name for each test as coral field names."""
    fields = []
    for aliases in tests:
        if aliases:
            fields.append(aliases[0])
    return fields


def print_lab_result_summary(lab_result: LabResult) -> None:
    """Print a human-readable summary of what was parsed."""
    print()
    print("=" * 60)
    print(f"  FILE    : {Path(lab_result.source_file).name}")
    print(f"  PATIENT : {lab_result.patient.full_name}")
    if lab_result.patient.date_of_birth:
        print(f"  DOB     : {lab_result.patient.date_of_birth}")
    if lab_result.patient.health_card_number:
        print(f"  HC#     : {lab_result.patient.health_card_number}")
    print(f"  LAB     : {lab_result.lab_name or 'Unknown'}")
    print(f"  PROVINCE: {lab_result.province or 'Unknown'}")
    print(f"  LANGUAGE: {lab_result.language.value}")
    if lab_result.collection_date:
        print(f"  DATE    : {lab_result.collection_date}")
    print(f"  MARKERS : {len(lab_result.markers)} found")
    print("=" * 60)

    if lab_result.parse_warnings:
        for w in lab_result.parse_warnings:
            print(f"  ⚠ WARNING: {w}")

    print()
    print(f"  {'MARKER':<40} {'VALUE':>10}  {'UNIT':<12}  {'MATCH'}")
    print(f"  {'-'*40} {'-'*10}  {'-'*12}  {'-'*20}")
    for m in lab_result.markers:
        status_str = {
            MatchStatus.EXACT: "✓ exact",
            MatchStatus.FUZZY: f"~ fuzzy → {m.coral_field_name}",
            MatchStatus.NOT_FOUND: "✗ NOT FOUND",
            MatchStatus.AMBIGUOUS: f"? ambiguous → {m.coral_field_name}",
        }[m.match_status]
        value_str = f"{m.value}" + (f" {m.flag}" if m.flag else "")
        print(f"  {m.raw_name:<40} {value_str:>10}  {(m.unit or ''):12}  {status_str}")
    print()


def print_missing_report(report) -> None:
    print("\n  ── Missing Marker Report ──────────────────────────────")
    if report.missing_from_coral:
        print(f"\n  Markers found in PDF but NOT in coral.app ({len(report.missing_from_coral)}):")
        for name in report.missing_from_coral:
            print(f"    • {name}")
    if report.missing_from_panel:
        print(f"\n  Markers expected in panel but NOT in PDF ({len(report.missing_from_panel)}):")
        for name in report.missing_from_panel:
            print(f"    • {name}")
    if report.extra_markers:
        print(f"\n  Extra markers not in any panel definition ({len(report.extra_markers)}):")
        for name in report.extra_markers:
            print(f"    • {name}")
    if not report.has_issues():
        print("  All markers accounted for. No issues.")
    print()


def save_missing_report(report, output_dir: str = "reports") -> str:
    """Save the missing marker report to a JSON file."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = (
        f"{Path(report.lab_result_file).stem}_"
        f"{report.patient_name.replace(' ', '_')}_"
        f"{date.today().isoformat()}_missing.json"
    )
    filepath = Path(output_dir) / filename
    data = {
        "lab_result_file": report.lab_result_file,
        "patient_name": report.patient_name,
        "date": date.today().isoformat(),
        "missing_from_coral": report.missing_from_coral,
        "missing_from_panel": report.missing_from_panel,
        "extra_markers": report.extra_markers,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return str(filepath)


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(
    pdf_path: str,
    settings: dict,
    tests: list[list[str]],
    testindexes: dict[str, int],
    units: list[list[str]],
    coral_fields: list[str],
    dry_run: bool = True,
    report_only: bool = False,
    panel_hint: str | None = None,
) -> None:
    """Process a single PDF through the full pipeline."""

    print(f"\n{'─'*60}")
    print(f"Processing: {pdf_path}")
    print("─" * 60)

    # ── Step 1: Extract text ──────────────────────────────────────────────────
    print("  [1/5] Extracting text from PDF...")
    try:
        text, used_ocr = extract_text(pdf_path)
    except Exception as exc:
        print(f"  ✗ Failed to extract text: {exc}")
        return

    if used_ocr:
        print("  → OCR used (scanned PDF detected)")
    else:
        print("  → Native text layer used")

    lang = detect_language(text)
    province = detect_province(text)
    print(f"  → Language: {lang.upper()}, Province: {province or 'unknown'}")

    # ── Step 2: Parse lab result ──────────────────────────────────────────────
    print("  [2/5] Parsing lab result...")
    parser = get_parser(text)
    print(f"  → Using parser: {type(parser).__name__}")
    lab_result = parser.parse(text, source_file=pdf_path)

    # ── Step 3: Match markers ─────────────────────────────────────────────────
    print("  [3/5] Matching markers to coral.app fields...")
    matcher = MarkerMatcher(
        coral_fields=coral_fields,
        aliases_path=settings.get("paths", {}).get("marker_aliases_file"),
    )
    matcher.match_all(lab_result.markers)

    matched_count = sum(
        1 for m in lab_result.markers
        if m.match_status in (MatchStatus.EXACT, MatchStatus.FUZZY)
    )
    print(f"  → {matched_count}/{len(lab_result.markers)} markers matched")

    # ── Step 4: Check missing markers ─────────────────────────────────────────
    print("  [4/5] Checking for missing markers...")
    checker = MissingChecker(
        panels_path=settings.get("paths", {}).get("panels_file", "config/panels.yaml"),
        coral_fields=coral_fields,
    )
    missing_report = checker.check(lab_result, panel_name=panel_hint)

    # ── Print summary ─────────────────────────────────────────────────────────
    print_lab_result_summary(lab_result)
    print_missing_report(missing_report)

    # Save report
    if missing_report.has_issues():
        report_path = save_missing_report(
            missing_report,
            output_dir=settings.get("paths", {}).get("reports_dir", "reports"),
        )
        print(f"  Missing marker report saved to: {report_path}")

    if report_only:
        print("  [report-only mode] Skipping coral.app entry.")
        return

    # ── Step 5: Enter into coral.app ─────────────────────────────────────────
    print("  [5/5] Preparing coral.app entry...")

    # Confirm with user before proceeding
    if not dry_run:
        answer = input("  → Proceed with entry? [y/N] ").strip().lower()
        if answer != "y":
            print("  Skipped.")
            return

    automator = CoralAutomator(settings)
    automator.set_test_index_map(testindexes)

    if dry_run:
        print("\n  [DRY RUN] Values that would be entered:")
        items_to_show = []
        for m in lab_result.markers:
            if m.match_status in (MatchStatus.EXACT, MatchStatus.FUZZY) and m.is_numeric:
                idx = testindexes.get((m.coral_field_name or m.raw_name).lower())
                if idx is not None:
                    items_to_show.append((idx, m.coral_field_name or m.raw_name, m.value))
        for idx, name, val in sorted(items_to_show):
            print(f"    [{idx:02d}] {name}: {val}")
        print(f"\n  → {len(items_to_show)} values would be entered.")
        return

    # Live mode
    try:
        automator.open_browser(headless=False)
        automator.login()
        member_url = automator.find_member(lab_result.patient.full_name)
        if not member_url:
            print(f"  ✗ Member not found: {lab_result.patient.full_name}")
            return
        print(f"  → Member found: {member_url}")
        automator.submit_lab_result(lab_result, member_url, dry_run=False)
    except Exception as exc:
        print(f"  ✗ Automation error: {exc}")
        raise
    finally:
        automator.close_browser()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automate lab result entry into coralhealth.app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py results/patient_abc.pdf
  python main.py results/patient_abc.pdf --dry-run
  python main.py results/patient_abc.pdf --panel CBC
  python main.py results/folder/ --report-only
        """,
    )
    parser.add_argument("input", help="PDF file or folder containing PDFs")
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Print what would be entered but don't touch coral.app (default: on)",
    )
    parser.add_argument(
        "--live", action="store_true", default=False,
        help="Actually enter data into coral.app (disables dry-run)",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Only parse PDFs and generate missing marker reports, no entry",
    )
    parser.add_argument(
        "--panel", type=str, default=None,
        help="Override panel detection (e.g. CBC, LIPID_PANEL, FULL_PANEL)",
    )
    parser.add_argument(
        "--settings", type=str, default="config/settings.yaml",
        help="Path to settings.yaml",
    )

    args = parser.parse_args()

    # Dry-run is default; --live overrides it
    dry_run = not args.live

    # Load config
    settings = load_settings(args.settings)
    tests_path = settings.get("paths", {}).get("tests_file", "config/tests.txt")
    units_path = settings.get("paths", {}).get("units_file", "config/units.txt")

    tests, testindexes = load_tests_and_index(tests_path)
    units = load_units(units_path)
    coral_fields = build_coral_fields_from_tests(tests)

    print(f"  Loaded {len(tests)} test definitions from {tests_path}")
    print(f"  Loaded {len(units)} unit definitions from {units_path}")

    if len(units) != len(tests):
        print(f"  WARNING: tests.txt has {len(tests)} entries but units.txt has {len(units)}. Check alignment.")

    # Collect PDF files
    input_path = Path(args.input)
    if input_path.is_dir():
        pdf_files = sorted(input_path.glob("*.pdf")) + sorted(input_path.glob("*.PDF"))
    elif input_path.is_file():
        pdf_files = [input_path]
    else:
        print(f"Error: '{args.input}' is not a file or directory.")
        sys.exit(1)

    if not pdf_files:
        print(f"No PDF files found in '{args.input}'")
        sys.exit(1)

    print(f"\nFound {len(pdf_files)} PDF file(s) to process.")
    if dry_run:
        print("Running in DRY-RUN mode. Use --live to actually enter data.\n")

    for pdf in pdf_files:
        process_pdf(
            pdf_path=str(pdf),
            settings=settings,
            tests=tests,
            testindexes=testindexes,
            units=units,
            coral_fields=coral_fields,
            dry_run=dry_run,
            report_only=args.report_only,
            panel_hint=args.panel,
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
