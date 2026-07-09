from __future__ import annotations

import html
import json
import re
import time
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

ALLOWED_AGENTS = {
    "Waardemeter Agent",
    "Schade Agent",
    "Polis Agent",
    "Document Agent",
    "Communicatie Agent",
    "Human Review",
    "",
}

DEFAULT_ANALYSIS = {
    "categorie": "ONBEKEND",
    "subcategorie": None,
    "vertrouwen_score": 0.0,
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
    "bijlagen_aanwezig": False,
    "bijlagen": False,
    "document_vermoeden": None,
    "korte_samenvatting": "",
    "samenvatting": "",
    "voorgestelde_vervolgstap": "",
    "voorgestelde_actie": "",
    "volgende_agent": "Human Review",
    "reden_routing": "",
    "menselijke_controle_nodig": True,
    "requires_human_review": True,
    "reden_menselijke_controle": "",
    "reason_for_human_review": "",
    "processing_status": "needs_human",
    "risico_niveau": "laag",
    "ontbrekende_gegevens": [],
    "redenen_voor_classificatie": [],
    "ai_model": "",
    "ai_raw_response": "",
    "ai_parse_status": "unknown",
    "ai_latency_ms": None,
}


class OllamaService:
    def __init__(self, base_url: str, model: str, timeout: int = 180, body_char_limit: int = 1500) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.body_char_limit = body_char_limit
        self.last_error: str | None = None

    def is_online(self) -> bool:
        status = self.status()
        return bool(status["online"])

    def status(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=3)
            latency_ms = int((time.perf_counter() - started) * 1000)
            response.raise_for_status()
            data = response.json()
            models = self._extract_model_names(data)
            self.last_error = None
            return {
                "online": True,
                "base_url": self.base_url,
                "model": self.model,
                "model_found": self.model in models,
                "models": models,
                "latency_ms": latency_ms,
                "last_error": None,
            }
        except requests.Timeout as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.last_error = str(exc)
            fallback = self.fallback_analysis(
                f"Ollama timeout: {exc}",
                has_attachments=bool(mail_data.get("has_attachments")),
                parse_status="timeout",
                processing_status="ai_timeout",
            )
            fallback["ai_model"] = self.model
            fallback["ai_raw_response"] = raw_response[:4000]
            fallback["ai_latency_ms"] = latency_ms
            return fallback
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.last_error = str(exc)
            return {
                "online": False,
                "base_url": self.base_url,
                "model": self.model,
                "model_found": False,
                "models": [],
                "latency_ms": latency_ms,
                "last_error": str(exc),
            }

    def model_available(self) -> bool:
        status = self.status()
        return bool(status["online"] and status["model_found"])

    def test_prompt(self) -> dict[str, Any]:
        prompt = f'Geef alleen geldige JSON terug: {{"status":"ok","model":"{self.model}"}}'
        started = time.perf_counter()
        try:
            raw_response = self.chat(prompt, system_prompt="Geef uitsluitend geldige JSON terug.")
            latency_ms = int((time.perf_counter() - started) * 1000)
            parsed = self._parse_json_object(raw_response)
            ok = parsed.get("status") == "ok"
            return {
                "ok": ok,
                "status": "ok" if ok else "onverwachte_json",
                "latency_ms": latency_ms,
                "response": parsed,
                "last_error": None,
            }
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.last_error = str(exc)
            return {
                "ok": False,
                "status": "mislukt",
                "latency_ms": latency_ms,
                "response": None,
                "last_error": str(exc),
            }

    def analyze_mail(self, mail_data: dict[str, Any]) -> dict[str, Any]:
        status = self.status()
        if not status["online"]:
            raise ConnectionError(f"Ollama is offline of niet bereikbaar: {status['last_error']}")
        if not status["model_found"]:
            raise RuntimeError(f"Ollama model niet gevonden: {self.model}")

        started = time.perf_counter()
        raw_response = ""
        try:
            raw_response = self.chat(
                self._build_user_prompt(mail_data),
                system_prompt=self._system_prompt(),
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            analysis = self.parse_analysis(raw_response)
            analysis["ai_model"] = self.model
            analysis["ai_raw_response"] = raw_response
            analysis["ai_parse_status"] = "valid_json"
            analysis["ai_latency_ms"] = latency_ms
            self.last_error = None
            return analysis
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.last_error = str(exc)
            fallback = self.fallback_analysis(
                f"Ollama JSON analyse mislukt: {exc}",
                has_attachments=bool(mail_data.get("has_attachments")),
                parse_status="parse_error",
            )
            fallback["ai_model"] = self.model
            fallback["ai_raw_response"] = raw_response[:4000]
            fallback["ai_latency_ms"] = latency_ms
            return fallback

    def chat(self, user_prompt: str, system_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        ollama_data = response.json()
        return (ollama_data.get("message") or {}).get("content", "")

    def parse_analysis(self, raw_response: str) -> dict[str, Any]:
        parsed = self._parse_json_object(raw_response)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama response bevat geen JSON-object.")
        analysis = DEFAULT_ANALYSIS.copy()
        analysis.update(parsed)
        return self._normalize_analysis(analysis)

    def fallback_analysis(
        self,
        reason: str,
        has_attachments: bool = False,
        parse_status: str = "fallback",
        processing_status: str | None = None,
    ) -> dict[str, Any]:
        analysis = DEFAULT_ANALYSIS.copy()
        analysis["bijlagen"] = bool(has_attachments)
        analysis["bijlagen_aanwezig"] = bool(has_attachments)
        analysis["samenvatting"] = "Analyse kon niet automatisch worden uitgevoerd."
        analysis["korte_samenvatting"] = analysis["samenvatting"]
        analysis["voorgestelde_actie"] = "Laat deze e-mail handmatig controleren."
        analysis["voorgestelde_vervolgstap"] = analysis["voorgestelde_actie"]
        analysis["volgende_agent"] = "Human Review"
        analysis["menselijke_controle_nodig"] = True
        analysis["requires_human_review"] = True
        analysis["reason_for_human_review"] = reason
        analysis["reden_menselijke_controle"] = reason
        analysis["processing_status"] = processing_status or (
            "ai_timeout" if "offline" in reason.lower() or "timeout" in reason.lower() else "needs_human"
        )
        analysis["redenen_voor_classificatie"] = [reason]
        analysis["ai_model"] = self.model
        analysis["ai_parse_status"] = parse_status
        return analysis

    def _parse_json_object(self, raw_response: str) -> dict[str, Any]:
        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw_response or "", re.DOTALL)
            if not match:
                raise ValueError("Geen JSON gevonden in Ollama response.")
            return json.loads(match.group(0))

    def _normalize_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        category = str(analysis.get("categorie") or "ONBEKEND").upper()
        if category in {"BEEINDIGING", "BEÃ‹INDIGING", "BEÃƒâ€¹INDIGING"}:
            category = "BEËINDIGING"
        if category not in CATEGORIES:
            category = "ONBEKEND"
        analysis["categorie"] = category

        confidence_value = analysis.get("vertrouwen_score", analysis.get("vertrouwen", 0.0))
        try:
            confidence = float(confidence_value or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        analysis["vertrouwen_score"] = confidence
        analysis["vertrouwen"] = confidence

        analysis["bijlagen_aanwezig"] = bool(
            analysis.get("bijlagen_aanwezig", analysis.get("bijlagen", False))
        )
        analysis["bijlagen"] = analysis["bijlagen_aanwezig"]

        summary = str(analysis.get("korte_samenvatting") or analysis.get("samenvatting") or "")
        action = str(analysis.get("voorgestelde_vervolgstap") or analysis.get("voorgestelde_actie") or "")
        analysis["korte_samenvatting"] = summary
        analysis["samenvatting"] = summary
        analysis["voorgestelde_vervolgstap"] = action
        analysis["voorgestelde_actie"] = action

        next_agent = str(analysis.get("volgende_agent") or "Human Review")
        if next_agent not in ALLOWED_AGENTS:
            next_agent = "Human Review"
        analysis["volgende_agent"] = next_agent

        review_reason = str(
            analysis.get("reden_menselijke_controle")
            or analysis.get("reason_for_human_review")
            or ""
        )
        requires_review = bool(
            analysis.get("menselijke_controle_nodig", analysis.get("requires_human_review", True))
        )
        if confidence < 0.80:
            requires_review = True
            review_reason = review_reason or "Vertrouwen lager dan 0.80."
        if category == "ONBEKEND":
            requires_review = True
            review_reason = review_reason or "Categorie onbekend."
        if category == "BEËINDIGING":
            requires_review = True
            review_reason = review_reason or "Beëindiging vereist menselijke controle."

        analysis["menselijke_controle_nodig"] = requires_review
        analysis["requires_human_review"] = requires_review
        analysis["reden_menselijke_controle"] = review_reason
        analysis["reason_for_human_review"] = review_reason

        if not isinstance(analysis.get("ontbrekende_gegevens"), list):
            analysis["ontbrekende_gegevens"] = []
        if not isinstance(analysis.get("redenen_voor_classificatie"), list):
            analysis["redenen_voor_classificatie"] = []

        for key, default_value in DEFAULT_ANALYSIS.items():
            analysis.setdefault(key, default_value)
        return analysis

    def _extract_model_names(self, data: dict[str, Any]) -> list[str]:
        names = []
        for item in data.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                names.append(str(name))
        return names

    def _system_prompt(self) -> str:
        return """
Je bent de Mail Intake Agent van Klaas Vis, een Nederlands assurantiekantoor.

Je taak:
- analyseer uitsluitend nieuwe inkomende e-mails
- classificeer de mail
- vat samen
- herken belangrijke gegevens
- bepaal welke vervolgstap nodig is
- routeer naar de juiste volgende agent
- vraag alleen menselijke controle als dat nodig is

Je mag nooit:
- doen alsof iets definitief verwerkt is
- e-mails beantwoorden
- ANVA bijwerken
- DDI indexeren
- betalingen uitvoeren
- poliswijzigingen definitief verwerken

Classificaties:
SCHADE, SCHADE_UITKERING, INBOEDELWAARDEMETER, HERBOUWWAARDEMETER,
WIJZIGING, BEËINDIGING, POLISDOCUMENT, FACTUUR, KLANTVRAAG,
SPAM_OF_ONBELANGRIJK, ONBEKEND.

Routing:
INBOEDELWAARDEMETER -> Waardemeter Agent
HERBOUWWAARDEMETER -> Waardemeter Agent
SCHADE -> Schade Agent
SCHADE_UITKERING -> Schade Agent
WIJZIGING -> Polis Agent
BEËINDIGING -> Polis Agent en menselijke controle
POLISDOCUMENT -> Document Agent
FACTUUR -> Document Agent
KLANTVRAAG -> Communicatie Agent
SPAM_OF_ONBELANGRIJK -> ignored
ONBEKEND -> Human Review

Menselijke controle is verplicht bij vertrouwen lager dan 0.80, beëindiging,
klacht, financieel risico, meerdere mogelijke klanten, onduidelijke opdracht,
ontbrekende belangrijke gegevens, mogelijke aansprakelijkheid, schade-uitkering
boven drempelbedrag of twijfel over vervolgactie.

Geef uitsluitend geldige JSON terug. Geen markdown, geen toelichting.
""".strip()

    def _build_user_prompt(self, mail_data: dict[str, Any]) -> str:
        schema = json.dumps(DEFAULT_ANALYSIS, ensure_ascii=False, indent=2)
        body_preview = self._clean_text(mail_data.get("body_preview") or "")
        body = self._clean_text(mail_data.get("body") or "")
        if mail_data.get("retry_minimal"):
            body = body_preview or body[:500]
        else:
            body = self._trim_thread(body, self.body_char_limit)
        attachment_metadata = mail_data.get("attachment_metadata") or []
        return f"""
Gebruik exact deze JSON-structuur:
{schema}

Te analyseren nieuwe inkomende e-mail:
Richting: {mail_data.get("direction", "incoming")}
Bronmap: {mail_data.get("source_folder", "unknown")}
Outlook message id: {mail_data.get("message_id", "")}
Internet message id: {mail_data.get("internet_message_id", "")}
Conversation id: {mail_data.get("conversation_id", "")}
Ontvangen op: {mail_data.get("received_at", "")}
Afzender: {mail_data.get("sender", "")}
Ontvanger: {mail_data.get("recipient", "")}
Onderwerp: {mail_data.get("subject", "")}
Bijlagen aanwezig: {mail_data.get("has_attachments", False)}
Bijlagennamen: {mail_data.get("attachment_names", "")}
Bijlagenmetadata: {json.dumps(attachment_metadata, ensure_ascii=False)[:800]}
BodyPreview: {body_preview[:500]}

Tekst:
{body}
""".strip()

    def _clean_text(self, value: str) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("\r", "\n")
        text = re.sub(r"\n\s*>.*", "\n", text)
        text = re.split(
            r"(?im)^(-{2,}\s*Oorspronkelijk bericht\s*-{2,}|From:|Van:|Sent:|Verzonden:|Onderwerp:)",
            text,
            maxsplit=1,
        )[0]
        text = re.sub(r"(?is)(disclaimer|vertrouwelijk|confidential).{0,1200}$", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _trim_thread(self, value: str, limit: int) -> str:
        return value[: max(200, limit)]
