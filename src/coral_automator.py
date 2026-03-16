"""
coral.app (coralhealth.app) automation via Playwright.

Workflow (mirrors manual steps confirmed by browser inspection):
  1. Search for member by name using the MUI DataGrid searchbox
  2. Click the matching member row to open the member profile
  3. Click the "+" MuiIconButton to create a new lab result entry
     (or navigate to an existing entry URL for updates)
  4. Optionally upload the source PDF file
  5. Set the effective date (collection date from the lab report)
  6. Fill in each numeric lab value by targeting the Nth freeTextInput field
     — index N corresponds to the field's position in config/tests.txt
  7. Click "Publish"

Selectors are configured in config/settings.yaml under coral.selectors.
Login is skipped when an active session already exists (cookie-based).
"""
from __future__ import annotations

import os
import time
from datetime import date
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from .models import LabResult, MarkerResult, MatchStatus


# Kept for backward compatibility with main.py imports
from dataclasses import dataclass

@dataclass
class EntryItem:
    """Deprecated — retained for import compatibility. Use fill_all_fields() instead."""
    marker_name: str
    tab_index: int
    value: str


class CoralAutomator:
    """
    Playwright-based automation for coralhealth.app lab result entry.

    Field targeting uses nth-child index matching (not tab key simulation)
    which is faster and more reliable for a fixed-order React/MUI form.
    """

    def __init__(self, settings: dict):
        self.settings = settings
        coral = settings.get("coral", {})
        self.base_url: str = coral.get("base_url", "https://coralhealth.app")
        self.email: str = (
            os.environ.get("CORAL_EMAIL") or coral.get("email", "")
        )
        self.password: str = (
            os.environ.get("CORAL_PASSWORD") or coral.get("password", "")
        )
        self.timeout_ms: int = coral.get("timeout_ms", 10000)
        self.type_delay: int = int(coral.get("type_delay", 0.05) * 1000)
        self._sel: dict = coral.get("selectors", {})

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._test_index_map: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Selectors (with fallbacks if settings key is missing)
    # ------------------------------------------------------------------ #

    def _s(self, key: str, fallback: str) -> str:
        return self._sel.get(key, fallback)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def open_browser(self, headless: bool = False) -> None:
        """Launch Playwright Chromium. headless=False shows the browser (recommended)."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright is not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            )
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def close_browser(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    # ------------------------------------------------------------------ #
    # Session / login
    # ------------------------------------------------------------------ #

    def ensure_logged_in(self) -> None:
        """
        Check if there is already an active session.
        If not (e.g. session expired), attempt login.
        Called automatically before any navigation step.
        """
        page = self._page
        page.goto(f"{self.base_url}/members", timeout=self.timeout_ms)
        page.wait_for_load_state("networkidle")

        # If redirected to /login, we need to authenticate
        if "/login" in page.url or "/sign-in" in page.url:
            print("  Session not active — logging in...")
            self._do_login()
        else:
            print("  Active session detected — skipping login.")

    def _do_login(self) -> None:
        """Perform the actual login flow."""
        if not self.email or not self.password:
            raise ValueError(
                "Coral credentials not set. "
                "Set CORAL_EMAIL and CORAL_PASSWORD environment variables "
                "or fill in config/settings.yaml."
            )
        page = self._page
        page.fill("input[type='email'], input[name='email']", self.email)
        page.fill("input[type='password'], input[name='password']", self.password)
        page.click(
            "button[type='submit'], button:has-text('Sign in'), "
            "button:has-text('Connexion'), button:has-text('Log in')"
        )
        page.wait_for_load_state("networkidle")
        if "/login" in page.url or "/sign-in" in page.url:
            raise RuntimeError("Login failed — check CORAL_EMAIL / CORAL_PASSWORD.")
        print("  Login successful.")

    # ------------------------------------------------------------------ #
    # Member search
    # ------------------------------------------------------------------ #

    def find_member_url(self, full_name: str) -> Optional[str]:
        """
        Search for a member by name and return their profile URL.
        Returns None if not found.  Raises if multiple matches are ambiguous.
        """
        page = self._page
        search_sel = self._s("search_member_input", "input[role='searchbox']")

        # Clear and type the search term
        page.fill(search_sel, "")
        page.fill(search_sel, full_name)
        page.wait_for_timeout(1000)   # let the DataGrid filter update

        # Look for member row links (href contains /members/)
        link_sel = "a[href*='/members/']"
        links = page.query_selector_all(link_sel)

        if not links:
            return None

        # Filter to links that contain the name (case-insensitive)
        name_lower = full_name.lower()
        matched = [
            lnk for lnk in links
            if name_lower.split()[0].lower() in (lnk.inner_text() or "").lower()
            or name_lower.split()[-1].lower() in (lnk.inner_text() or "").lower()
        ]
        if not matched:
            matched = links  # fall back to first result

        href = matched[0].get_attribute("href") or ""
        if href.startswith("/"):
            return f"{self.base_url}{href}"
        return href or None

    def navigate_to_member(self, member_url: str) -> None:
        """Navigate to a member's profile page."""
        self._page.goto(member_url, timeout=self.timeout_ms)
        self._page.wait_for_load_state("networkidle")

    # ------------------------------------------------------------------ #
    # Creating / opening a lab result entry
    # ------------------------------------------------------------------ #

    def click_add_new_lab_result(self) -> None:
        """
        Click the "+" button to create a new lab result questionnaire entry.
        The button is a MuiIconButton-sizeLg in the content area.
        """
        page = self._page
        add_sel = self._s("add_new_button", "button.MuiIconButton-sizeLg")
        page.click(add_sel, timeout=self.timeout_ms)
        page.wait_for_load_state("networkidle")

    def navigate_to_lab_entry_url(self, entry_url: str) -> None:
        """Navigate directly to an existing lab result entry URL (for updates)."""
        self._page.goto(entry_url, timeout=self.timeout_ms)
        self._page.wait_for_load_state("networkidle")

    # ------------------------------------------------------------------ #
    # File upload
    # ------------------------------------------------------------------ #

    def upload_pdf(self, pdf_path: str) -> None:
        """
        Upload the source PDF to the lab result entry.
        The file input is hidden; we trigger it via the AttachFile button.
        """
        page = self._page
        upload_button_sel = self._s(
            "file_upload_button",
            "button:has(svg[data-testid='AttachFileSvgIcon'])",
        )
        file_input_sel = self._s("file_upload_input", "input[type='file']")

        try:
            # Some apps expose a hidden file input that we can target directly
            file_input = page.query_selector(file_input_sel)
            if file_input:
                file_input.set_input_files(pdf_path)
            else:
                # Click the attach button, then handle the file chooser dialog
                with page.expect_file_chooser() as fc_info:
                    page.click(upload_button_sel, timeout=self.timeout_ms)
                fc_info.value.set_files(pdf_path)
            page.wait_for_load_state("networkidle")
            print(f"  ✓ Uploaded: {pdf_path}")
        except Exception as exc:
            print(f"  ⚠ PDF upload failed ({exc}). Continuing without upload.")

    # ------------------------------------------------------------------ #
    # Setting the effective date
    # ------------------------------------------------------------------ #

    def set_effective_date(self, collection_date: date) -> None:
        """
        Set the effective date field to the lab collection date.
        The input is: div[data-testid='effective-date-input'] input[type='date']
        Expects ISO format YYYY-MM-DD.
        """
        page = self._page
        date_sel = self._s(
            "effective_date_input",
            "div[data-testid='effective-date-input'] input[type='date']",
        )
        iso_date = collection_date.strftime("%Y-%m-%d")
        page.fill(date_sel, iso_date, timeout=self.timeout_ms)
        print(f"  ✓ Effective date set to {iso_date}")

    # ------------------------------------------------------------------ #
    # Filling lab values
    # ------------------------------------------------------------------ #

    def fill_field(self, field_index: int, value: str) -> None:
        """
        Fill a single lab field by its 0-based index in the form.
        Uses nth(index) on 'div[data-testid=freeTextInput] input'.
        """
        field_sel = self._s("lab_field_input", "div[data-testid='freeTextInput'] input")
        locator = self._page.locator(field_sel).nth(field_index)
        locator.fill("", timeout=self.timeout_ms)
        locator.fill(value, timeout=self.timeout_ms)

    def fill_all_fields(self, items: list[tuple[int, str, str]]) -> None:
        """
        Fill multiple lab fields.

        Args:
            items: list of (field_index, field_name, value)
        """
        for idx, name, value in sorted(items, key=lambda x: x[0]):
            try:
                self.fill_field(idx, value)
                print(f"    [{idx:02d}] {name}: {value}")
                time.sleep(0.05)
            except Exception as exc:
                print(f"    [{idx:02d}] {name}: FAILED ({exc})")

    # ------------------------------------------------------------------ #
    # Draft / publish switch
    # ------------------------------------------------------------------ #

    def ensure_draft_mode(self) -> None:
        """
        Turn OFF the Publish toggle so the entry is saved as a draft.

        The switch is: input[type="checkbox"][role="switch"]
        When aria-checked="true" the entry will be published on save — we
        want aria-checked="false" (draft) so you can review before publishing.
        """
        page = self._page
        switch_sel = self._s(
            "publish_switch",
            "input[type='checkbox'][role='switch']",
        )
        try:
            switch = page.locator(switch_sel).first
            if switch.get_attribute("aria-checked") == "true":
                switch.click()
                page.wait_for_timeout(300)
                print("  ✓ Publish toggle turned OFF — entry will save as draft")
            else:
                print("  ✓ Publish toggle already OFF — will save as draft")
        except Exception as exc:
            print(f"  ⚠ Could not locate publish toggle ({exc}). Check manually.")

    # ------------------------------------------------------------------ #
    # High-level flow: full submission for one LabResult
    # ------------------------------------------------------------------ #

    def submit_lab_result(
        self,
        lab_result: LabResult,
        member_url: str,
        pdf_path: Optional[str] = None,
        entry_url: Optional[str] = None,
        dry_run: bool = True,
    ) -> None:
        """
        Full automated entry for a LabResult into coral.app.

        Args:
            lab_result:  parsed and matched LabResult
            member_url:  coral.app URL for the patient's member page
            pdf_path:    path to the source PDF (uploaded for the record)
            entry_url:   if set, navigate to this existing entry (for updates)
                         instead of creating a new one
            dry_run:     if True, print planned actions but do nothing
        """
        # Collect matched numeric markers with a known field index
        items: list[tuple[int, str, str]] = []
        for m in lab_result.markers:
            if m.match_status not in (MatchStatus.EXACT, MatchStatus.FUZZY):
                continue
            if not m.is_numeric or m.coral_field_name is None:
                continue
            idx = self._test_index_map.get(m.coral_field_name.lower())
            if idx is None:
                print(f"  ⚠ No field index for '{m.coral_field_name}' — skipping")
                continue
            items.append((idx, m.coral_field_name, str(m.numeric_value)))

        if not items:
            print("  No matched numeric markers to enter.")
            return

        if dry_run:
            print(f"\n  [DRY RUN] Would enter {len(items)} value(s):")
            for idx, name, value in sorted(items):
                print(f"    [{idx:02d}] {name}: {value}")
            if lab_result.collection_date:
                print(f"    Effective date: {lab_result.collection_date}")
            if pdf_path:
                print(f"    PDF upload: {pdf_path}")
            return

        # --- Live mode ---
        if entry_url:
            print(f"  Navigating to existing entry: {entry_url}")
            self.navigate_to_lab_entry_url(entry_url)
        else:
            print(f"  Navigating to member: {member_url}")
            self.navigate_to_member(member_url)
            print("  Clicking '+' to create new lab result entry...")
            self.click_add_new_lab_result()

        if pdf_path:
            self.upload_pdf(pdf_path)

        if lab_result.collection_date:
            self.set_effective_date(lab_result.collection_date)

        print(f"  Filling {len(items)} field(s)...")
        self.fill_all_fields(items)

        self.ensure_draft_mode()

        print()
        print("  ─────────────────────────────────────────────────────")
        print("  ✋ Automation complete — please review the data entry.")
        print("     When ready, click Save manually in the browser.")
        print("  ─────────────────────────────────────────────────────")

    # ------------------------------------------------------------------ #
    # Index map (built from tests.txt by the main pipeline)
    # ------------------------------------------------------------------ #

    def set_test_index_map(self, test_index_map: dict[str, int]) -> None:
        """
        Provide the mapping from normalised field names → 0-based form index.
        Built from tests.txt by the main pipeline (first alias = canonical name).
        """
        self._test_index_map = {k.lower(): v for k, v in test_index_map.items()}
