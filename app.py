from __future__ import annotations

import os
import base64
import csv
import io
import json
import re
import secrets
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv

from agents import DocumentAgent, MailIntakeAgent, WaardemeterAgent
from database import Database
from services import AIAnalysisWorker, MailboxMonitor, MicrosoftGraphService, NH1816PortalService, OllamaService, TokenStore
from services.microsoft_graph_service import create_pkce_pair


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/klaasvis_ai.db").strip()
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-this-secret").strip()
ENABLE_SENT_LEARNING = os.getenv("ENABLE_SENT_LEARNING", "false").strip().lower() == "true"
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
OLLAMA_BODY_CHAR_LIMIT = int(os.getenv("OLLAMA_BODY_CHAR_LIMIT", "1500"))
OLLAMA_ANALYSIS_INTERVAL_SECONDS = int(os.getenv("OLLAMA_ANALYSIS_INTERVAL_SECONDS", "10"))
MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "").strip()
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
EXPECTED_MICROSOFT_REDIRECT_URI = "http://localhost:5000/outlook/callback"
MICROSOFT_REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI", EXPECTED_MICROSOFT_REDIRECT_URI).strip()
if MICROSOFT_REDIRECT_URI != EXPECTED_MICROSOFT_REDIRECT_URI:
    MICROSOFT_REDIRECT_URI = EXPECTED_MICROSOFT_REDIRECT_URI
ALLOWED_OUTLOOK_EMAIL = os.getenv("ALLOWED_OUTLOOK_EMAIL", "").strip().lower()
NH1816_USERNAME = os.getenv("NH1816_USERNAME", "").strip()
NH1816_PASSWORD = os.getenv("NH1816_PASSWORD", "").strip()
NH1816_VALUE_METERS_URL = os.getenv("NH1816_VALUE_METERS_URL", "").strip()
NH1816_HEADLESS = os.getenv("NH1816_HEADLESS", "true").strip().lower() == "true"

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

database = Database(DATABASE_PATH)
database.initialize()
ollama_service = OllamaService(
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    timeout=OLLAMA_TIMEOUT_SECONDS,
    body_char_limit=OLLAMA_BODY_CHAR_LIMIT,
)
mail_agent = MailIntakeAgent(ollama_service, OLLAMA_MODEL)
document_agent = DocumentAgent(ollama_service, OLLAMA_MODEL)
waardemeter_agent = WaardemeterAgent()
token_store = TokenStore("database/microsoft_token.json")
graph_service = MicrosoftGraphService(
    MICROSOFT_TENANT_ID,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_REDIRECT_URI,
    token_store,
    ALLOWED_OUTLOOK_EMAIL,
)
nh1816_service = NH1816PortalService(
    NH1816_USERNAME,
    NH1816_PASSWORD,
    NH1816_VALUE_METERS_URL,
    headless=NH1816_HEADLESS,
    debug_dir=BASE_DIR / "storage" / "debug",
)

OUTLOOK_SCOPES = ["User.Read", "Mail.Read", "offline_access"]
MAIL_AGENT_RESET_MARKER = Path("database/mail_agent_reset_at.txt")
DOCUMENT_STORAGE_PATH = BASE_DIR / "storage" / "documents"
AGENT_ROUTING = {
    "INBOEDELWAARDEMETER": "Waardemeter Agent",
    "HERBOUWWAARDEMETER": "Waardemeter Agent",
    "SCHADE": "Schade Agent",
    "SCHADE_UITKERING": "Schade Agent",
    "WIJZIGING": "Polis Agent",
    "BEËINDIGING": "Polis Agent",
    "BEËINDIGING": "Polis Agent",
    "BEÃ‹INDIGING": "Polis Agent",
    "BEEINDIGING": "Polis Agent",
    "POLISDOCUMENT": "Document Agent",
    "FACTUUR": "Document Agent",
    "KLANTVRAAG": "Communicatie Agent",
}

Path("logs").mkdir(exist_ok=True)
DOCUMENT_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
database.log("Applicatie", "INFO", "Applicatie gestart")


def has_real_microsoft_secret() -> bool:
    return bool(MICROSOFT_CLIENT_SECRET) and MICROSOFT_CLIENT_SECRET != "VUL_HIER_JE_NIEUWE_SECRET_IN"


def startup_yes_no(value: bool) -> str:
    return "ja" if value else "nee"


database.log(
    "Outlook",
    "INFO",
    "Outlook config loaded",
    (
        f"tenant={startup_yes_no(bool(MICROSOFT_TENANT_ID))} "
        f"client_id={startup_yes_no(bool(MICROSOFT_CLIENT_ID))} "
        f"secret={startup_yes_no(has_real_microsoft_secret())} "
        f"redirect_uri={startup_yes_no(bool(MICROSOFT_REDIRECT_URI))} "
        f"allowed_email={startup_yes_no(bool(ALLOWED_OUTLOOK_EMAIL))}"
    ),
)
database.log("Applicatie", "INFO", "Flask session secret loaded", startup_yes_no(bool(APP_SECRET_KEY)))
database.log("Outlook", "INFO", "Sent learning mode", startup_yes_no(ENABLE_SENT_LEARNING))
database.log(
    "Waardemeter Agent",
    "INFO",
    "NH1816 config loaded",
    (
        f"credentials={startup_yes_no(bool(NH1816_USERNAME and NH1816_PASSWORD))} "
        f"value_meters_url={startup_yes_no(bool(NH1816_VALUE_METERS_URL))} "
        f"headless={startup_yes_no(NH1816_HEADLESS)}"
    ),
)


