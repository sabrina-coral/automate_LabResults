"""
coral.app (coralhealth.app) automation via Playwright.

Based on the observed tab-based navigation pattern:
- Each test marker has a fixed TAB index position in the lab entry form
- Navigation: Tab/Shift+Tab to move between fields
- Values are typed directly into the focused field

Usage:
    automator = CoralAutomator(settings)
    automator.open_browser()
    automator.login()
    member_url = automator.find_member(patient)
    automator.navigate_to_lab_entry(member_url)
    automator.enter_results(toenter)   # toenter: list of (name, tab_index, value)
    automator.save()
    automator.close_browser()
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from .models import CoralMember, LabResult, MarkerResult, MatchStatus


@dataclass
class EntryItem:
    """A single value to be entered into coral.app."""
    marker_name: str      # display name for logging
    tab_index: int        # 0-based position in the form (from tests.txt order)
    value: str            # numeric value to type


class CoralAutomator:
    """
    Playwright-based automation for coralhealth.app lab result entry.

    Tab navigation mirrors the colleague's pyautogui approach but runs
    inside a real Playwright browser — more reliable and doesn't require
    the lab software to be the focused window.
    """

    def __init__(self, settings: dict):
        self.settings = settings
        self.base_url: str = settings.get("coral", {}).get("base_url", "https://coralhealth.app")
        self.email: str = (
            os.environ.get("CORAL_EMAIL")
            or settings.get("coral", {}).get("email", "")
        )
        self.password: str = (
            os.environ.get("CORAL_PASSWORD")
            or settings.get("coral", {}).get("password", "")
        )
        self.timeout_ms: int = settings.get("coral", {}).get("timeout_ms", 10000)
        self.type_delay: float = settings.get("coral", {}).get("type_delay", 0.05)
        self.tab_delay: float = settings.get("coral", {}).get("tab_delay", 0.08)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._current_tab_index: int = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def open_browser(self, headless: bool = False) -> None:
        """Launch Playwright browser. headless=False lets you watch (recommended)."""
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
    # Login
    # ------------------------------------------------------------------ #

    def login(self) -> None:
        """Log in to coralhealth.app."""
        if not self.email or not self.password:
            raise ValueError(
                "Coral credentials not set. "
                "Set CORAL_EMAIL and CORAL_PASSWORD environment variables."
            )
        page = self._page
        page.goto(f"{self.base_url}/login", timeout=self.timeout_ms)
        page.wait_for_load_state("networkidle")

        # Try common login field selectors
        page.fill("input[type='email'], input[name='email']", self.email)
        page.fill("input[type='password'], input[name='password']", self.password)
        page.click("button[type='submit'], button:has-text('Sign in'), button:has-text('Connexion')")
        page.wait_for_load_state("networkidle")

    # ------------------------------------------------------------------ #
    # Member search
    # ------------------------------------------------------------------ #

    def find_member(self, full_name: str) -> Optional[str]:
        """
        Search for a member by name and return their profile URL.
        Returns None if not found; raises if multiple matches.
        """
        page = self._page
        selectors = self.settings.get("coral", {}).get("selectors", {})

        # Navigate to member search
        page.goto(f"{self.base_url}/members", timeout=self.timeout_ms)
        page.wait_for_load_state("networkidle")

        search_sel = selectors.get("search_member_input", "input[type='search']")
        page.fill(search_sel, full_name)
        page.wait_for_timeout(800)  # wait for search results to load

        result_sel = selectors.get("member_result_item", ".member-item")
        results = page.query_selector_all(result_sel)

        if not results:
            return None
        if len(results) == 1:
            href = results[0].get_attribute("href")
            if href:
                return f"{self.base_url}{href}" if href.startswith("/") else href
            results[0].click()
            page.wait_for_load_state("networkidle")
            return page.url

        # Multiple matches — return list of names for the caller to handle
        names = [r.inner_text().strip() for r in results]
        raise ValueError(
            f"Multiple members match '{full_name}': {names}\n"
            "Please specify the member more precisely."
        )

    def navigate_to_lab_entry(self, member_url: str) -> None:
        """Go to the lab results entry page for a member."""
        page = self._page
        page.goto(member_url, timeout=self.timeout_ms)
        page.wait_for_load_state("networkidle")

        # Find and click the lab entry section / button
        selectors = self.settings.get("coral", {}).get("selectors", {})
        lab_sel = selectors.get("lab_entry_section", ".lab-results-form")

        try:
            page.click(lab_sel, timeout=self.timeout_ms)
        except Exception:
            # May already be on the right page; proceed
            pass
        page.wait_for_load_state("networkidle")

    # ------------------------------------------------------------------ #
    # Data entry (tab-based navigation, same as colleague's approach)
    # ------------------------------------------------------------------ #

    def focus_first_field(self) -> None:
        """Click / focus the first lab entry field in the form."""
        selectors = self.settings.get("coral", {}).get("selectors", {})
        first_sel = selectors.get("first_field", ".lab-input:first-child")
        self._page.click(first_sel, timeout=self.timeout_ms)
        self._current_tab_index = 0

    def _tab_to(self, target_index: int) -> None:
        """
        Navigate to the field at target_index by pressing Tab or Shift+Tab.
        Mirrors the colleague's moveup/movedown logic but via Playwright keyboard.
        """
        page = self._page
        while self._current_tab_index != target_index:
            if self._current_tab_index < target_index:
                page.keyboard.press("Tab")
                self._current_tab_index += 1
            else:
                page.keyboard.press("Shift+Tab")
                self._current_tab_index -= 1
            time.sleep(self.tab_delay)

    def enter_results(self, items: list[EntryItem]) -> None:
        """
        Enter a list of lab values into the form using tab navigation.

        Args:
            items: list of EntryItem, sorted by tab_index for efficiency
        """
        # Sort by tab_index so we always move forward when possible
        sorted_items = sorted(items, key=lambda x: x.tab_index)

        self.focus_first_field()

        for item in sorted_items:
            print(f"  → Entering {item.marker_name}: {item.value}")
            self._tab_to(item.tab_index)
            # Clear existing value and type new one
            self._page.keyboard.press("Control+a")
            self._page.keyboard.type(item.value, delay=int(self.type_delay * 1000))
            time.sleep(0.05)

    def save(self) -> None:
        """Click the Save button."""
        selectors = self.settings.get("coral", {}).get("selectors", {})
        save_sel = selectors.get(
            "save_button",
            "button[type='submit'], button:has-text('Save'), button:has-text('Enregistrer')"
        )
        self._page.click(save_sel, timeout=self.timeout_ms)
        self._page.wait_for_load_state("networkidle")
        print("  ✓ Saved successfully")

    # ------------------------------------------------------------------ #
    # High-level helper: full flow for one LabResult
    # ------------------------------------------------------------------ #

    def submit_lab_result(
        self,
        lab_result: LabResult,
        member_url: str,
        dry_run: bool = True,
    ) -> None:
        """
        Full automated entry for a LabResult into coral.app.

        Args:
            lab_result: parsed and matched LabResult
            member_url: coral.app URL for the patient's member page
            dry_run: if True, print actions but don't actually click/type
        """
        matched = [
            m for m in lab_result.markers
            if m.match_status in (MatchStatus.EXACT, MatchStatus.FUZZY)
            and m.is_numeric
            and m.coral_field_name is not None
        ]

        if not matched:
            print("  No matched markers to enter.")
            return

        # Build EntryItem list using tab_index from coral_field_name lookup
        items = []
        for m in matched:
            idx = self._field_name_to_tab_index(m.coral_field_name)
            if idx is not None:
                items.append(EntryItem(
                    marker_name=m.coral_field_name,
                    tab_index=idx,
                    value=str(m.numeric_value),
                ))
            else:
                print(f"  Warning: no tab index for '{m.coral_field_name}' — skipping")

        if dry_run:
            print(f"\n  [DRY RUN] Would enter {len(items)} values:")
            for item in sorted(items, key=lambda x: x.tab_index):
                print(f"    [{item.tab_index:02d}] {item.marker_name}: {item.value}")
            return

        self.navigate_to_lab_entry(member_url)
        self.enter_results(items)
        self.save()

    def _field_name_to_tab_index(self, field_name: str) -> Optional[int]:
        """
        Convert a coral.app field name to its tab index.
        The tab index is derived from the position in tests.txt.
        Must be set via set_test_index_map() before calling.
        """
        return self._test_index_map.get(field_name.lower())

    def set_test_index_map(self, test_index_map: dict[str, int]) -> None:
        """
        Provide the mapping from normalised field names to tab indices.
        Built from tests.txt by the main pipeline.
        """
        self._test_index_map: dict[str, int] = {k.lower(): v for k, v in test_index_map.items()}
