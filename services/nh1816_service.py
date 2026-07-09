from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
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
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright ontbreekt. Installeer dependencies en voer uit: "
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
                try:
                    selectors_path = self.debug_dir / "nh1816_selectors.json"
                    selectors_path.write_text(
                        json.dumps(self._selector_debug(page), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                raise RuntimeError(self._failure_message(page, exc)) from exc
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
        if self._mfa_required(page):
            raise RuntimeError("MFA vereist")
        if self._login_rejected(page):
            screenshot_path = self.debug_dir / "nh1816_login_failed.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            raise RuntimeError("Loginpagina geladen maar credentials geweigerd")

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
        selectors = [
            "table:visible tbody tr:visible",
            "table:visible tr:visible",
            "[role='grid']:visible [role='row']:visible",
            "[role='table']:visible [role='row']:visible",
        ]
        last_error: Exception | None = None
        for selector in selectors:
            try:
                page.wait_for_selector(selector, timeout=15000)
                return
            except timeout_error as exc:
                last_error = exc
                continue
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        raise RuntimeError(f"NH1816 waardemetertabel niet gevonden. Selectors geprobeerd: {', '.join(selectors)}") from last_error

    def _extract_visible_table_rows(self, page: Any) -> tuple[list[dict[str, Any]], list[str]]:
        table_payload = page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                const rowMeta = (row) => {
                    const style = window.getComputedStyle(row);
                    const cells = Array.from(row.querySelectorAll('td, [role="cell"], [role="gridcell"]')).filter(visible);
                    return {
                        className: typeof row.className === 'string' ? row.className : '',
                        style: row.getAttribute('style') || '',
                        dataStatus: row.getAttribute('data-status') || '',
                        backgroundColor: style.backgroundColor || '',
                        color: style.color || '',
                        cellClassNames: cells.map((cell) => typeof cell.className === 'string' ? cell.className : ''),
                        cellBackgroundColors: cells.map((cell) => window.getComputedStyle(cell).backgroundColor || ''),
                        cellStyles: cells.map((cell) => cell.getAttribute('style') || '')
                    };
                };
                const directRows = (table) => Array.from(table.querySelectorAll(':scope > tbody > tr, :scope > tr')).filter(visible);
                const directCells = (row) => Array.from(row.querySelectorAll(':scope > td, :scope > th')).filter(visible);
                const tableScore = (headers) => {
                    const haystack = headers.join(' ').toLowerCase();
                    const needles = ['relatie', 'adres', 'email', 'branche', 'polis', 'behandeld'];
                    return needles.filter((needle) => haystack.includes(needle)).length;
                };
                const tables = Array.from(document.querySelectorAll('table')).filter(visible).map((table) => {
                    let headers = Array.from(table.querySelectorAll('thead th, thead td')).filter(visible).map(text);
                    let headerIndex = -1;
                    const rows = directRows(table);
                    if (!headers.length) {
                        for (let index = 0; index < rows.length; index += 1) {
                            const cells = directCells(rows[index]);
                            const values = cells.map(text);
                            if (tableScore(values) >= 4) {
                                headers = values;
                                headerIndex = index;
                                break;
                            }
                        }
                    }
                    if (headerIndex === -1 && headers.length) {
                        headerIndex = rows.findIndex((row) => directCells(row).map(text).join(' ') === headers.join(' '));
                    }
                    return { table, headers, rows, headerIndex, score: tableScore(headers) };
                }).sort((a, b) => b.score - a.score);
                for (const table of tables) {
                    if (table.score < 4) continue;
                    const rows = [];
                    const dataRows = table.rows.slice(Math.max(table.headerIndex + 1, 0));
                    for (const row of dataRows) {
                        const cells = directCells(row);
                        if (!cells.length) continue;
                        const values = cells.map(text);
                        if (!values.some((value) => value.length)) continue;
                        rows.push({ values, rawText: values.join(' | '), meta: rowMeta(row) });
                    }
                    if (rows.length) return { type: 'table', headers: table.headers, rows };
                }
                const grids = Array.from(document.querySelectorAll('[role="grid"], [role="table"]')).filter(visible);
                for (const grid of grids) {
                    const headerRow = Array.from(grid.querySelectorAll('[role="row"]')).find((row) => {
                        return visible(row) && row.querySelectorAll('[role="columnheader"]').length;
                    });
                    const headers = headerRow
                        ? Array.from(headerRow.querySelectorAll('[role="columnheader"]')).filter(visible).map(text)
                        : [];
                    if (tableScore(headers) < 4) continue;
                    const rows = [];
                    for (const row of Array.from(grid.querySelectorAll('[role="row"]')).filter(visible)) {
                        const cells = Array.from(row.querySelectorAll('[role="cell"], [role="gridcell"]')).filter(visible);
                        if (!cells.length) continue;
                        const values = cells.map(text);
                        if (!values.some((value) => value.length)) continue;
                        rows.push({ values, rawText: values.join(' | '), meta: rowMeta(row) });
                    }
                    if (rows.length) return { type: 'role-grid', headers, rows };
                }
                return { type: 'none', headers: [], rows: [], selectors: {
                    tables: document.querySelectorAll('table').length,
                    visibleTables: Array.from(document.querySelectorAll('table')).filter(visible).length,
                    trs: document.querySelectorAll('tr').length,
                    visibleTrs: Array.from(document.querySelectorAll('tr')).filter(visible).length,
                    roleRows: document.querySelectorAll('[role="row"]').length,
                    visibleRoleRows: Array.from(document.querySelectorAll('[role="row"]')).filter(visible).length
                }};
            }
            """
        )
        headers = [str(value or "").strip() for value in table_payload.get("headers", [])]
        if not headers:
            self._write_selector_debug(page, table_payload)
            raise RuntimeError("Tabel gevonden maar kolommen niet herkend")
        items = []
        for row in table_payload.get("rows", []):
            values = [str(value or "").strip() for value in row.get("values", [])]
            raw_text = str(row.get("rawText") or " | ".join(values))
            meta = row.get("meta") or {}
            mapped = self._map_row(headers, values, raw_text)
            row_state = self._row_state_from_marker(
                " ".join(
                    [
                        str(meta.get("className") or ""),
                        str(meta.get("style") or ""),
                        str(meta.get("dataStatus") or ""),
                        str(meta.get("backgroundColor") or ""),
                        " ".join(str(value or "") for value in meta.get("cellClassNames") or []),
                        " ".join(str(value or "") for value in meta.get("cellBackgroundColors") or []),
                        " ".join(str(value or "") for value in meta.get("cellStyles") or []),
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
            mapped["raw_json"] = {**(mapped.get("raw_json") or {}), "_row_meta": meta, "_cells": values}
            mapped["action_button_present"] = False
            self._validate_dom_row(mapped)
            items.append(mapped)
        if not items:
            self._write_selector_debug(page, table_payload)
            raise RuntimeError("Value meters pagina geladen maar tabel niet gevonden")
        return items, headers

    def _map_row(self, headers: list[str], values: list[str], raw_text: str) -> dict[str, Any]:
        row = {headers[index] if index < len(headers) else f"kolom_{index + 1}": value for index, value in enumerate(values)}
        normalized = {self._normalize_header(key): value for key, value in row.items()}
        branche = self._first_value(normalized, ["branche", "branch", "verzekering", "product"])
        meter_type = self._meter_type_from_branche(
            self._first_value(normalized, ["soort", "type", "waardemeter", "meter", "soortwaardemeter"]) or branche
        )
        return {
            "klantnaam": self._first_value(normalized, ["klantnaam", "klant", "relatie", "verzekeringnemer", "naam"]),
            "adres": self._first_value(normalized, ["adres", "straat", "woonadres", "risicoadres"]),
            "email": self._first_value(normalized, ["email", "emailadres", "e-mail", "mail"]),
            "polisnummer": self._first_value(normalized, ["polisnummer", "polisnr", "polis", "polnr", "policynumber"]),
            "branche": branche,
            "meter_type": meter_type,
            "expiry_date": self._first_value(
                normalized,
                [
                    "verloopdatuminboedelverlengdatumopstal",
                    "verloopdatuminboedel",
                    "verlengdatumopstal",
                    "verloopdatum",
                    "verlengdatum",
                    "einddatum",
                ],
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

    def _meter_type_from_branche(self, value: str) -> str:
        lowered = (value or "").lower()
        if "opstal" in lowered or "herbouw" in lowered or "woonhuis" in lowered:
            return "herbouwwaardemeter"
        if "inboedel" in lowered:
            return "inboedelwaardemeter"
        return ""

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
            if not rgb:
                rgb = self._parse_hex_color(color)
            if rgb:
                red, green, blue = rgb
                if green > red and green > blue + 20:
                    return "processed"
                if abs(red - green) <= 18 and abs(green - blue) <= 18 and 90 <= red <= 235:
                    return "open"
            if color == "green":
                return "processed"
            if color.startswith("#e3eec6"):
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
        raise RuntimeError("NH1816 rijstatus kon niet uit de DOM-kleur worden bepaald.")

    def _parse_rgb(self, value: str) -> tuple[int, int, int] | None:
        match = re.search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", value or "")
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    def _parse_hex_color(self, value: str) -> tuple[int, int, int] | None:
        color = (value or "").strip().lower()
        if not color.startswith("#"):
            return None
        hex_value = color[1:]
        if len(hex_value) == 3:
            hex_value = "".join(character * 2 for character in hex_value)
        if len(hex_value) < 6:
            return None
        try:
            return int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16)
        except ValueError:
            return None

    def _validate_dom_row(self, item: dict[str, Any]) -> None:
        required = {
            "klantnaam": "Relatie / klantnaam",
            "adres": "Adres",
            "email": "Emailadres",
            "branche": "Branche",
            "polisnummer": "Polisnr.",
            "expiry_date": "Verloopdatum / Verlengdatum",
            "meter_type": "meter_type",
            "status": "Status",
        }
        if item.get("status") == "behandeld":
            required["handled_date"] = "Behandeld datum"
        missing = [label for key, label in required.items() if not str(item.get(key) or "").strip()]
        if missing:
            raise RuntimeError(
                "Tabel gevonden maar kolommen niet herkend. Ontbrekende kolommen: "
                + ", ".join(missing)
                + f". Rij: {item.get('raw_text')}"
            )

    def _mfa_required(self, page: Any) -> bool:
        text = self._safe_body_text(page).lower()
        markers = ["mfa", "2fa", "tweestaps", "authenticator", "verificatiecode", "beveiligingscode"]
        return any(marker in text for marker in markers)

    def _login_rejected(self, page: Any) -> bool:
        text = self._safe_body_text(page).lower()
        has_password = False
        try:
            has_password = page.locator("input[type='password']").count() > 0
        except Exception:
            has_password = False
        markers = ["ongeldig", "incorrect", "mislukt", "invalid", "denied", "geweigerd", "wachtwoord"]
        return has_password and any(marker in text for marker in markers)

    def _safe_body_text(self, page: Any) -> str:
        try:
            return str(page.locator("body").inner_text(timeout=1500))
        except Exception:
            return ""

    def _failure_message(self, page: Any, exc: Exception) -> str:
        reason = str(exc)
        lowered_reason = reason.lower()
        url = ""
        try:
            url = page.url
        except Exception:
            url = ""
        if "mfa vereist" in lowered_reason or self._mfa_required(page):
            return "MFA vereist"
        if "credentials geweigerd" in lowered_reason or self._login_rejected(page):
            return "Loginpagina geladen maar credentials geweigerd"
        if "tabel gevonden maar kolommen" in lowered_reason:
            return f"Tabel gevonden maar kolommen niet herkend. Debugbestanden zijn opgeslagen in storage/debug. Detail: {reason}"
        if "waardemetertabel niet gevonden" in lowered_reason:
            if "value-meters" in url:
                return "Value meters pagina geladen maar tabel niet gevonden. Debugbestanden zijn opgeslagen in storage/debug."
            return "Portal redirect werkt niet of value-meters pagina laadt niet. Debugbestanden zijn opgeslagen in storage/debug."
        return f"NH1816 ophalen mislukt. Debugbestanden zijn opgeslagen in storage/debug. Detail: {reason}"

    def _selector_debug(self, page: Any) -> dict[str, Any]:
        return page.evaluate(
            """
            () => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const count = (selector) => {
                    const elements = Array.from(document.querySelectorAll(selector));
                    return { selector, total: elements.length, visible: elements.filter(visible).length };
                };
                return {
                    url: window.location.href,
                    title: document.title,
                    selectors: [
                        count('table'),
                        count('thead'),
                        count('tbody'),
                        count('tr'),
                        count('th'),
                        count('td'),
                        count('[role="grid"]'),
                        count('[role="table"]'),
                        count('[role="row"]'),
                        count('[role="cell"]'),
                        count('[role="gridcell"]'),
                        count('[role="columnheader"]')
                    ],
                    visibleTableTexts: Array.from(document.querySelectorAll('table')).filter(visible).slice(0, 5).map((table) => table.innerText.slice(0, 1000))
                };
            }
            """
        )

    def _write_selector_debug(self, page: Any, payload: dict[str, Any] | None = None) -> None:
        selectors_path = self.debug_dir / "nh1816_selectors.json"
        debug_payload = self._selector_debug(page)
        if payload:
            debug_payload["parser_payload"] = payload
        selectors_path.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
