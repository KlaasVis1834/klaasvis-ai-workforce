from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
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
                self._wait_for_value_meter_rows(page, PlaywrightTimeoutError)
                html = page.content()
                html_snapshot_path = self.debug_dir / "nh1816_value_meters.html"
                html_snapshot_path.write_text(html, encoding="utf-8")
                screenshot_path = self.debug_dir / "nh1816_value_meters.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                items, columns = self._extract_visible_table_rows(page)
                soup = BeautifulSoup(html, "html.parser")
                if not items:
                    items, columns = self._extract_items(soup)
                if not items:
                    items = self._extract_raw_rows(soup)
                return NH1816FetchResult(
                    items=items,
                    columns=columns,
                    fetched_at=fetched_at,
                    screenshot_path=str(screenshot_path),
                    html_snapshot_path=str(html_snapshot_path),
                )
            except Exception as exc:
                screenshot_path = self.debug_dir / "nh1816_value_meters.png"
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

    def _wait_for_value_meter_rows(self, page: Any, timeout_error: type[Exception]) -> None:
        try:
            page.wait_for_selector("table tr, [role='row']", timeout=30000)
        except timeout_error:
            page.wait_for_load_state("domcontentloaded", timeout=10000)

    def _extract_visible_table_rows(self, page: Any) -> tuple[list[dict[str, Any]], list[str]]:
        table_payload = page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const rowMeta = (row) => {
                    const style = window.getComputedStyle(row);
                    return {
                        className: typeof row.className === 'string' ? row.className : '',
                        style: row.getAttribute('style') || '',
                        dataStatus: row.getAttribute('data-status') || '',
                        backgroundColor: style.backgroundColor || '',
                        color: style.color || ''
                    };
                };
                const tables = Array.from(document.querySelectorAll('table')).filter(visible);
                for (const table of tables) {
                    const headerCells = Array.from(table.querySelectorAll('thead th')).filter(visible);
                    let headers = headerCells.map((cell) => cell.innerText.trim());
                    if (!headers.length) {
                        const first = Array.from(table.querySelectorAll('tr')).find((row) => visible(row));
                        if (first) {
                            headers = Array.from(first.querySelectorAll('th')).filter(visible).map((cell) => cell.innerText.trim());
                        }
                    }
                    const rows = [];
                    for (const row of Array.from(table.querySelectorAll('tbody tr, tr')).filter(visible)) {
                        const cells = Array.from(row.querySelectorAll('td')).filter(visible);
                        if (!cells.length) continue;
                        const values = cells.map((cell) => cell.innerText.trim());
                        if (!values.some(Boolean)) continue;
                        rows.push({ values, rawText: values.filter(Boolean).join(' | '), meta: rowMeta(row) });
                    }
                    if (rows.length) return { headers, rows };
                }
                const roleRows = Array.from(document.querySelectorAll('[role="row"]')).filter(visible);
                const rows = [];
                for (const row of roleRows) {
                    const cells = Array.from(row.querySelectorAll('[role="cell"], [role="gridcell"], [role="columnheader"]')).filter(visible);
                    const values = cells.length ? cells.map((cell) => cell.innerText.trim()) : [row.innerText.trim()];
                    if (!values.some(Boolean)) continue;
                    rows.push({ values, rawText: values.filter(Boolean).join(' | '), meta: rowMeta(row) });
                }
                return { headers: [], rows };
            }
            """
        )
        headers = [str(value or "").strip() for value in table_payload.get("headers", [])]
        items = []
        for row in table_payload.get("rows", []):
            values = [str(value or "").strip() for value in row.get("values", [])]
            raw_text = str(row.get("rawText") or " | ".join(value for value in values if value))
            meta = row.get("meta") or {}
            mapped = self._map_row(headers, values, raw_text)
            row_state = self._row_state_from_marker(
                " ".join(
                    [
                        str(meta.get("className") or ""),
                        str(meta.get("style") or ""),
                        str(meta.get("dataStatus") or ""),
                        str(meta.get("backgroundColor") or ""),
                        raw_text,
                    ]
                )
            )
            if row_state == "unknown":
                row_state = self._status_state(mapped.get("status", ""))
            mapped["row_state"] = row_state
            mapped["status"] = self._portal_status(row_state)
            mapped["row_class"] = meta.get("className")
            mapped["row_background_color"] = meta.get("backgroundColor")
            mapped["raw_json"] = {**(mapped.get("raw_json") or {}), "_row_meta": meta}
            mapped["action_button_present"] = False
            items.append(mapped)
        return items, headers

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
            row_state = self._row_state(row)
            mapped["row_state"] = self._status_state(mapped.get("status", "")) if row_state == "unknown" else row_state
            mapped["status"] = self._portal_status(mapped["row_state"])
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
                            "row_state": self._row_state(element),
                            "action_button_present": bool(element.find(["button", "a", "input"])),
                        }
                    )
            if rows:
                return rows
        body_text = soup.get_text(" ", strip=True)
        if body_text:
            return [{"raw_text": body_text[:4000], "status": "parsing_onzeker", "row_state": "unknown", "action_button_present": False}]
        return []

    def _map_row(self, headers: list[str], values: list[str], raw_text: str) -> dict[str, Any]:
        if not headers:
            headers = [
                "Relatie",
                "Adres",
                "Emailadres",
                "Branche",
                "Polisnr.",
                "Verloopdatum Inboedel / Verlengdatum Opstal",
                "Behandeld",
            ]
        row = {headers[index] if index < len(headers) else f"kolom_{index + 1}": value for index, value in enumerate(values)}
        normalized = {self._normalize_header(key): value for key, value in row.items()}
        return {
            "klantnaam": self._first_value(normalized, ["klantnaam", "klant", "relatie", "verzekeringnemer", "naam"]),
            "adres": self._first_value(normalized, ["adres", "straat", "woonadres", "risicoadres"]),
            "email": self._first_value(normalized, ["email", "emailadres", "e-mail", "mail"]),
            "polisnummer": self._first_value(normalized, ["polisnummer", "polisnr", "polis", "polnr", "policynumber"]),
            "branche": self._first_value(normalized, ["branche", "branch", "verzekering", "product"]),
            "meter_type": self._first_value(normalized, ["soort", "type", "waardemeter", "meter", "soortwaardemeter", "branche"]),
            "expiry_date": self._first_value(
                normalized,
                ["verloopdatuminboedel", "verlengdatumopstal", "verloopdatum", "verlengdatum", "einddatum"],
            ),
            "handled_date": self._first_value(normalized, ["behandeld", "behandelddatum", "datumbehandeld"]),
            "request_date": self._first_value(normalized, ["datumverzoek", "verzoekdatum", "datum", "aanvraagdatum"]),
            "status": self._first_value(normalized, ["status", "portalstatus", "nh1816status"]) or "",
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

    def _row_state(self, element: Any) -> str:
        marker = " ".join(
            [
                " ".join(element.get("class", [])) if hasattr(element, "get") else "",
                str(element.get("style", "")) if hasattr(element, "get") else "",
                str(element.get("data-status", "")) if hasattr(element, "get") else "",
                element.get_text(" ", strip=True) if hasattr(element, "get_text") else "",
            ]
        ).lower()
        return self._row_state_from_marker(marker)

    def _row_state_from_marker(self, marker: str) -> str:
        marker = (marker or "").lower()
        if "groen" in marker or "green" in marker or "success" in marker or "verwerkt" in marker or "behandeld" in marker:
            return "processed"
        if "grijs" in marker or "grey" in marker or "gray" in marker or "nieuw" in marker or "openstaand" in marker:
            return "open"
        color_match = re.search(r"(?:background(?:-color)?\s*:\s*)?(rgba?\([^)]+\)|#[0-9a-f]{3,8}|green|grey|gray)", marker)
        if color_match:
            color = color_match.group(1)
            rgb = self._parse_rgb(color)
            if rgb:
                red, green, blue = rgb
                if green > red + 25 and green > blue + 25:
                    return "processed"
                if abs(red - green) <= 18 and abs(green - blue) <= 18 and 90 <= red <= 235:
                    return "open"
            if color == "green":
                return "processed"
            if color in {"gray", "grey"} or color.startswith("#ccc") or color.startswith("#ddd") or color.startswith("#eee"):
                return "open"
        return "unknown"

    def _status_state(self, value: str) -> str:
        lowered = (value or "").lower()
        if "verwerkt" in lowered or "afgerond" in lowered or "groen" in lowered:
            return "processed"
        if "open" in lowered or "nieuw" in lowered or "grijs" in lowered:
            return "open"
        return "unknown"

    def _portal_status(self, row_state: str) -> str:
        if row_state == "open":
            return "openstaand"
        if row_state == "processed":
            return "behandeld"
        return "onbekend"

    def _parse_rgb(self, value: str) -> tuple[int, int, int] | None:
        match = re.search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", value or "")
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