def future_agents() -> list[dict]:
    return [
        {"naam": "Customer Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "DDI Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "ANVA Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Polis Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Schade Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Communicatie Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Compliance Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
    ]


@app.context_processor
def inject_settings() -> dict:
    return {
        "active_model": OLLAMA_MODEL,
        "allowed_outlook_email": ALLOWED_OUTLOOK_EMAIL,
        "sent_learning_enabled": ENABLE_SENT_LEARNING,
        "ollama_timeout_seconds": OLLAMA_TIMEOUT_SECONDS,
        "ollama_body_char_limit": OLLAMA_BODY_CHAR_LIMIT,
        "nh1816_value_meters_url": NH1816_VALUE_METERS_URL,
    }


def mask_value(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return f"{value[:2]}..."
    return f"{value[:visible]}...{value[-visible:]}"


def route_exists(rule: str) -> bool:
    return any(item.rule == rule for item in app.url_map.iter_rules())


def yes_no(value: bool) -> str:
    return "ja" if value else "nee"


def route_mail_analysis(mail_data: dict, analysis: dict) -> dict:
    routed = dict(analysis)
    category = str(routed.get("categorie") or "ONBEKEND").upper()
    if category == "BEEINDIGING":
        category = "BEËINDIGING"
    routed["categorie"] = category
    if category.startswith("BE") and "INDIGING" in category:
        category = "BEËINDIGING"
        routed["categorie"] = category

    source_folder = str(mail_data.get("source_folder") or "unknown").lower()
    direction = str(mail_data.get("direction") or "incoming").lower()
    confidence = float(routed.get("vertrouwen_score", routed.get("vertrouwen", 0.0)) or 0.0)
    routed["vertrouwen_score"] = confidence
    routed["vertrouwen"] = confidence

    if source_folder == "sentitems" or direction == "outgoing":
        routed["processing_status"] = "learning_only"
        routed["volgende_agent"] = ""
        routed["menselijke_controle_nodig"] = False
        routed["requires_human_review"] = False
        routed["reason_for_human_review"] = ""
        routed["reden_menselijke_controle"] = ""
        return routed

    if routed.get("processing_status") in {"ai_unavailable", "ai_timeout"}:
        routed["volgende_agent"] = "Human Review"
        routed["requires_human_review"] = True
        routed["menselijke_controle_nodig"] = True
        routed["reason_for_human_review"] = routed.get("reason_for_human_review") or "Ollama is niet beschikbaar of te traag."
        routed["reden_menselijke_controle"] = routed["reason_for_human_review"]
        routed["routed_at"] = datetime.now().isoformat(timespec="seconds")
        return routed

    if category == "SPAM_OF_ONBELANGRIJK":
        routed["volgende_agent"] = ""
        routed["requires_human_review"] = False
        routed["menselijke_controle_nodig"] = False
        routed["reason_for_human_review"] = ""
        routed["reden_menselijke_controle"] = ""
        routed["processing_status"] = "ignored"
        routed["routed_at"] = datetime.now().isoformat(timespec="seconds")
        return routed

    next_agent = AGENT_ROUTING.get(category, "")
    risky = confidence < 0.80 or category == "ONBEKEND" or category == "BEËINDIGING" or not next_agent
    model_requested_review = bool(
        routed.get("requires_human_review", routed.get("menselijke_controle_nodig", False))
    )
    requires_review = risky or model_requested_review
    routed["volgende_agent"] = next_agent
    routed["requires_human_review"] = requires_review
    routed["menselijke_controle_nodig"] = requires_review
    if requires_review:
        reasons = []
        if category == "ONBEKEND":
            reasons.append("Categorie onbekend.")
        if category == "BEËINDIGING":
            reasons.append("Beëindiging vereist menselijke controle.")
        if confidence < 0.80:
            reasons.append("Lage zekerheid in automatische classificatie.")
        if not next_agent:
            reasons.append("Geen volgende agent bepaald.")
        if routed.get("reason_for_human_review"):
            reasons.append(str(routed["reason_for_human_review"]))
        routed["reason_for_human_review"] = " ".join(dict.fromkeys(reasons)) or "Menselijke controle vereist."
        routed["reden_menselijke_controle"] = routed["reason_for_human_review"]
        routed["processing_status"] = "needs_human"
    else:
        routed["reason_for_human_review"] = ""
        routed["reden_menselijke_controle"] = ""
        routed["processing_status"] = "routed" if next_agent else "new"
    routed["routed_at"] = datetime.now().isoformat(timespec="seconds")
    return routed


def session_key_summary() -> str:
    safe_keys = [
        key
        for key in session.keys()
        if key in {"oauth_state", "oauth_code_verifier", "_flashes"}
    ]
    return ", ".join(sorted(safe_keys)) if safe_keys else "geen"


def load_mail_reset_cutoff() -> datetime | None:
    if not MAIL_AGENT_RESET_MARKER.exists():
        return None
    try:
        raw_value = MAIL_AGENT_RESET_MARKER.read_text(encoding="utf-8").strip()
        if not raw_value:
            return None
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except (OSError, ValueError):
        return None


def parse_graph_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def filter_messages_after_reset(messages: list[dict]) -> tuple[list[dict], int]:
    cutoff = load_mail_reset_cutoff()
    if not cutoff:
        return messages, 0
    kept = []
    skipped = 0
    for message in messages:
        received_at = parse_graph_datetime(message.get("received_at"))
        if received_at and received_at <= cutoff:
            skipped += 1
            continue
        kept.append(message)
    return kept, skipped


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "document")
    return cleaned.strip("._") or "document"


def document_kind_from_attachment(name: str, content_type: str) -> str:
    return document_agent.detect_document_kind(name, content_type)


def store_outlook_attachment(mail_row: dict, attachment: dict) -> dict | None:
    attachment_id = attachment.get("id")
    message_id = mail_row.get("outlook_message_id") or mail_row.get("message_id")
    if not attachment_id or not message_id:
        return None
    content = graph_service.fetch_attachment_content(message_id, attachment_id)
    content_bytes = content.get("contentBytes")
    if not content_bytes:
        return None
    filename = safe_filename(content.get("name") or attachment.get("name") or "attachment.bin")
    target_dir = DOCUMENT_STORAGE_PATH / str(mail_row["id"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    target_path.write_bytes(base64.b64decode(content_bytes))
    return {
        "mail_analysis_id": mail_row["id"],
        "outlook_message_id": message_id,
        "attachment_id": attachment_id,
        "document_name": filename,
        "file_path": str(target_path),
        "content_type": content.get("contentType") or attachment.get("content_type"),
        "file_size": content.get("size") or attachment.get("size") or target_path.stat().st_size,
        "document_kind": document_kind_from_attachment(filename, content.get("contentType") or attachment.get("content_type") or ""),
    }


def enqueue_documents_for_mail(mail_row: dict, analysis: dict) -> int:
    if analysis.get("volgende_agent") != "Document Agent":
        return 0
    try:
        attachments = json.loads(mail_row.get("attachment_metadata") or "[]")
    except json.JSONDecodeError:
        attachments = []
    if not attachments:
        database.log("Document Agent", "INFO", "Geen bijlagen om te queueen", mail_row.get("subject"))
        return 0
    queued = 0
    for attachment in attachments:
        try:
            document_data = store_outlook_attachment(mail_row, attachment)
            if not document_data:
                continue
            document_id = database.save_document_queue_item(document_data)
            if document_id:
                queued += 1
                database.log_document(document_id, "INFO", "Document in queue gezet", document_data.get("document_name"))
                database.log("Document Agent", "INFO", "Document in queue gezet", document_data.get("document_name"))
        except Exception as exc:
            database.log("Document Agent", "ERROR", "Bijlage opslaan mislukt", f"{attachment.get('name')} | {exc}")
    return queued


WAARDEMETER_HEADER_ALIASES = {
    "customer_name": {"klant", "klantnaam", "relatie", "relatienaam", "naam", "verzekeringnemer"},
    "address": {"adres", "straat", "woonadres", "risicoadres"},
    "email": {"email", "e-mail", "emailadres", "mail"},
    "policy_number": {"polis", "polisnummer", "polisnr", "polnr", "policynumber", "policy_number"},
    "branche": {"branche", "branch", "verzekering", "product"},
    "meter_type": {"soort", "type", "soortwaardemeter", "soort_waardemeter", "waardemeter", "meter"},
    "request_date": {"datum", "datumverzoek", "datum_verzoek", "verzoekdatum", "aanvraagdatum"},
    "expiry_date": {
        "verloopdatuminboedelverlengdatumopstal",
        "verloopdatuminboedel",
        "verlengdatumopstal",
        "verloopdatum",
        "verlengdatum",
        "einddatum",
    },
    "handled_date": {"behandeld", "behandelddatum", "datumbehandeld"},
    "portal_status": {"status", "nh1816status", "portalstatus", "portal_status"},
}


def nh1816_config_status() -> dict[str, Any]:
    return {
        "username_present": bool(NH1816_USERNAME),
        "password_present": bool(NH1816_PASSWORD),
        "value_meters_url_present": bool(NH1816_VALUE_METERS_URL),
        "value_meters_url": NH1816_VALUE_METERS_URL,
        "headless": NH1816_HEADLESS,
        "automatic_portal_processing": False,
    }


def current_operator() -> str:
    return os.getenv("USERNAME") or os.getenv("USER") or "lokale_gebruiker"


def hard_delete_allowed() -> bool:
    return os.getenv("ENABLE_ADMIN_HARD_DELETE", "false").strip().lower() == "true"


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def map_waardemeter_row(row: dict[str, Any], source: str) -> dict[str, Any]:
    mapped: dict[str, Any] = {"source": source}
    normalized = {normalize_header(key): value for key, value in row.items()}
    for target, aliases in WAARDEMETER_HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapped[target] = normalized[alias]
                break
    if not mapped.get("meter_type"):
        combined = " ".join(str(value or "") for value in row.values())
        mapped["meter_type"] = combined
    if not mapped.get("portal_status"):
        mapped["portal_status"] = "openstaand"
    return mapped


def parse_csv_waardemeters(raw_data: bytes, source: str) -> list[dict[str, Any]]:
    text = raw_data.decode("utf-8-sig", errors="ignore")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,	,")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except csv.Error:
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return [map_waardemeter_row(row, source) for row in reader if any(row.values())]


def parse_xlsx_waardemeters(raw_data: bytes, source: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(raw_data)) as workbook:
        shared_strings = read_xlsx_shared_strings(workbook)
        sheet_name = first_xlsx_sheet_name(workbook)
        root = ElementTree.fromstring(workbook.read(sheet_name))
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", namespace):
        values: list[str] = []
        for cell in row.findall("a:c", namespace):
            cell_index = xlsx_column_index(cell.attrib.get("r", ""))
            while len(values) < cell_index:
                values.append("")
            values.append(read_xlsx_cell(cell, shared_strings, namespace))
        if any(values):
            rows.append(values)
    if not rows:
        return []
    headers = [value or f"kolom_{index + 1}" for index, value in enumerate(rows[0])]
    items = []
    for values in rows[1:]:
        row = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
        if any(row.values()):
            items.append(map_waardemeter_row(row, source))
    return items


def read_xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings = []
    for item in root.findall("a:si", namespace):
        parts = [node.text or "" for node in item.findall(".//a:t", namespace)]
        strings.append("".join(parts))
    return strings


def first_xlsx_sheet_name(workbook: zipfile.ZipFile) -> str:
    names = [name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
    if not names:
        raise ValueError("Geen werkblad gevonden in Excel-bestand.")
    return sorted(names)[0]


def read_xlsx_cell(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    value = cell.find("a:v", namespace)
    if value is None or value.text is None:
        inline_text = cell.find(".//a:t", namespace)
        return inline_text.text if inline_text is not None and inline_text.text else ""
    raw_value = value.text
    if cell.attrib.get("t") == "s":
        try:
            return shared_strings[int(raw_value)]
        except (ValueError, IndexError):
            return raw_value
    return raw_value


def xlsx_column_index(reference: str) -> int:
    letters = re.sub(r"[^A-Z]", "", (reference or "").upper())
    if not letters:
        return 0
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return max(index - 1, 0)


def parse_pasted_waardemeters(text: str, source: str) -> list[dict[str, Any]]:
    raw = text.strip()
    if not raw:
        return []
    return parse_csv_waardemeters(raw.encode("utf-8"), source)


def import_waardemeter_items(items: list[dict[str, Any]]) -> dict[str, int]:
    imported = 0
    duplicates = 0
    failed = 0
    for item in items:
        try:
            analysis = waardemeter_agent.run(item)
            waardemeter_id = database.save_waardemeter(analysis)
            if waardemeter_id:
                imported += 1
                if analysis.get("task_type") == "WAARDEMETER_REQUEST":
                    database.log("Waardemeter Agent", "INFO", "AI Queue taak aangemaakt", analysis.get("policy_number"))
                else:
                    database.log("Waardemeter Agent", "INFO", "Waardemeter historie opgeslagen", analysis.get("policy_number"))
            else:
                duplicates += 1
        except Exception as exc:
            failed += 1
            database.log("Waardemeter Agent", "ERROR", "Waardemeter import mislukt", str(exc))
    return {"imported": imported, "duplicates": duplicates, "failed": failed}


def import_nh1816_fetch_items(items: list[dict[str, Any]], fetched_at: str) -> dict[str, int]:
    prepared = []
    for item in items:
        mapped = map_waardemeter_row(
            {
                "klantnaam": item.get("klantnaam") or item.get("customer_name") or "",
                "adres": item.get("adres") or item.get("address") or "",
                "email": item.get("email") or "",
                "polisnummer": item.get("polisnummer") or item.get("policy_number") or "",
                "branche": item.get("branche") or "",
                "soort": item.get("meter_type") or item.get("branche") or "",
                "verloopdatum": item.get("expiry_date") or "",
                "behandeld": item.get("handled_date") or "",
                "datum verzoek": item.get("request_date") or "",
                "status": item.get("status") or item.get("portal_status") or "",
            },
            "NH1816 portal",
        )
        mapped["raw_text"] = item.get("raw_text") or ""
        mapped["raw_json"] = item.get("raw_json") or item
        mapped["fetched_at"] = fetched_at
        mapped["action_button_present"] = bool(item.get("action_button_present"))
        mapped["row_state"] = item.get("row_state") or "unknown"
        mapped["row_css_class"] = item.get("row_css_class") or item.get("row_class") or ""
        mapped["background_color"] = item.get("background_color") or item.get("row_background_color") or ""
        prepared.append(mapped)
    return import_waardemeter_items(prepared)


@app.route("/")
def dashboard():
    ollama_online = ollama_service.is_online()
    database.log(
        "Applicatie",
        "INFO",
        "Status Ollama gecontroleerd",
        "online" if ollama_online else "offline",
    )
    show_hidden = request.args.get("show_hidden") == "1"
    stats = database.dashboard_stats(include_hidden=show_hidden)
    return render_template(
        "dashboard.html",
        ollama_status="online" if ollama_online else "offline",
        database_status=database.status(),
        outlook_status=graph_service.connection_status(),
        monitor_status=mailbox_monitor.snapshot(),
        ai_worker_status=ai_analysis_worker.snapshot(),
        document_worker_status=document_analysis_worker.snapshot(),
        stats=stats,
        document_stats=database.document_stats(),
        waardemeter_stats=database.waardemeter_stats(),
        logs=database.latest_logs(10),
        show_hidden=show_hidden,
    )


@app.route("/agents")
def agents():
    return render_template(
        "agents.html",
        mail_agent=mail_agent.metadata(),
        document_agent=document_agent.metadata(),
        waardemeter_agent=waardemeter_agent.metadata(),
        future_agents=future_agents(),
    )


@app.route("/documents")
def documents():
    status_filter = request.args.get("status", "openstaand")
    return render_template(
        "documents.html",
        documents=database.document_analyses(100, status_filter=status_filter),
        document_stats=database.document_stats(),
        status_filter=status_filter,
    )


@app.route("/documents/<int:document_id>/mark-processed", methods=["POST"])
def document_mark_processed(document_id: int):
    database.mark_document_work_status(document_id, "verwerkt", current_operator(), "Handmatig gemarkeerd als verwerkt")
    flash("Document gemarkeerd als verwerkt.", "success")
    return redirect(url_for("documents"))


@app.route("/documents/<int:document_id>/archive", methods=["POST"])
def document_archive(document_id: int):
    database.mark_document_work_status(document_id, "gearchiveerd", current_operator(), "Handmatig gearchiveerd")
    flash("Document gearchiveerd.", "success")
    return redirect(url_for("documents"))


@app.route("/documents/<int:document_id>/delete", methods=["POST"])
def document_delete(document_id: int):
    database.mark_document_work_status(document_id, "verwijderd", current_operator(), "Soft delete via dashboard")
    flash("Document verwijderd uit de standaardweergave.", "success")
    return redirect(url_for("documents"))


@app.route("/documents/<int:document_id>/retry", methods=["POST"])
def document_retry(document_id: int):
    database.retry_document_analysis(document_id, current_operator())
    flash("Document opnieuw in de analysequeue gezet.", "success")
    return redirect(url_for("documents"))


@app.route("/admin/documents/<int:document_id>/hard-delete", methods=["POST"])
def document_hard_delete(document_id: int):
    if not hard_delete_allowed() or request.form.get("confirm") != "DEFINITIEF":
        flash("Definitief verwijderen is alleen beschikbaar voor beheerder met expliciete bevestiging.", "error")
        return redirect(url_for("documents"))
    database.hard_delete_document(document_id, current_operator())
    flash("Document definitief verwijderd.", "success")
    return redirect(url_for("documents", status="alles"))


@app.route("/waardemeters")
def waardemeters():
    status_filter = request.args.get("status", "openstaand")
    return render_template(
        "waardemeters.html",
        waardemeters=database.waardemeters(200, status_filter=status_filter),
        stats=database.waardemeter_stats(),
        nh1816_config=nh1816_config_status(),
        status_filter=status_filter,
    )


@app.route("/waardemeters/fetch-nh1816", methods=["POST"])
def waardemeters_fetch_nh1816():
    if not nh1816_service.configured:
        database.log("Waardemeter Agent", "ERROR", "NH1816 fetch niet gestart: configuratie ontbreekt")
        flash("NH1816 ophalen kan niet starten: vul NH1816_USERNAME, NH1816_PASSWORD en NH1816_VALUE_METERS_URL in .env.", "error")
        return redirect(url_for("waardemeters"))
    try:
        database.log("Waardemeter Agent", "INFO", "NH1816 fetch gestart", "value_meters_url=aanwezig")
        fetch_result = nh1816_service.fetch_value_meters()
        database.log("Waardemeter Agent", "INFO", "NH1816 kolommen gevonden", ", ".join(fetch_result.columns) or "geen tabelkolommen gevonden")
        result = import_nh1816_fetch_items(fetch_result.items, fetch_result.fetched_at)
        database.log(
            "Waardemeter Agent",
            "INFO",
            "NH1816 fetch voltooid",
            f"gevonden={len(fetch_result.items)}; geimporteerd={result['imported']}; duplicaten={result['duplicates']}; fouten={result['failed']}",
        )
        flash(
            f"NH1816 ophalen voltooid: {len(fetch_result.items)} gevonden, {result['imported']} nieuw, {result['duplicates']} duplicaten.",
            "success" if result["failed"] == 0 else "error",
        )
    except Exception as exc:
        database.log("Waardemeter Agent", "ERROR", "NH1816 fetch mislukt", str(exc))
        flash(f"NH1816 ophalen mislukt: {exc}", "error")
    return redirect(url_for("waardemeters"))


@app.route("/waardemeters/import", methods=["GET", "POST"])
def waardemeters_import():
    if request.method == "GET":
        return render_template("waardemeters_import.html", nh1816_config=nh1816_config_status())

    items: list[dict[str, Any]] = []
    upload = request.files.get("waardemeter_file")
    pasted_rows = request.form.get("pasted_rows", "")
    try:
        if upload and upload.filename:
            filename = upload.filename.lower()
            raw_data = upload.read()
            if filename.endswith(".csv"):
                items.extend(parse_csv_waardemeters(raw_data, "CSV upload"))
            elif filename.endswith(".xlsx"):
                items.extend(parse_xlsx_waardemeters(raw_data, "Excel upload"))
            else:
                flash("Gebruik voorlopig CSV of Excel .xlsx voor import.", "error")
                return redirect(url_for("waardemeters_import"))
        if pasted_rows.strip():
            items.extend(parse_pasted_waardemeters(pasted_rows, "Plak-import"))
    except Exception as exc:
        database.log("Waardemeter Agent", "ERROR", "Importbestand lezen mislukt", str(exc))
        flash("Importbestand kon niet worden gelezen.", "error")
        return redirect(url_for("waardemeters_import"))

    if not items:
        flash("Geen waardemeterregels gevonden om te importeren.", "error")
        return redirect(url_for("waardemeters_import"))

    result = import_waardemeter_items(items)
    flash(
        f"Import voltooid: {result['imported']} items, {result['duplicates']} duplicaten, {result['failed']} fouten.",
        "success" if result["failed"] == 0 else "error",
    )
    return redirect(url_for("waardemeters"))


@app.route("/waardemeters/<int:waardemeter_id>")
def waardemeter_detail(waardemeter_id: int):
    item = database.get_waardemeter(waardemeter_id)
    if not item:
        flash("Waardemeter item niet gevonden.", "error")
        return redirect(url_for("waardemeters"))
    return render_template("waardemeter_detail.html", item=item)


@app.route("/waardemeters/<int:waardemeter_id>/approve", methods=["POST"])
def waardemeter_approve(waardemeter_id: int):
    item = database.get_waardemeter(waardemeter_id)
    if not item:
        flash("Waardemeter item niet gevonden.", "error")
        return redirect(url_for("waardemeters"))
    database.update_waardemeter_status(
        waardemeter_id,
        "handmatig_verwerken_nodig",
        "Akkoord gegeven; exacte NH1816 klantregel mag handmatig verwerkt worden",
    )
    database.log("Waardemeter Agent", "INFO", "Akkoord gegeven", item.get("policy_number"))
    database.log("Waardemeter Agent", "INFO", "Handmatig verwerken vereist", item.get("policy_number"))
    flash("Akkoord geregistreerd. Verwerk nu alleen de exacte geselecteerde klantregel handmatig in NH1816.", "success")
    return redirect(url_for("waardemeters"))


@app.route("/waardemeters/<int:waardemeter_id>/mark-nh1816-done", methods=["POST"])
def waardemeter_mark_nh1816_done(waardemeter_id: int):
    item = database.get_waardemeter(waardemeter_id)
    if not item:
        flash("Waardemeter item niet gevonden.", "error")
        return redirect(url_for("waardemeters"))
    database.mark_waardemeter_work_status(waardemeter_id, "verwerkt", current_operator(), "NH1816 handmatig verwerkt")
    database.log("Waardemeter Agent", "INFO", "NH1816 handmatig verwerkt", item.get("policy_number"))
    flash("Waardemeter gemarkeerd als verwerkt in NH1816.", "success")
    return redirect(url_for("waardemeters"))


@app.route("/waardemeters/<int:waardemeter_id>/mark-processed", methods=["POST"])
def waardemeter_mark_processed(waardemeter_id: int):
    database.mark_waardemeter_work_status(waardemeter_id, "verwerkt", current_operator(), "Handmatig gemarkeerd als verwerkt")
    flash("Waardemeter gemarkeerd als verwerkt.", "success")
    return redirect(url_for("waardemeters"))


@app.route("/waardemeters/<int:waardemeter_id>/archive", methods=["POST"])
def waardemeter_archive(waardemeter_id: int):
    database.mark_waardemeter_work_status(waardemeter_id, "gearchiveerd", current_operator(), "Handmatig gearchiveerd")
    flash("Waardemeter gearchiveerd.", "success")
    return redirect(url_for("waardemeters"))


@app.route("/waardemeters/<int:waardemeter_id>/delete", methods=["POST"])
def waardemeter_delete(waardemeter_id: int):
    database.mark_waardemeter_work_status(waardemeter_id, "verwijderd", current_operator(), "Soft delete via dashboard")
    flash("Waardemeter verwijderd uit de standaardweergave.", "success")
    return redirect(url_for("waardemeters"))


@app.route("/admin/waardemeters/<int:waardemeter_id>/hard-delete", methods=["POST"])
def waardemeter_hard_delete(waardemeter_id: int):
    if not hard_delete_allowed() or request.form.get("confirm") != "DEFINITIEF":
        flash("Definitief verwijderen is alleen beschikbaar voor beheerder met expliciete bevestiging.", "error")
        return redirect(url_for("waardemeters"))
    database.hard_delete_waardemeter(waardemeter_id, current_operator())
    flash("Waardemeter definitief verwijderd.", "success")
    return redirect(url_for("waardemeters", status="alles"))


@app.route("/mail-analyses")
def mail_analyses():
    show_hidden = request.args.get("show_hidden") == "1"
    return render_template(
        "mail_analyses.html",
        analyses=database.mail_analyses(100, include_hidden=show_hidden),
        stats=database.dashboard_stats(include_hidden=show_hidden),
        show_hidden=show_hidden,
    )


@app.route("/mail-analyses/retry/<int:analysis_id>", methods=["POST"])
def retry_mail_analysis(analysis_id: int):
    database.retry_analysis(analysis_id)
    database.log("Mail Intake Agent", "INFO", "Mail opnieuw in AI queue gezet", f"id={analysis_id}")
    flash("Mail is opnieuw in de AI analysequeue gezet.", "success")
    return redirect(url_for("mail_analyses"))


@app.route("/mail-test", methods=["GET", "POST"])
def mail_test():
    if request.method == "GET":
        return render_template("mail_test.html")

    mail_data = {
        "sender": request.form.get("sender", "").strip(),
        "recipient": request.form.get("recipient", "").strip(),
        "subject": request.form.get("subject", "").strip(),
        "body": request.form.get("body", "").strip(),
        "has_attachments": request.form.get("has_attachments") == "yes",
        "attachment_names": request.form.get("attachment_names", "").strip(),
        "source": "Handmatig",
        "source_folder": "unknown",
        "direction": "incoming",
    }

    database.log("Mail Intake Agent", "INFO", "Nieuwe analyse gestart", mail_data.get("subject"))
    analysis = route_mail_analysis(mail_data, mail_agent.run(mail_data))

    try:
        analysis_id = database.save_mail_analysis(mail_data, analysis)
        if mail_agent.last_error:
            database.log("Mail Intake Agent", "ERROR", "Analyse mislukt", mail_agent.last_error)
            if "json" in mail_agent.last_error.lower():
                database.log("Mail Intake Agent", "ERROR", "JSON parse fout", mail_agent.last_error)
        else:
            database.log("Mail Intake Agent", "INFO", "Analyse geslaagd", f"Analyse ID {analysis_id}")
    except Exception as exc:
        database.log("Database", "ERROR", "Database fout", str(exc))
        flash("Analyse uitgevoerd, maar opslaan in de database is mislukt.", "error")
        analysis_id = None

    if analysis.get("categorie") == "ONBEKEND" and analysis.get("vertrouwen") == 0.0:
        database.log("Mail Intake Agent", "WARNING", "Analyse fallback gebruikt")

    return render_template(
        "mail_result.html",
        mail_data=mail_data,
        analysis=analysis,
        analysis_id=analysis_id,
    )


@app.route("/mailbox")
def mailbox():
    return redirect(url_for("outlook_accounts"))


@app.route("/outlook/accounts")
def outlook_accounts():
    return render_template(
        "outlook_accounts.html",
        outlook_status=graph_service.connection_status(),
        monitor_status=mailbox_monitor.snapshot(),
        latest_imports=database.dashboard_stats()["latest_imports"],
        allowed_outlook_email=ALLOWED_OUTLOOK_EMAIL,
    )


@app.route("/debug/outlook-config")
def debug_outlook_config():
    config = {
        "tenant_id_present": yes_no(bool(MICROSOFT_TENANT_ID)),
        "client_id_present": yes_no(bool(MICROSOFT_CLIENT_ID)),
        "client_secret_present": yes_no(has_real_microsoft_secret()),
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "allowed_outlook_email": ALLOWED_OUTLOOK_EMAIL,
        "outlook_connect_route": yes_no(route_exists("/outlook/connect")),
        "outlook_callback_route": yes_no(route_exists("/outlook/callback")),
    }
    return render_template("debug_outlook_config.html", config=config)


@app.route("/debug/ollama", methods=["GET", "POST"])
def debug_ollama():
    status = ollama_service.status()
    test_result = None
    if request.method == "POST":
        test_result = ollama_service.test_prompt()
        database.log(
            "Ollama",
            "INFO" if test_result.get("ok") else "ERROR",
            "Ollama debug test uitgevoerd",
            f"status={test_result.get('status')}; latency_ms={test_result.get('latency_ms')}; error={test_result.get('last_error')}",
        )
    return render_template(
        "debug_ollama.html",
        status=status,
        test_result=test_result,
    )


@app.route("/debug/mail-agent-test", methods=["GET", "POST"])
def debug_mail_agent_test():
    examples = {
        "herbouwwaardemeter": "Klant vraagt om een herbouwwaardemeter voor de woning vanwege een nieuwe hypotheek.",
        "schade_uitkering": "Klant vraagt wanneer de schade-uitkering wordt betaald en noemt schadenummer S-12345.",
        "poliswijziging": "Klant wil het kenteken op de autoverzekering wijzigen naar AB-123-C.",
        "beeindiging": "Klant vraagt de woonverzekering per volgende maand te beëindigen.",
        "onbekend": "Klant stuurt een onduidelijke vraag zonder polisnummer of concrete opdracht.",
    }
    result = None
    selected = request.form.get("scenario", "herbouwwaardemeter")
    if request.method == "POST":
        body = examples.get(selected, examples["onbekend"])
        mail_data = {
            "source": "Debug",
            "source_folder": "inbox",
            "direction": "incoming",
            "sender": "debug@klaasvis.local",
            "recipient": ALLOWED_OUTLOOK_EMAIL,
            "subject": f"Debug scenario: {selected}",
            "body": body,
            "has_attachments": False,
            "attachment_names": "",
        }
        result = route_mail_analysis(mail_data, mail_agent.run(mail_data))
        database.log(
            "Mail Intake Agent",
            "INFO",
            "Debug mail-agent-test uitgevoerd",
            f"scenario={selected}; categorie={result.get('categorie')}; status={result.get('processing_status')}",
        )
    return render_template(
        "debug_mail_agent_test.html",
        examples=examples,
        selected=selected,
        result=result,
    )


@app.route("/outlook/connect")
def microsoft_login():
    if not graph_service.configured:
        database.log("Outlook", "ERROR", "Microsoft OAuth niet geconfigureerd")
        flash("Vul eerst de Microsoft .env variabelen in, inclusief een echte client secret.", "error")
        return redirect(url_for("outlook_accounts"))

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = create_pkce_pair()
    session["oauth_state"] = state
    session["oauth_code_verifier"] = code_verifier
    session.modified = True
    authorization_url = graph_service.auth_url(state, code_challenge)
    database.log("Outlook OAuth", "INFO", "Microsoft OAuth login gestart")
    database.log("Outlook OAuth", "INFO", "State aangemaakt", yes_no(bool(state)))
    database.log("Outlook OAuth", "INFO", "Session bevat oauth_state", yes_no(bool(session.get("oauth_state"))))
    database.log("Outlook OAuth", "INFO", "Session keys aanwezig", session_key_summary())
    database.log("Outlook OAuth", "INFO", "Gebruikte tenant id", MICROSOFT_TENANT_ID)
    database.log("Outlook OAuth", "INFO", "Gebruikte client id gemaskeerd", mask_value(MICROSOFT_CLIENT_ID))
    database.log("Outlook OAuth", "INFO", "Gebruikte redirect uri", MICROSOFT_REDIRECT_URI)
    database.log("Outlook OAuth", "INFO", "Gebruikte scopes", " ".join(OUTLOOK_SCOPES))
    database.log("Outlook OAuth", "INFO", "Authorization URL", authorization_url)
    return redirect(authorization_url)


@app.route("/outlook/callback")
def microsoft_callback():
    database.log("Outlook OAuth", "INFO", "Callback aangeroepen", "ja")
    code = request.args.get("code")
    database.log("Outlook OAuth", "INFO", "Code ontvangen", yes_no(bool(code)))
    returned_state = request.args.get("state")
    expected_state = session.get("oauth_state")
    database.log("Outlook OAuth", "INFO", "Callback state ontvangen", yes_no(bool(returned_state)))
    database.log("Outlook OAuth", "INFO", "Session bevat oauth_state", yes_no(bool(expected_state)))
    database.log("Outlook OAuth", "INFO", "Session keys aanwezig", session_key_summary())
    state_valid = bool(returned_state and expected_state and returned_state == expected_state)
    database.log("Outlook OAuth", "INFO", "State match", yes_no(state_valid))

    error = request.args.get("error")
    if error:
        details = request.args.get("error_description", error)
        database.log("Outlook", "ERROR", "Microsoft OAuth mislukt", details)
        flash(f"Microsoft login mislukt: {details}", "error")
        return redirect(url_for("outlook_accounts"))

    if not state_valid:
        database.log("Outlook", "ERROR", "Microsoft OAuth state ongeldig")
        flash(
            "Microsoft login afgebroken: ongeldige sessiestatus. "
            f"State ontvangen: {yes_no(bool(returned_state))}; sessie bevat state: {yes_no(bool(expected_state))}.",
            "error",
        )
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)
        return redirect(url_for("outlook_accounts"))

    session.pop("oauth_state", None)
    session.modified = True

    code_verifier = session.get("oauth_code_verifier")
    if not code or not code_verifier:
        database.log("Outlook", "ERROR", "Microsoft OAuth callback zonder code")
        flash("Microsoft login gaf geen autorisatiecode terug.", "error")
        session.pop("oauth_code_verifier", None)
        return redirect(url_for("outlook_accounts"))

    try:
        database.log("Outlook OAuth", "INFO", "Token exchange gestart")
        token_data = graph_service.exchange_code(code, code_verifier)
        database.log("Outlook OAuth", "INFO", "Token exchange geslaagd")
    except Exception as exc:
        database.log("Outlook OAuth", "ERROR", "Token exchange mislukt", str(exc))
        database.log("Outlook", "ERROR", "Microsoft token ophalen mislukt", str(exc))
        flash(f"Outlook verbinden is mislukt: {exc}", "error")
        session.pop("oauth_code_verifier", None)
        return redirect(url_for("outlook_accounts"))

    try:
        profile = graph_service.profile_from_token(token_data["access_token"])
        database.log("Outlook OAuth", "INFO", "Graph /me geslaagd")
        login_email = graph_service.account_email(profile)
        database.log("Outlook OAuth", "INFO", "Gevonden e-mailadres", login_email)
        allowed_match = graph_service.profile_is_allowed(profile)
        database.log("Outlook OAuth", "INFO", "Allowed email match", yes_no(allowed_match))
        if not allowed_match:
            database.log("Outlook", "ERROR", "Niet-toegestaan Outlook-account geweigerd", login_email)
            flash(
                f"Dit Outlook-account is niet toegestaan. Alleen {ALLOWED_OUTLOOK_EMAIL} mag worden gekoppeld.",
                "error",
            )
            return redirect(url_for("outlook_accounts"))
        graph_service.save_authorized_token(token_data, profile)
        database.log("Outlook", "INFO", "Outlook verbonden")
        database.log("Outlook", "INFO", "Toegestaan Outlook-account gekoppeld", login_email)
        database.log("Outlook", "INFO", "OAuth OK")
        database.log("Outlook", "INFO", "Sync startmoment opgeslagen", graph_service.sync_started_at())
        flash("Outlook gekoppeld. Alleen nieuwe inkomende berichten vanaf nu worden verwerkt.", "success")
    except Exception as exc:
        database.log("Outlook OAuth", "ERROR", "Graph /me mislukt", str(exc))
        database.log("Outlook", "ERROR", "Microsoft Graph profiel ophalen mislukt", str(exc))
        flash(f"Outlook verbinden is mislukt: {exc}", "error")
    finally:
        session.pop("oauth_state", None)
        session.pop("oauth_code_verifier", None)

    return redirect(url_for("outlook_accounts"))


@app.route("/mailbox/import", methods=["GET", "POST"])
def import_mailbox():
    database.log("Mail Agent", "INFO", "Handmatige import route aangeroepen; automatische monitor actief")
    flash("De Mail Intake Agent scant Outlook automatisch elke 15 seconden.", "success")
    return redirect(url_for("outlook_accounts"))


@app.route("/outlook/accounts/sync", methods=["POST"])
def outlook_sync_now():
    return outlook_sync_account(ALLOWED_OUTLOOK_EMAIL)


@app.route("/outlook/sync/<account_id>", methods=["POST"])
def outlook_sync_account(account_id: str):
    if account_id.strip().lower() != ALLOWED_OUTLOOK_EMAIL:
        database.log("Outlook", "ERROR", "Synchronisatie geweigerd voor niet-toegestaan account", account_id)
        flash("Synchronisatie geweigerd: dit Outlook-account is niet toegestaan.", "error")
        return redirect(url_for("outlook_accounts"))
    try:
        database.log("Outlook", "INFO", "Inbox sync gestart")
        result = mailbox_monitor.run_once()
        flash(
            f"Inbox synchronisatie voltooid: {result['imported']} nieuwe mails, {result['duplicates']} duplicaten, {result['failed']} fouten.",
            "success",
        )
    except Exception as exc:
        database.log("Outlook", "ERROR", "Handmatige synchronisatie mislukt", str(exc))
        flash(f"Synchronisatie mislukt: {exc}", "error")
    return redirect(url_for("outlook_accounts"))


@app.route("/outlook/sent-learning/<account_id>", methods=["POST"])
def outlook_sent_learning(account_id: str):
    if account_id.strip().lower() != ALLOWED_OUTLOOK_EMAIL:
        database.log("Outlook", "ERROR", "Sent learning geweigerd voor niet-toegestaan account", account_id)
        flash("Sent learning geweigerd: dit Outlook-account is niet toegestaan.", "error")
        return redirect(url_for("outlook_accounts"))
    if not ENABLE_SENT_LEARNING:
        database.log("Outlook", "INFO", "Sentitems overgeslagen", "ENABLE_SENT_LEARNING=false")
        flash("Leermodus voor verzonden berichten staat uit.", "error")
        return redirect(url_for("outlook_accounts"))
    try:
        result = import_outlook_messages("learning")
        flash(
            f"Leermodus scan voltooid: {result['imported']} verzonden mails opgeslagen, {result['duplicates']} duplicaten, {result['failed']} fouten.",
            "success",
        )
    except Exception as exc:
        database.log("Outlook", "ERROR", "Sent learning scan mislukt", str(exc))
        flash(f"Leermodus scan mislukt: {exc}", "error")
    return redirect(url_for("outlook_accounts"))


@app.route("/outlook/accounts/disconnect", methods=["POST"])
def outlook_disconnect():
    return outlook_disconnect_account(ALLOWED_OUTLOOK_EMAIL)


@app.route("/outlook/disconnect/<account_id>", methods=["POST"])
def outlook_disconnect_account(account_id: str):
    if account_id.strip().lower() != ALLOWED_OUTLOOK_EMAIL:
        database.log("Outlook", "ERROR", "Ontkoppelen geweigerd voor niet-toegestaan account", account_id)
        flash("Ontkoppelen geweigerd: dit Outlook-account is niet toegestaan.", "error")
        return redirect(url_for("outlook_accounts"))
    token_store.clear()
    database.log("Outlook", "INFO", "Outlook-account ontkoppeld", ALLOWED_OUTLOOK_EMAIL)
    flash("Outlook-account is ontkoppeld.", "success")
    return redirect(url_for("outlook_accounts"))


def queued_analysis() -> dict:
    return {
        "categorie": "ONBEKEND",
        "vertrouwen": 0.0,
        "vertrouwen_score": 0.0,
        "prioriteit": "normaal",
        "samenvatting": "",
        "korte_samenvatting": "",
        "voorgestelde_actie": "",
        "voorgestelde_vervolgstap": "",
        "volgende_agent": "",
        "menselijke_controle_nodig": False,
        "requires_human_review": False,
        "reason_for_human_review": "",
        "processing_status": "queued",
        "ai_model": OLLAMA_MODEL,
        "ai_parse_status": "queued",
    }


def cleanup_active_outlook_messages() -> dict[str, int]:
    moved = 0
    deleted = 0
    checked = 0
    for item in database.active_outlook_messages(100):
        message_id = item.get("outlook_message_id") or item.get("message_id")
        if not message_id:
            continue
        checked += 1
        state = graph_service.inbox_message_state(message_id)
        if state == "inbox":
            continue
        if state == "deleted_or_not_found":
            deleted += 1
            database.update_processing_status(item["id"], "deleted_or_not_found", item.get("subject"))
            database.log("Outlook", "INFO", "Mail verwijderd of niet gevonden", f"{message_id} | {item.get('subject')}")
        else:
            moved += 1
            database.update_processing_status(item["id"], "moved_or_archived", item.get("subject"))
            database.log("Outlook", "INFO", "Mail verplaatst of gearchiveerd", f"{message_id} | {item.get('subject')}")
    return {"checked": checked, "moved": moved, "deleted": deleted}


def process_one_queued_mail() -> dict[str, int]:
    mail_row = database.get_next_queued_mail()
    if not mail_row:
        return {"processed": 0, "timeouts": 0, "hidden": 0}

    message_id = mail_row.get("outlook_message_id") or mail_row.get("message_id")
    if mail_row.get("source") == "Outlook" and message_id:
        state = graph_service.inbox_message_state(message_id)
        if state != "inbox":
            status = "deleted_or_not_found" if state == "deleted_or_not_found" else "moved_or_archived"
            database.update_processing_status(mail_row["id"], status, mail_row.get("subject"))
            return {"processed": 0, "timeouts": 0, "hidden": 1}

    database.update_processing_status(mail_row["id"], "analyzing", mail_row.get("subject"))
    database.log("Mail Intake Agent", "INFO", "AI gestart", f"id={mail_row['id']}; subject={mail_row.get('subject')}")

    analysis = route_mail_analysis(mail_row, mail_agent.run(mail_row))
    if analysis.get("processing_status") == "ai_timeout":
        retry_data = dict(mail_row)
        retry_data["retry_minimal"] = True
        database.log("Mail Intake Agent", "WARNING", "Ollama timeout; retry met minimale input", mail_row.get("subject"))
        analysis = route_mail_analysis(retry_data, mail_agent.run(retry_data))
        if analysis.get("processing_status") == "ai_timeout":
            database.update_mail_analysis_result(mail_row["id"], analysis)
            database.log("Mail Intake Agent", "ERROR", "AI timeout na retry", mail_row.get("subject"))
            return {"processed": 1, "timeouts": 1, "hidden": 0}

    database.update_mail_analysis_result(mail_row["id"], analysis)
    queued_documents = enqueue_documents_for_mail(mail_row, analysis)
    if queued_documents:
        database.log("Document Agent", "INFO", "Bijlagen doorgestuurd naar Document Agent", str(queued_documents))
    database.log(
        "Mail Intake Agent",
        "INFO",
        "AI analyse opgeslagen",
        (
            f"id={mail_row['id']}; categorie={analysis.get('categorie')}; "
            f"vertrouwen={analysis.get('vertrouwen_score', analysis.get('vertrouwen'))}; "
            f"volgende_agent={analysis.get('volgende_agent')}; status={analysis.get('processing_status')}"
        ),
    )
    return {"processed": 1, "timeouts": 0, "hidden": 0}


def process_one_queued_document() -> dict[str, int]:
    document_row = database.get_next_queued_document()
    if not document_row:
        return {"processed": 0, "failed": 0}
    database.update_document_status(document_row["id"], "analyzing", document_row.get("document_name"))
    database.log("Document Agent", "INFO", "Documentanalyse gestart", document_row.get("document_name"))
    result = document_agent.run(document_row)
    database.update_document_analysis_result(document_row["id"], result)
    database.log_document(
        document_row["id"],
        "INFO" if result.get("status") != "needs_human" else "WARNING",
        "Documentanalyse opgeslagen",
        f"{result.get('categorie')} | {result.get('documenttype')}",
    )
    database.log(
        "Document Agent",
        "INFO",
        "Documentanalyse opgeslagen",
        f"{document_row.get('document_name')} | {result.get('categorie')} | {result.get('status')}",
    )
    return {"processed": 1, "failed": 0}


def import_outlook_messages(mode: str = "poll") -> dict[str, int]:
    import_batch_id = uuid.uuid4().hex
    database.log("Outlook", "INFO", "Mailbox scan gestart", f"modus={mode}; batch={import_batch_id}")
    cleanup_result = {"checked": 0, "moved": 0, "deleted": 0}
    if mode == "learning":
        if not ENABLE_SENT_LEARNING:
            database.log("Outlook", "INFO", "Sentitems overgeslagen", "ENABLE_SENT_LEARNING=false")
            return {"imported": 0, "duplicates": 0, "failed": 0}
        messages = graph_service.fetch_sent_learning_messages(mode)
        database.log("Outlook", "INFO", "Graph OK", f"{len(messages)} sentitems opgehaald voor leermodus")
    else:
        database.log("Outlook", "INFO", "Inbox sync gestart")
        cleanup_result = cleanup_active_outlook_messages()
        database.log(
            "Outlook",
            "INFO",
            "Actieve Inbox-mails gecontroleerd",
            f"checked={cleanup_result['checked']}; moved={cleanup_result['moved']}; deleted={cleanup_result['deleted']}",
        )
        sync_anchor = graph_service.inbox_sync_anchor()
        database.log("Outlook", "INFO", "Inbox sync anchor", sync_anchor)
        messages = graph_service.fetch_inbox_messages(mode, since=sync_anchor)
        database.log("Outlook", "INFO", "Aantal inbox mails opgehaald", str(len(messages)))
        messages, skipped_by_reset = filter_messages_after_reset(messages)
        if skipped_by_reset:
            database.log("Outlook", "INFO", "Inbox mails overgeslagen door reset-watermark", str(skipped_by_reset))
        database.log("Outlook", "INFO", "Aantal sentitems overgeslagen", "sentitems niet in hoofdworkflow")
    imported = 0
    duplicates = 0
    failed = 0
    latest_received_at = None

    for mail_data in messages:
        latest_received_at = mail_data.get("received_at") or latest_received_at
        database.log("Outlook", "INFO", "Mail opgehaald", mail_data.get("subject"))
        mail_data["import_batch_id"] = import_batch_id
        mail_data["source_hash"] = database.build_source_hash(mail_data)
        if database.is_duplicate_mail(mail_data):
            duplicates += 1
            database.log("Outlook", "INFO", "Duplicaat overgeslagen", mail_data.get("subject"))
            continue

        if mode == "learning":
            database.log("Outlook", "INFO", "Verzonden bericht opgeslagen als leermodus", mail_data.get("subject"))
            analysis = route_mail_analysis(mail_data, queued_analysis())
        else:
            database.log("Mail Intake Agent", "INFO", "Nieuwe inboxmail in AI queue gezet", mail_data.get("subject"))
            analysis = queued_analysis()
        try:
            analysis_id = database.save_mail_analysis(mail_data, analysis)
            imported += 1
            database.log("Database", "INFO", "Queued mail opgeslagen", f"Analyse ID {analysis_id}")
        except Exception as exc:
            failed += 1
            database.log("Database", "ERROR", "Database fout", str(exc))

    database.log(
        "Outlook",
        "INFO",
        "Mailbox scan afgerond",
        f"batch={import_batch_id}; queued={imported}; duplicaten={duplicates}; fouten={failed}; hidden={cleanup_result['moved'] + cleanup_result['deleted']}",
    )
    if mode != "learning":
        graph_service.update_last_sync_at(latest_received_at)
        database.log("Outlook", "INFO", "Last sync bijgewerkt", latest_received_at or "nu")
    database.log("Dashboard", "INFO", "Dashboard bijgewerkt", f"nieuwe_mails={imported}")
    return {"imported": imported, "duplicates": duplicates, "failed": failed}


def outlook_connected() -> bool:
    status = graph_service.connection_status()
    return bool(status.get("connected") and status.get("configured"))


mailbox_monitor = MailboxMonitor(
    scan_callback=import_outlook_messages,
    connected_callback=outlook_connected,
    log_callback=database.log,
    interval_seconds=15,
)
mailbox_monitor.start()

ai_analysis_worker = AIAnalysisWorker(
    process_callback=process_one_queued_mail,
    log_callback=database.log,
    interval_seconds=OLLAMA_ANALYSIS_INTERVAL_SECONDS,
    agent_name="Mail Intake Agent",
)
ai_analysis_worker.start()

document_analysis_worker = AIAnalysisWorker(
    process_callback=process_one_queued_document,
    log_callback=database.log,
    interval_seconds=OLLAMA_ANALYSIS_INTERVAL_SECONDS,
    agent_name="Document Agent",
)
document_analysis_worker.start()


@app.route("/api/status")
def api_status():
    outlook_status = graph_service.connection_status()
    return jsonify(
        {
            "mail_agent": mail_agent.metadata(),
            "outlook": {
                "connected": outlook_status.get("connected"),
                "configured": outlook_status.get("configured"),
                "status": outlook_status.get("status"),
                "allowed_email": outlook_status.get("allowed_email"),
            },
            "monitor": mailbox_monitor.snapshot(),
            "ai_worker": ai_analysis_worker.snapshot(),
            "document_worker": document_analysis_worker.snapshot(),
            "document_stats": database.document_stats(),
            "waardemeter_stats": database.waardemeter_stats(),
            "stats": database.dashboard_stats(),
        }
    )


@app.route("/logs")
def logs():
    return render_template("logs.html", logs=database.latest_logs(100))


@app.route("/settings")
def settings():
    return render_template("settings.html")


@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        database.log("Applicatie", "ERROR", f"HTTP {error.code}", str(error))
        return render_template("error.html", error=error), error.code
    database.log("Applicatie", "ERROR", "Applicatiefout", str(error))
    return render_template("error.html", error=error), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.getenv("FLASK_DEBUG") == "1", use_reloader=False)
