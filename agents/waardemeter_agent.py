from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from agents.base_agent import BaseAgent


INBOEDEL_LINK = "https://www.klaasvis.nl/inboedelwaardemeter/"
HERBOUW_LINK = "https://www.klaasvis.nl/herbouwwaardemeter/"


class WaardemeterAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="Waardemeter Agent",
            version="0.1",
            description="Bereidt NH1816 inboedel- en herbouwwaardemeter acties voor zonder definitieve verwerking.",
            model="regels + concepttemplates",
        )

    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.status = "bezig"
        self.touch()
        item = self.normalize_item(input_data)
        item["proposed_action"] = self.proposed_action(item)
        item["concept_email_subject"] = self.email_subject(item)
        item["concept_email_body"] = self.email_body(item)
        item["anva_memo"] = self.anva_memo(item)
        item["agenda_task"] = "Controle retour waardemeter NH1816"
        item["agenda_due_date"] = self.due_date()
        item["status"] = "concepten_klaar"
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
                or ""
            ),
            "policy_number": self.clean_text(
                input_data.get("policy_number")
                or input_data.get("polisnummer")
                or input_data.get("polis")
                or ""
            ),
            "meter_type": meter_type,
            "insurer": "NH1816",
            "request_date": self.clean_text(
                input_data.get("request_date")
                or input_data.get("datum verzoek")
                or input_data.get("datum")
                or ""
            ),
            "portal_status": self.clean_text(
                input_data.get("portal_status")
                or input_data.get("status")
                or input_data.get("nh1816 status")
                or ""
            ),
            "raw_text": self.clean_text(input_data.get("raw_text") or ""),
            "raw_json": input_data.get("raw_json") or {},
            "fetched_at": input_data.get("fetched_at"),
            "action_button_present": bool(input_data.get("action_button_present")),
        }

    def normalize_meter_type(self, value: str) -> str:
        lowered = self.clean_text(value).lower()
        if "herbouw" in lowered or "opstal" in lowered or "woonhuis" in lowered:
            return "herbouwwaardemeter"
        if "inboedel" in lowered:
            return "inboedelwaardemeter"
        return "onbekend"

    def proposed_action(self, item: dict[str, Any]) -> str:
        if item["meter_type"] == "herbouwwaardemeter":
            return "Stuur klant conceptmail met link naar herbouwwaardemeter en bereid ANVA-memo/rappel voor."
        if item["meter_type"] == "inboedelwaardemeter":
            return "Stuur klant conceptmail met link naar inboedelwaardemeter en bereid ANVA-memo/rappel voor."
        return "Controleer handmatig welk waardemeterformulier NH1816 vraagt."

    def email_subject(self, item: dict[str, Any]) -> str:
        label = "Herbouwwaardemeter" if item["meter_type"] == "herbouwwaardemeter" else "Inboedelwaardemeter"
        policy_number = item.get("policy_number") or "uw polis"
        return f"{label} NH1816 - polis {policy_number}"

    def email_body(self, item: dict[str, Any]) -> str:
        customer_name = item.get("customer_name") or "klant"
        policy_number = item.get("policy_number") or "[polisnummer]"
        if item["meter_type"] == "herbouwwaardemeter":
            link = HERBOUW_LINK
            subject = "herbouwwaardemeter"
            intro = (
                "Aan uw woonhuis kunnen veranderingen plaatsvinden die van invloed zijn op uw verzekering. "
                "NH1816 vraagt daarom of er verbouwingen zijn geweest die invloed hebben op de herbouwwaarde."
            )
            instruction = "Wilt u eventuele wijzigingen doorgeven via de herbouwwaardemeter?"
        elif item["meter_type"] == "inboedelwaardemeter":
            link = INBOEDEL_LINK
            subject = "inboedelwaardemeter"
            intro = (
                "Veranderingen in uw gezinssamenstelling, inkomen of woning kunnen van invloed zijn op de waarde "
                "van uw inboedel. NH1816 vraagt daarom opnieuw een inboedelwaardemeter op."
            )
            instruction = "Wilt u de inboedelwaardemeter invullen?"
        else:
            link = "https://www.klaasvis.nl/"
            subject = "waardemeter"
            intro = "NH1816 vraagt om aanvullende waardemeterinformatie voor uw verzekering."
            instruction = "Wilt u de gevraagde gegevens controleren en aan ons doorgeven?"

        return (
            f"Beste {customer_name},\n\n"
            f"{intro}\n\n"
            f"{instruction}\n"
            f"U kunt het formulier hier invullen: {link}\n\n"
            f"Uw polisnummer is: {policy_number}\n\n"
            "Heeft u vragen of twijfelt u of u iets moet doorgeven, neem dan gerust contact met ons op.\n\n"
            "Met vriendelijke groet,\n"
            "Klaas Vis Verzekeringen"
        )

    def anva_memo(self, item: dict[str, Any]) -> str:
        label = self.readable_meter_type(item["meter_type"])
        today = date.today().isoformat()
        return f"NH1816 verzoekt om ingevulde {label}. Klant aangeschreven op {today}."

    def due_date(self) -> str:
        return (date.today() + timedelta(days=14)).isoformat()

    def readable_meter_type(self, meter_type: str) -> str:
        if meter_type == "herbouwwaardemeter":
            return "herbouwwaardemeter"
        if meter_type == "inboedelwaardemeter":
            return "inboedelwaardemeter"
        return "waardemeter"

    def clean_text(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text
