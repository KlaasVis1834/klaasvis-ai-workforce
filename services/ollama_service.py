from __future__ import annotations

import json
import re
from typing import Any

import requests


CATEGORIES = {
    "SCHADE",
    "SCHADE_UITKERING",
    "INBOEDELWAARDEMETER",
    "HERBOUWWAARDEMETER",
    "WIJZIGING",
    "BEËINDIGING",
    "POLISDOCUMENT",
    "FACTUUR",
    "KLANTVRAAG",
    "SPAM_OF_ONBELANGRIJK",
    "ONBEKEND",
}

DEFAULT_ANALYSIS = {
    "categorie": "ONBEKEND",
    "vertrouwen": 0.0,
    "prioriteit": "normaal",
    "afzender_type": "onbekend",
    "maatschappij": None,
    "klantnaam": None,
    "relatienummer": None,
    "polisnummer": None,
    "schadenummer": None,
    "kenteken": None,
    "bedrag": None,
    "bijlagen": False,
    "samenvatting": "",
    "voorgestelde_actie": "",
    "volgende_agent": "",
    "menselijke_controle_nodig": True,
    "requires_human_review": True,
    "reason_for_human_review": "",
    "redenen_voor_classificatie": [],
}


class OllamaService:
    def __init__(self, base_url: str, model: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_online(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=1)
            return response.ok
        except requests.RequestException:
            return False

    def analyze_mail(self, mail_data: dict[str, Any]) -> dict[str, Any]:
        prompt = self._build_prompt(mail_data)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        response = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        ollama_data = response.json()
        return self.parse_analysis(ollama_data.get("response", ""))

    def parse_analysis(self, raw_response: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            parsed = self._extract_json(raw_response)

        if not isinstance(parsed, dict):
            raise ValueError("Ollama response bevat geen JSON-object.")

        analysis = DEFAULT_ANALYSIS.copy()
        analysis.update(parsed)
        return self._normalize_analysis(analysis)

    def fallback_analysis(self, reason: str, has_attachments: bool = False) -> dict[str, Any]:
        analysis = DEFAULT_ANALYSIS.copy()
        analysis["bijlagen"] = bool(has_attachments)
        analysis["samenvatting"] = "Analyse kon niet automatisch worden uitgevoerd."
        analysis["voorgestelde_actie"] = "Laat deze e-mail handmatig controleren."
        analysis["requires_human_review"] = True
        analysis["reason_for_human_review"] = reason
        analysis["redenen_voor_classificatie"] = [reason]
        return analysis

    def _extract_json(self, raw_response: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if not match:
            raise ValueError("Geen JSON gevonden in Ollama response.")
        return json.loads(match.group(0))

    def _normalize_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        category = str(analysis.get("categorie") or "ONBEKEND").upper()
        if category == "BEEINDIGING":
            category = "BEËINDIGING"
        if category not in CATEGORIES:
            category = "ONBEKEND"
        analysis["categorie"] = category

        try:
            confidence = float(analysis.get("vertrouwen") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        analysis["vertrouwen"] = max(0.0, min(1.0, confidence))

        if analysis["vertrouwen"] < 0.80:
            analysis["menselijke_controle_nodig"] = True
            if not analysis.get("reason_for_human_review"):
                analysis["reason_for_human_review"] = "Lage zekerheid in automatische classificatie."
        else:
            analysis["menselijke_controle_nodig"] = bool(
                analysis.get("menselijke_controle_nodig", False)
            )
        analysis["requires_human_review"] = bool(analysis["menselijke_controle_nodig"])
        if analysis["categorie"] == "ONBEKEND":
            analysis["menselijke_controle_nodig"] = True
            analysis["requires_human_review"] = True
            if not analysis.get("reason_for_human_review"):
                analysis["reason_for_human_review"] = "Categorie onbekend."

        analysis["bijlagen"] = bool(analysis.get("bijlagen", False))
        if not isinstance(analysis.get("redenen_voor_classificatie"), list):
            analysis["redenen_voor_classificatie"] = []

        for key, default_value in DEFAULT_ANALYSIS.items():
            analysis.setdefault(key, default_value)

        return analysis

    def _build_prompt(self, mail_data: dict[str, Any]) -> str:
        schema = json.dumps(DEFAULT_ANALYSIS, ensure_ascii=False, indent=2)
        categories = ", ".join(sorted(CATEGORIES))
        return f"""
Je bent een administratieve intake-agent voor een Nederlands assurantiekantoor.
Je mag alleen classificeren, samenvatten en een niet-definitieve vervolgstap voorstellen.
Je mag nooit doen alsof iets definitief verwerkt is.
Je voert geen e-mails, ANVA-acties, DDI-acties of andere automatische verwerking uit.
Herken geldbedragen, polisnummers, schadenummers, kentekens en klantnamen voorzichtig.
Bij twijfel kies je categorie ONBEKEND en is menselijke controle nodig.
Als vertrouwen lager is dan 0.80, zet menselijke_controle_nodig op true.

Geef uitsluitend geldige JSON terug volgens dit schema:
{schema}

Kies exact een categorie uit:
{categories}

Te analyseren e-mail:
Richting: {mail_data.get("direction", "incoming")}
Bronmap: {mail_data.get("source_folder", "unknown")}
Afzender: {mail_data.get("sender", "")}
Ontvanger: {mail_data.get("recipient", "")}
Onderwerp: {mail_data.get("subject", "")}
Bijlagen aanwezig: {mail_data.get("has_attachments", False)}
Bijlagennamen: {mail_data.get("attachment_names", "")}

Tekst:
{mail_data.get("body", "")}

Vul altijd samenvatting, voorgestelde_actie, volgende_agent, requires_human_review en reason_for_human_review.
Menselijke controle is alleen nodig bij lage zekerheid, risico, ontbrekende gegevens of ONBEKEND.
""".strip()
