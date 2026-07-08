from __future__ import annotations

from typing import Any

from agents.base_agent import BaseAgent
from services.ollama_service import OllamaService


class MailIntakeAgent(BaseAgent):
    def __init__(self, ollama_service: OllamaService, model: str) -> None:
        super().__init__(
            name="Mail Intake Agent",
            version="0.3",
            description="Analyseert nieuwe inkomende Outlook-mails via lokale Ollama en routeert naar de juiste volgende agent.",
            model=model,
        )
        self.ollama_service = ollama_service
        self.last_error: str | None = None

    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.status = "bezig"
        self.last_error = None
        self.touch()
        try:
            if not self.ollama_service.is_online():
                raise ConnectionError("Ollama is offline of niet bereikbaar.")
            analysis = self.ollama_service.analyze_mail(input_data)
            analysis["bijlagen"] = bool(input_data.get("has_attachments"))
            self.status = "beschikbaar"
            self.touch()
            return analysis
        except Exception as exc:
            self.status = "fout"
            self.last_error = str(exc)
            self.touch()
            return self.ollama_service.fallback_analysis(
                f"Ollama analyse mislukt: {exc}",
                has_attachments=bool(input_data.get("has_attachments")),
            )
