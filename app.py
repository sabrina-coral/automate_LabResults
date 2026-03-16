#!/usr/bin/env python3
"""
Lab Results Entry — Streamlit UI
==================================
Drop PDF lab results, preview parsed values, then enter them into coral.app.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import os
import tempfile

import pandas as pd
import streamlit as st

from main import (
    build_coral_fields_from_tests,
    load_settings,
    load_tests_and_index,
    load_units,
)
from src.coral_automator import CoralAutomator
from src.marker_matcher import MarkerMatcher
from src.missing_checker import MissingChecker
from src.models import MatchStatus
from src.parsers import get_parser
from src.pdf_processor import extract_text


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Lab Results Entry",
    page_icon="🧪",
    layout="wide",
)


# ── Config (loaded once, cached) ──────────────────────────────────────────────

@st.cache_resource
def get_config():
    settings = load_settings()
    paths = settings.get("paths", {})
    tests, testindexes = load_tests_and_index(paths.get("tests_file", "config/tests.txt"))
    units = load_units(paths.get("units_file", "config/units.txt"))
    coral_fields = build_coral_fields_from_tests(tests)
    return settings, tests, testindexes, units, coral_fields


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(pdf_path: str, settings: dict, tests, testindexes, coral_fields):
    """Parse a PDF and return (lab_result, report, entry_items, used_ocr)."""
    text, used_ocr = extract_text(pdf_path)

    parser = get_parser(text)
    lab_result = parser.parse(text, source_file=pdf_path)

    matcher = MarkerMatcher(
        coral_fields=coral_fields,
        aliases_path=settings.get("paths", {}).get("marker_aliases_file"),
    )
    for aliases in tests:
        if len(aliases) >= 2:
            primary = aliases[0]
            for alias in aliases[1:]:
                matcher.add_alias(alias, primary)
    matcher.match_all(lab_result.markers)

    checker = MissingChecker(
        panels_path=settings.get("paths", {}).get("panels_file", "config/panels.yaml"),
        coral_fields=coral_fields,
    )
    report = checker.check(lab_result)

    entry_items = []
    for m in lab_result.markers:
        if m.match_status in (MatchStatus.EXACT, MatchStatus.FUZZY) and m.is_numeric:
            idx = testindexes.get((m.coral_field_name or m.raw_name).lower())
            if idx is not None:
                entry_items.append((idx, m.coral_field_name or m.raw_name, m.value))
    entry_items.sort()

    return lab_result, report, entry_items, used_ocr


def build_markers_df(lab_result) -> pd.DataFrame:
    STATUS_EMOJI = {
        MatchStatus.EXACT:     "✅ exact",
        MatchStatus.FUZZY:     "🔶 fuzzy",
        MatchStatus.AMBIGUOUS: "❓ ambiguous",
        MatchStatus.NOT_FOUND: "❌ not found",
    }
    rows = []
    for m in lab_result.markers:
        rows.append({
            "Test (PDF name)":  m.raw_name,
            "Value":            m.value or "",
            "Unit":             m.unit or "",
            "coral.app field":  m.coral_field_name or "—",
            "Status":           STATUS_EMOJI[m.match_status],
            "_enter":           m.match_status in (MatchStatus.EXACT, MatchStatus.FUZZY) and m.is_numeric,
        })
    return pd.DataFrame(rows)


# ── Session state ─────────────────────────────────────────────────────────────

for _key, _default in [
    ("results", {}),
    ("entry_status", {}),
    ("entry_errors", {}),
    ("temp_dir", tempfile.mkdtemp()),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default


# ── Load config ───────────────────────────────────────────────────────────────

settings, tests, testindexes, units, coral_fields = get_config()
coral_cfg = settings.get("coral", {})
_email    = coral_cfg.get("email")    or os.environ.get("CORAL_EMAIL", "")
_password = coral_cfg.get("password") or os.environ.get("CORAL_PASSWORD", "")
_creds_ok = bool(_email and _password)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Connection")
    if _creds_ok:
        st.success(f"✅ Credentials loaded\n\n`{_email}`")
    else:
        st.error(
            "❌ No credentials found.\n\n"
            "Add `coral.email` and `coral.password` to `config/settings.yaml` "
            "or set the `CORAL_EMAIL` / `CORAL_PASSWORD` environment variables."
        )

    st.divider()
    st.caption("🔒 All PDFs are processed locally.\nNothing is uploaded to any server.")
    st.divider()
    if st.button("🗑 Clear all results", use_container_width=True):
        st.session_state.results.clear()
        st.session_state.entry_status.clear()
        st.session_state.entry_errors.clear()
        st.rerun()


# ── Header ────────────────────────────────────────────────────────────────────

st.title("🧪 Lab Results Entry")
st.caption(
    "Upload lab result PDFs · Preview parsed values · Enter into coral.app  \n"
    "Supports Dynacare (QC / ON), DCML, Biron, LifeLabs · Digital and scanned (OCR)"
)
st.divider()


# ── File upload ───────────────────────────────────────────────────────────────

uploaded_files = st.file_uploader(
    "Drop PDF lab results here",
    type="pdf",
    accept_multiple_files=True,
    label_visibility="collapsed",
)

# Process any newly uploaded files
if uploaded_files:
    for uf in uploaded_files:
        if uf.name not in st.session_state.results:
            tmp_path = os.path.join(st.session_state.temp_dir, uf.name)
            with open(tmp_path, "wb") as fh:
                fh.write(uf.getbuffer())

            with st.spinner(f"Processing **{uf.name}**…"):
                try:
                    lab_result, report, entry_items, used_ocr = run_pipeline(
                        tmp_path, settings, tests, testindexes, coral_fields
                    )
                    st.session_state.results[uf.name] = {
                        "lab_result":  lab_result,
                        "report":      report,
                        "entry_items": entry_items,
                        "used_ocr":    used_ocr,
                        "pdf_path":    tmp_path,
                    }
                except Exception as exc:
                    st.session_state.results[uf.name] = {"error": str(exc)}


# ── Result cards ──────────────────────────────────────────────────────────────

for filename, data in st.session_state.results.items():
    st.divider()

    # ── Error card ────────────────────────────────────────────────────────────
    if "error" in data:
        st.error(f"**{filename}** — Failed to process: `{data['error']}`")
        continue

    lab_result  = data["lab_result"]
    report      = data["report"]
    entry_items = data["entry_items"]
    used_ocr    = data["used_ocr"]
    pdf_path    = data["pdf_path"]
    status      = st.session_state.entry_status.get(filename)

    # ── Card header ───────────────────────────────────────────────────────────
    head_col, metric_col = st.columns([3, 1])
    with head_col:
        st.subheader(f"📄 {lab_result.patient.full_name or 'Unknown Patient'}")
        meta = [
            lab_result.lab_name or "Unknown lab",
            lab_result.province or "",
            str(lab_result.collection_date) if lab_result.collection_date else "",
            f"DOB {lab_result.patient.date_of_birth}" if lab_result.patient.date_of_birth else "",
            "OCR (scanned)" if used_ocr else "Digital PDF",
        ]
        st.caption("  ·  ".join(p for p in meta if p))
    with metric_col:
        st.metric("Ready to enter", f"{len(entry_items)} values")

    # ── Marker table ──────────────────────────────────────────────────────────
    df = build_markers_df(lab_result)
    matched_count = df["_enter"].sum()
    with st.expander(
        f"Markers — {matched_count} will be entered · {len(df) - matched_count} skipped",
        expanded=True,
    ):
        show_all = st.toggle(
            "Show unmatched markers too",
            key=f"toggle_{filename}",
            value=False,
        )
        display_df = df.drop(columns=["_enter"])
        if not show_all:
            display_df = display_df[df["_enter"]]
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── Warnings ──────────────────────────────────────────────────────────────
    if report.missing_from_coral:
        names = ", ".join(report.missing_from_coral[:5])
        extra = f" +{len(report.missing_from_coral) - 5} more" if len(report.missing_from_coral) > 5 else ""
        st.warning(
            f"⚠️ **{len(report.missing_from_coral)} marker(s)** found in PDF "
            f"but have no matching field in coral.app: {names}{extra}"
        )
    if report.missing_from_panel:
        names = ", ".join(report.missing_from_panel[:5])
        extra = f" +{len(report.missing_from_panel) - 5} more" if len(report.missing_from_panel) > 5 else ""
        st.info(
            f"ℹ️ **{len(report.missing_from_panel)} expected panel marker(s)** "
            f"not found in this PDF: {names}{extra}"
        )

    # ── Entry status / actions ────────────────────────────────────────────────
    if status == "success":
        st.success("✅ Successfully entered into coral.app — check the browser to review and save.")
    elif status == "member_not_found":
        st.error(
            f"❌ Could not find **{lab_result.patient.full_name}** in coral.app. "
            "Check the name spelling or add the member manually first."
        )
    elif status == "error":
        st.error(f"❌ Automation error: `{st.session_state.entry_errors.get(filename, 'unknown')}`")
    else:
        btn_col, _ = st.columns([2, 4])
        with btn_col:
            _disabled = not _creds_ok or len(entry_items) == 0
            _help = (
                "Set credentials in config/settings.yaml first" if not _creds_ok
                else "No numeric values matched — nothing to enter" if len(entry_items) == 0
                else None
            )
            if st.button(
                f"Enter {len(entry_items)} values into coral.app",
                key=f"enter_{filename}",
                type="primary",
                disabled=_disabled,
                help=_help,
            ):
                with st.spinner("Opening browser and entering data… watch for the browser window."):
                    try:
                        automator = CoralAutomator(settings)
                        automator.set_test_index_map(testindexes)
                        automator.open_browser(headless=False)
                        try:
                            automator.login()
                            member_url = automator.find_member(lab_result.patient.full_name)
                            if not member_url:
                                st.session_state.entry_status[filename] = "member_not_found"
                            else:
                                automator.submit_lab_result(
                                    lab_result, member_url, dry_run=False
                                )
                                st.session_state.entry_status[filename] = "success"
                        finally:
                            automator.close_browser()
                    except Exception as exc:
                        st.session_state.entry_errors[filename] = str(exc)
                        st.session_state.entry_status[filename] = "error"
                st.rerun()


# ── Empty state ───────────────────────────────────────────────────────────────

if not st.session_state.results and not uploaded_files:
    st.markdown(
        """
        <div style="text-align:center; padding:60px 0; color:#888;">
            <p style="font-size:1.1rem;">↑ Drop one or more PDF lab results above to get started</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
