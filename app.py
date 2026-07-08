from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv

from agents import MailIntakeAgent
from database import Database
from services import MailboxMonitor, MicrosoftGraphService, OllamaService, TokenStore
from services.microsoft_graph_service import create_pkce_pair


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/klaasvis_ai.db").strip()
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-this-secret").strip()
ENABLE_SENT_LEARNING = os.getenv("ENABLE_SENT_LEARNING", "false").strip().lower() == "true"
MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "").strip()
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
EXPECTED_MICROSOFT_REDIRECT_URI = "http://localhost:5000/outlook/callback"
MICROSOFT_REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI", EXPECTED_MICROSOFT_REDIRECT_URI).strip()
if MICROSOFT_REDIRECT_URI != EXPECTED_MICROSOFT_REDIRECT_URI:
    MICROSOFT_REDIRECT_URI = EXPECTED_MICROSOFT_REDIRECT_URI
ALLOWED_OUTLOOK_EMAIL = os.getenv("ALLOWED_OUTLOOK_EMAIL", "").strip().lower()

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

database = Database(DATABASE_PATH)
database.initialize()
ollama_service = OllamaService(OLLAMA_BASE_URL, OLLAMA_MODEL)
mail_agent = MailIntakeAgent(ollama_service, OLLAMA_MODEL)
token_store = TokenStore("database/microsoft_token.json")
graph_service = MicrosoftGraphService(
    MICROSOFT_TENANT_ID,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_REDIRECT_URI,
    token_store,
    ALLOWED_OUTLOOK_EMAIL,
)

OUTLOOK_SCOPES = ["User.Read", "Mail.Read", "offline_access"]
MAIL_AGENT_RESET_MARKER = Path("database/mail_agent_reset_at.txt")
AGENT_ROUTING = {
    "INBOEDELWAARDEMETER": "Waardemeter Agent",
    "HERBOUWWAARDEMETER": "Waardemeter Agent",
    "SCHADE": "Schade Agent",
    "SCHADE_UITKERING": "Schade Agent",
    "WIJZIGING": "Polis Agent",
    "BEËINDIGING": "Polis Agent",
    "BEÃ‹INDIGING": "Polis Agent",
    "BEEINDIGING": "Polis Agent",
    "POLISDOCUMENT": "Document Agent",
    "FACTUUR": "Document Agent",
    "KLANTVRAAG": "Communicatie Agent",
}

Path("logs").mkdir(exist_ok=True)
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


