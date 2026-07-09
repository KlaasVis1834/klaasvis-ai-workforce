from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from agents.base_agent import BaseAgent
from services.ollama_service import OllamaService


DOCUMENT_CATEGORIES = {
    "polisblad",
    "schadeformulier",
    "expertiserapport",
    "factuur",
    "betaalbewijs",
    "inboedelwaardemeter",
    "herbouwwaardemeter",
    "correspondentie",
    "offerte",
    "royementsverzoek",
    "onbekend",
}

DEFAULT_DOCUMENT_ANALYSIS = {
    "relatievoorstel": None,
    "polisvoorstel": None,
    "schadevoorstel": None,
    "documenttype": "onbekend",
    "categorie": "onbekend",
    "samenvatting": "",
    "vertrouwen_score": 0.0,
    "menselijke_controle_nodig": True,
    "reden_controle": "Onvoldoende zekerheid.",
    "klantnaam": None,
    "polisnummer": None,
    "schadenummer": None,
    "kenteken": None,
    "maatschappij": None,
    "datum": None,
    "bedrag": None,
    "risico_niveau": "laag",
    "status": "needs_human",
}


class DocumentAgent(BaseAgent):
    def __init__(self, ollama_service: OllamaService, model: str, text_limit: int = 4000) -> None:
        super().__init__(
            name="Document Agent",
            version="0.1",
            description="Analyseert Outlook-bijlagen en maakt een DDI-indexvoorstel zonder definitieve koppeling.",
            model=model,
        )
        self.ollama_service = ollama_service
        self.text_limit = text_limit
        self.last_error: str | None = None

    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.status = "bezig"
        self.last_error = None
        self.touch()
        started = time.perf_counter()
        extracted_text = ""
        raw_response = ""
        try:
            document_path = Path(input_data.get("file_path") or "")
            document_kind = self.detect_document_kind(
                input_data.get("document_name") or document_path.name,
                input_data.get("content_type") or "",
            )
            extracted_text = self.extract_text(document_path, document_kind)
            prompt_payload = {
                **input_data,
                "document_kind": document_kind,
                "extracted_text": extracted_text[: self.text_limit],
            }
            raw_response = self.ollama_service.chat(
                self._build_prompt(prompt_payload),
                system_prompt=self._system_prompt(),
            )
            parsed = self._parse_json(raw_response)
            result = self._normalize_result(parsed)
            result["extracted_text"] = extracted_text[: self.text_limit]
            result["document_kind"] = document_kind
            result["ai_model"] = self.model
            result["ai_raw_response"] = raw_response
            result["ai_parse_status"] = "valid_json"
            result["ai_latency_ms"] = int((time.perf_counter() - started) * 1000)
            self.status = "beschikbaar"
            self.touch()
            return result
        except Exception as exc:
            self.status = "fout"
            self.last_error = str(exc)
            self.touch()
            result = DEFAULT_DOCUMENT_ANALYSIS.copy()
            result["samenvatting"] = "Document kon niet automatisch worden geanalyseerd."
            result["reden_controle"] = f"Document Agent fout: {exc}"
            result["extracted_text"] = extracted_text[: self.text_limit]
            result["ai_model"] = self.model
            result["ai_raw_response"] = raw_response[:4000]
            result["ai_parse_status"] = "fallback"
            result["ai_latency_ms"] = int((time.perf_counter() - started) * 1000)
            return result

    def detect_document_kind(self, filename: str, content_type: str) -> str:
        name = filename.lower()
        content_type = content_type.lower()
        if name.endswith(".pdf") or "pdf" in content_type:
            return "pdf"
        if name.endswith((".docx", ".doc")) or "word" in content_type:
            return "word"
        if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")) or content_type.startswith("image/"):
            return "image"
        if name.endswith((".eml", ".msg")) or "message" in content_type:
            return "email_attachment"
        if name.endswith((".txt", ".csv")) or content_type.startswith("text/"):
            return "text"
        return "other"

    def extract_text(self, path: Path, document_kind: str) -> str:
        if not path.exists():
            return ""
        if document_kind == "word" and path.suffix.lower() == ".docx":
            return self._extract_docx_text(path)
        if document_kind == "pdf":
            return self._extract_pdf_text(path)
        if document_kind == "text":
            return path.read_text(encoding="utf-8", errors="ignore")
        if document_kind == "image":
            return "OCR placeholder: afbeelding of scan moet later met OCR worden gelezen."
        if document_kind == "email_attachment":
            return path.read_text(encoding="utf-8", errors="ignore")
        return ""

    def _extract_docx_text(self, path: Path) -> str:
        with zipfile.ZipFile(path) as docx:
            xml_data = docx.read("word/document.xml")
        root = ElementTree.fromstring(xml_data)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        texts = [node.text or "" for node in root.findall(".//w:t", namespace)]
        return " ".join(texts)

    def _extract_pdf_text(self, path: Path) -> str:
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            data = path.read_bytes()[:200000]
            text = data.decode("latin-1", errors="ignore")
            text = re.sub(r"[^A-Za-z0-9À-ÿ.,;:€/\-\s]", " ", text)
            text = re.sub(r"\s+", " ", text)
            return text[: self.text_limit]

    def _parse_json(self, raw_response: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw_response or "", re.DOTALL)
            if not match:
                raise ValueError("Geen JSON gevonden in Document Agent response.")
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("Document Agent response is geen JSON-object.")
        return parsed

    def _normalize_result(self, parsed: dict[str, Any]) -> dict[str, Any]:
        result = DEFAULT_DOCUMENT_ANALYSIS.copy()
        result.update(parsed)
        category = str(result.get("categorie") or "onbekend").lower()
        if category not in DOCUMENT_CATEGORIES:
            category = "onbekend"
        result["categorie"] = category
        result["documenttype"] = result.get("documenttype") or category
        try:
            confidence = float(result.get("vertrouwen_score") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        result["vertrouwen_score"] = confidence
        if confidence < 0.80 or category == "onbekend":
            result["menselijke_controle_nodig"] = True
            result["reden_controle"] = result.get("reden_controle") or "Lage zekerheid of onbekende documentcategorie."
            result["status"] = "needs_human"
        else:
            result["menselijke_controle_nodig"] = bool(result.get("menselijke_controle_nodig", False))
            result["status"] = "analyzed" if not result["menselijke_controle_nodig"] else "needs_human"
        return result

    def _system_prompt(self) -> str:
        return """
Je bent de Document Agent van Klaas Vis, een Nederlands assurantiekantoor.
Je analyseert bijlagen uit inkomende Outlook-mails en maakt alleen een voorstel.
Je mag nooit definitief indexeren in DDI, ANVA aanpassen of documenten definitief koppelen.
Geef uitsluitend geldige JSON terug.
""".strip()

    def _build_prompt(self, data: dict[str, Any]) -> str:
        schema = json.dumps(DEFAULT_DOCUMENT_ANALYSIS, ensure_ascii=False, indent=2)
        return f"""
Classificeer dit document en maak een DDI-indexvoorstel.

Toegestane categorieen:
{", ".join(sorted(DOCUMENT_CATEGORIES))}

JSON-schema:
{schema}

Document:
Naam: {data.get("document_name", "")}
Content type: {data.get("content_type", "")}
Bestandstype: {data.get("document_kind", "")}
Gekoppelde mail: {data.get("mail_subject", "")}

Tekst:
{data.get("extracted_text", "")}
""".strip()
