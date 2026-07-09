from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class NH1816FetchResult:
    items: list[dict[str, Any]]
    columns: list[str]
    fetched_at: str
    screenshot_path: str | None = None
    html_snapshot_path: str | None = None


class NH1816PortalService:
    def __init__(
        self,
        username: str,
        password: str,
        value_meters_url: str,
        headless: bool = True,
        debug_dir: str | Path = "storage/debug",
    ) -> None:
        self.username = username
        self.password = password
        self.value_meters_url = value_meters_url
        self.headless = headless
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password and self.value_meters_url)

    def fetch_value_meters(self) -> NH1816FetchResult:
        if not self.configured:
            raise RuntimeError("NH1816 configuratie ontbreekt. Vul URL, gebruikersnaam en wachtwoord in .env.")

        try:
            from bs4 import BeautifulSoup
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright/BeautifulSoup ontbreekt. Installeer dependencies en voer uit: "
                "python -m playwright install chromium"
            ) from exc

        fetched_at = datetime.now().isoformat(timespec="seconds")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(self.value_meters_url, wait_until="domcontentloaded", timeout=60000)
                self._try_login(page, PlaywrightTimeoutError)
                page.goto(self.value_meters_url, wait_until="networkidle", timeout=60000)
                html = page.content()
                html_snapshot_path = self.debug_dir / "nh1816_value_meters.html"
                html_snapshot_path.write_text(html, encoding="utf-8")
                soup = BeautifulSoup(html, "html.parser")
                items, columns = self._extract_items(soup)
                if not items:
                    items = self._extract_raw_rows(soup)
                return NH1816FetchResult(
                    items=items,
                    columns=columns,
                    fetched_at=fetched_at,
                    html_snapshot_path=str(html_snapshot_path),
                )
            except Exception as exc:
                screenshot_path = self.debug_dir / "nh1816_login_failed.png"
                html_snapshot_path = self.debug_dir / "nh1816_value_meters.html"
                try:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                except Exception:
                    screenshot_path = None
                try:
                    html_snapshot_path.write_text(page.content(), encoding="utf-8")
                except Exception:
                    html_snapshot_path = None
                raise RuntimeError(
                    "NH1816 ophalen mislukt. Debugbestanden zijn opgeslagen in storage/debug."
                ) from exc
            finally:
                context.close()
                browser.close()

    def _try_login(self, page: Any, timeout_error: type[Exception]) -> None:
        username_selector = self._first_visible(
            page,
            [
                "input[type='email']",
                "input[name*='user' i]",
                "input[id*='user' i]",
                "input[name*='email' i]",
                "input[id*='email' i]",
                "input[type='text']",
            ],
        )
        password_selector = self._first_visible(
            page,
            [
                "input[type='password']",
                "input[name*='password' i]",
                "input[id*='password' i]",
                "input[name*='wachtwoord' i]",
                "input[id*='wachtwoord' i]",
            ],
        )
        if not username_selector and not password_selector:
            return

        if username_selector:
            page.fill(username_selector, self.username)
        if password_selector:
            page.fill(password_selector, self.password)

        button_selector = self._first_visible(
            page,
            [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Inloggen')",
                "button:has-text('Login')",
                "button:has-text('Aanmelden')",
            ],
        )
        if button_selector:
            page.click(button_selector)
        else:
            page.keyboard.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except timeout_error:
            page.wait_for_load_state("domcontentloaded", timeout=30000)

    def _first_visible(self, page: Any, selectors: list[str]) -> str | None:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1500):
                    return selector
            except Exception:
                continue
        return None

    def _extract_items(self, soup: Any) -> tuple[list[dict[str, Any]], list[str]]:
        table = soup.find("table")
        if not table:
            return [], []
        headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
        if not headers:
            first_row = table.find("tr")
            headers = [cell.get_text(" ", strip=True) for cell in first_row.find_all(["th", "td"])] if first_row else []
        rows = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            raw_text = " | ".join(value for value in values if value)
            mapped = self._map_row(headers, values, raw_text)
            mapped["action_button_present"] = bool(row.find(["button", "a", "input"]))
            rows.append(mapped)
        return rows, headers

    def _extract_raw_rows(self, soup: Any) -> list[dict[str, Any]]:
        rows = []
        selectors = ["[role='row']", ".row", "li", "article"]
        for selector in selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if self._looks_like_value_meter(text):
                    rows.append(
                        {
                            "raw_text": text,
                            "status": "openstaand",
                            "action_button_present": bool(element.find(["button", "a", "input"])),
                        }
                    )
            if rows:
                return rows
        body_text = soup.get_text(" ", strip=True)
        if body_text:
            return [{"raw_text": body_text[:4000], "status": "parsing_onzeker", "action_button_present": False}]
        return []

    def _map_row(self, headers: list[str], values: list[str], raw_text: str) -> dict[str, Any]:
        row = {headers[index] if index < len(headers) else f"kolom_{index + 1}": value for index, value in enumerate(values)}
        normalized = {self._normalize_header(key): value for key, value in row.items()}
        return {
            "klantnaam": self._first_value(normalized, ["klantnaam", "klant", "relatie", "verzekeringnemer", "naam"]),
            "polisnummer": self._first_value(normalized, ["polisnummer", "polis", "polnr", "policynumber"]),
            "meter_type": self._first_value(normalized, ["soort", "type", "waardemeter", "meter", "soortwaardemeter"]),
            "request_date": self._first_value(normalized, ["datumverzoek", "verzoekdatum", "datum", "aanvraagdatum"]),
            "status": self._first_value(normalized, ["status", "portalstatus", "nh1816status"]) or "openstaand",
            "raw_text": raw_text,
            "raw_json": row,
        }

    def _first_value(self, row: dict[str, str], keys: list[str]) -> str:
        for key in keys:
            if row.get(key):
                return row[key]
        return ""

    def _normalize_header(self, value: str) -> str:
        return "".join(character for character in value.lower() if character.isalnum())

    def _looks_like_value_meter(self, text: str) -> bool:
        lowered = text.lower()
        return "waardemeter" in lowered or "inboedel" in lowered or "herbouw" in lowered or "opstal" in lowered
