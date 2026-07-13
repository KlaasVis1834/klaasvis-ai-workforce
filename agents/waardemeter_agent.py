from __future__ import annotations

import re
from typing import Any

from agents.base_agent import BaseAgent


class WaardemeterAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="Waardemeter Agent",
            version="0.1",
            description="Haalt openstaande NH1816 waardemeters op, herkent grijze regels en zet taken in de AI Queue.",
            model="regels + portal parser",
        )

    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.status = "bezig"
        self.touch()
        item = self.normalize_item(input_data)
        item["processing_status"] = self.processing_status(item)
        item["task_type"] = "WAARDEMETER_REQUEST" if item["processing_status"] == "nieuw_verzoek" else ""
        item["task_payload"] = self.task_payload(item) if item["task_type"] else {}
        self.status = "beschikbaar"
        self.touch()
        return item

    def normalize_item(self, input_data: dict[str, Any]) -> dict[str, Any]:
        meter_type = self.normalize_meter_type(
            input_data.get("meter_type")
            or input_data.get("soort waardemeter")
            or input_data.get("soort")
            or input_data.get("type")
            or input_data.get("waardemeter")
            or input_data.get("branche")
            or input_data.get("raw_text")
            or ""
        )
        return {
            "source": input_data.get("source") or "Import",
            "customer_name": self.clean_text(
                input_data.get("customer_name")
                or input_data.get("klantnaam")
                or input_data.get("klant")
                or input_data.get("relatie")
                or input_data.get("naam")
                or ""
            ),
            "address": self.clean_text(
                input_data.get("address")
                or input_data.get("adres")
                or input_data.get("straat")
                or ""
            ),
            "email": self.clean_text(
                input_data.get("email")
                or input_data.get("e-mail")
                or input_data.get("emailadres")
                or ""
            ),
            "policy_number": self.clean_text(
                input_data.get("policy_number")
                or input_data.get("polisnummer")
                or input_data.get("polis")
                or ""
            ),
            "meter_type": meter_type,
            "branche": self.normalize_branch(input_data.get("branche") or input_data.get("branch") or input_data.get("raw_text") or ""),
            "insurer": "NH1816",
            "request_date": self.clean_text(
                input_data.get("request_date")
                or input_data.get("datum verzoek")
                or input_data.get("datum")
                or ""
            ),
            "expiry_date": self.clean_text(
                input_data.get("expiry_date")
                or input_data.get("verloopdatum")
                or input_data.get("verlengdatum")
                or input_data.get("verloopdatum/verlengdatum")
                or ""
            ),
            "handled_date": self.clean_text(
                input_data.get("handled_date")
                or input_data.get("behandeld")
                or input_data.get("behandeld datum")
                or ""
            ),
            "portal_status": self.clean_text(
                input_data.get("portal_status")
                or input_data.get("status")
                or input_data.get("nh1816 status")
                or ""
            ),
            "row_css_class": self.clean_text(input_data.get("row_css_class") or input_data.get("row_class") or ""),
            "background_color": self.clean_text(input_data.get("background_color") or input_data.get("row_background_color") or ""),
            "raw_text": self.clean_text(input_data.get("raw_text") or ""),
            "raw_json": input_data.get("raw_json") or {},
            "fetched_at": input_data.get("fetched_at"),
            "action_button_present": bool(input_data.get("action_button_present")),
            "row_state": self.normalize_row_state(input_data.get("row_state") or input_data.get("regel_status") or input_data.get("status") or ""),
        }

    def normalize_meter_type(self, value: str) -> str:
        lowered = self.clean_text(value).lower()
        if "herbouw" in lowered or "opstal" in lowered or "woonhuis" in lowered:
            return "herbouwwaardemeter"
        if "inboedel" in lowered:
            return "inboedelwaardemeter"
        return ""

    def normalize_branch(self, value: str) -> str:
        lowered = self.clean_text(value).lower()
        if "opstal" in lowered or "herbouw" in lowered or "woonhuis" in lowered:
            return "Opstal"
        if "inboedel" in lowered:
            return "Inboedel"
        return ""

    def normalize_row_state(self, value: str) -> str:
        lowered = self.clean_text(value).lower()
        if "groen" in lowered or "green" in lowered or "processed" in lowered:
            return "processed"
        if "grijs" in lowered or "grey" in lowered or "gray" in lowered or "open" in lowered or not lowered:
            return "open"
        return lowered

    def processing_status(self, item: dict[str, Any]) -> str:
        if item.get("row_state") == "processed" or item.get("portal_status") == "verwerkt":
            return "verwerkt"
        return "nieuw_verzoek"

    def task_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "klant": item.get("customer_name") or "",
            "polisnummer": item.get("policy_number") or "",
            "email": item.get("email") or "",
            "adres": item.get("address") or "",
            "type": "HERBOUW" if item.get("meter_type") == "herbouwwaardemeter" else "INBOEDEL",
            "maatschappij": "NH1816",
            "status": "waiting_for_next_agent",
        }

    def clean_text(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text