def future_agents() -> list[dict]:
    return [
        {"naam": "Document Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Customer Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "DDI Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "ANVA Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Polis Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Schade Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Waardemeter Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Communicatie Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Compliance Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
    ]


@app.context_processor
def inject_settings() -> dict:
    return {
        "active_model": OLLAMA_MODEL,
        "allowed_outlook_email": ALLOWED_OUTLOOK_EMAIL,
        "sent_learning_enabled": ENABLE_SENT_LEARNING,
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

    source_folder = str(mail_data.get("source_folder") or "unknown").lower()
    direction = str(mail_data.get("direction") or "incoming").lower()
    confidence = float(routed.get("vertrouwen") or 0.0)

    if source_folder == "sentitems" or direction == "outgoing":
        routed["processing_status"] = "learning_only"
        routed["volgende_agent"] = ""
        routed["menselijke_controle_nodig"] = False
        routed["requires_human_review"] = False
        routed["reason_for_human_review"] = ""
        return routed

    next_agent = AGENT_ROUTING.get(category, "")
    risky = confidence < 0.80 or category == "ONBEKEND" or not next_agent
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
        if confidence < 0.80:
            reasons.append("Lage zekerheid in automatische classificatie.")
        if not next_agent:
            reasons.append("Geen volgende agent bepaald.")
        if routed.get("reason_for_human_review"):
            reasons.append(str(routed["reason_for_human_review"]))
        routed["reason_for_human_review"] = " ".join(dict.fromkeys(reasons)) or "Menselijke controle vereist."
        routed["processing_status"] = "needs_human"
    else:
        routed["reason_for_human_review"] = ""
        routed["processing_status"] = "routed" if next_agent else "new"
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


@app.route("/")
def dashboard():
    ollama_online = ollama_service.is_online()
    database.log(
        "Applicatie",
        "INFO",
        "Status Ollama gecontroleerd",
        "online" if ollama_online else "offline",
    )
    stats = database.dashboard_stats()
    return render_template(
        "dashboard.html",
        ollama_status="online" if ollama_online else "offline",
        database_status=database.status(),
        outlook_status=graph_service.connection_status(),
        monitor_status=mailbox_monitor.snapshot(),
        stats=stats,
        logs=database.latest_logs(10),
    )


@app.route("/agents")
def agents():
    return render_template(
        "agents.html",
        mail_agent=mail_agent.metadata(),
        future_agents=future_agents(),
    )


@app.route("/mail-analyses")
def mail_analyses():
    return render_template(
        "mail_analyses.html",
        analyses=database.mail_analyses(100),
        stats=database.dashboard_stats(),
    )


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
        mailbox_monitor.run_once()
        flash("Outlook is verbonden.", "success")
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


def import_outlook_messages(mode: str = "poll") -> dict[str, int]:
    import_batch_id = uuid.uuid4().hex
    database.log("Outlook", "INFO", "Mailbox scan gestart", f"modus={mode}; batch={import_batch_id}")
    if mode == "learning":
        if not ENABLE_SENT_LEARNING:
            database.log("Outlook", "INFO", "Sentitems overgeslagen", "ENABLE_SENT_LEARNING=false")
            return {"imported": 0, "duplicates": 0, "failed": 0}
        messages = graph_service.fetch_sent_learning_messages(mode)
        database.log("Outlook", "INFO", "Graph OK", f"{len(messages)} sentitems opgehaald voor leermodus")
    else:
        database.log("Outlook", "INFO", "Inbox sync gestart")
        messages = graph_service.fetch_inbox_messages(mode)
        database.log("Outlook", "INFO", "Aantal inbox mails opgehaald", str(len(messages)))
        messages, skipped_by_reset = filter_messages_after_reset(messages)
        if skipped_by_reset:
            database.log("Outlook", "INFO", "Inbox mails overgeslagen door reset-watermark", str(skipped_by_reset))
        database.log("Outlook", "INFO", "Aantal sentitems overgeslagen", "sentitems niet in hoofdworkflow")
    imported = 0
    duplicates = 0
    failed = 0
    routed_count = 0
    human_review_count = 0

    for mail_data in messages:
        database.log("Outlook", "INFO", "Mail opgehaald", mail_data.get("subject"))
        mail_data["import_batch_id"] = import_batch_id
        mail_data["source_hash"] = database.build_source_hash(mail_data)
        if database.is_duplicate_mail(mail_data):
            duplicates += 1
            database.log("Outlook", "INFO", "Duplicaat overgeslagen", mail_data.get("subject"))
            continue

        database.log("Mail Intake Agent", "INFO", "Mail ontvangen", mail_data.get("subject"))
        database.log("Mail Intake Agent", "INFO", "Ollama gestart", mail_data.get("internet_message_id"))
        analysis = route_mail_analysis(mail_data, mail_agent.run(mail_data))
        if analysis.get("processing_status") == "routed":
            routed_count += 1
        if analysis.get("requires_human_review"):
            human_review_count += 1
        try:
            analysis_id = database.save_mail_analysis(mail_data, analysis)
            imported += 1
            if mail_agent.last_error:
                database.log("Mail Intake Agent", "ERROR", "Analyse mislukt", mail_agent.last_error)
                if "json" in mail_agent.last_error.lower():
                    database.log("Mail Intake Agent", "ERROR", "JSON parse fout", mail_agent.last_error)
            else:
                database.log("Mail Intake Agent", "INFO", "Analyse voltooid", f"Analyse ID {analysis_id}")
                database.log("Database", "INFO", "Database opgeslagen", f"Analyse ID {analysis_id}")
                if analysis.get("processing_status") == "learning_only":
                    database.log("Outlook", "INFO", "Verzonden bericht opgeslagen als leermodus", mail_data.get("subject"))
                elif analysis.get("volgende_agent"):
                    database.log(
                        "Router",
                        "INFO",
                        "Mail gerouteerd naar volgende agent",
                        f"{analysis.get('volgende_agent')} | {mail_data.get('subject')}",
                    )
        except Exception as exc:
            failed += 1
            database.log("Database", "ERROR", "Database fout", str(exc))

    database.log(
        "Outlook",
        "INFO",
        "Mailbox scan afgerond",
        f"batch={import_batch_id}; geimporteerd={imported}; duplicaten={duplicates}; fouten={failed}; gerouteerd={routed_count}; menselijke_controle={human_review_count}",
    )
    database.log("Router", "INFO", "Aantal mails gerouteerd naar volgende agent", str(routed_count))
    database.log("Router", "INFO", "Aantal mails met menselijke controle", str(human_review_count))
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
