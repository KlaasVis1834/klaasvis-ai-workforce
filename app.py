from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from agents import MailIntakeAgent
from database import Database
from services import MailboxMonitor, MicrosoftGraphService, OllamaService, TokenStore
from services.microsoft_graph_service import create_pkce_pair


load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/klaasvis_ai.db")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-this-secret")
MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "")
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
MICROSOFT_REDIRECT_URI = os.getenv(
    "MICROSOFT_REDIRECT_URI",
    "http://localhost:5000/auth/microsoft/callback",
)
MICROSOFT_TOKEN_PATH = os.getenv("MICROSOFT_TOKEN_PATH", "database/microsoft_token.json")
ALLOWED_OUTLOOK_EMAIL = os.getenv("ALLOWED_OUTLOOK_EMAIL", "").strip().lower()

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

database = Database(DATABASE_PATH)
database.initialize()
ollama_service = OllamaService(OLLAMA_BASE_URL, OLLAMA_MODEL)
mail_agent = MailIntakeAgent(ollama_service, OLLAMA_MODEL)
token_store = TokenStore(MICROSOFT_TOKEN_PATH)
graph_service = MicrosoftGraphService(
    MICROSOFT_TENANT_ID,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_REDIRECT_URI,
    token_store,
    ALLOWED_OUTLOOK_EMAIL,
)

OUTLOOK_SCOPES = ["User.Read", "Mail.Read", "offline_access"]

Path("logs").mkdir(exist_ok=True)
database.log("Applicatie", "INFO", "Applicatie gestart")
removed_rows = database.cleanup_non_production_mail_data()
if removed_rows:
    database.log("Database", "INFO", "Niet-productie maildata verwijderd", str(removed_rows))
removed_logs = database.cleanup_non_production_logs()
if removed_logs:
    database.log("Database", "INFO", "Niet-productie logs verwijderd", str(removed_logs))


@app.context_processor
def inject_settings() -> dict:
    return {"active_model": OLLAMA_MODEL}


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
        monitor_status=mailbox_monitor.snapshot(),
    )


@app.route("/mail-test", methods=["GET", "POST"])
def mail_test():
    database.log("Mail Agent", "INFO", "Handmatige mailtest route aangeroepen; automatische Outlook intake actief")
    flash("De Mail Intake Agent verwerkt Outlook-mail automatisch. Handmatige invoer is uitgeschakeld.", "success")
    return redirect(url_for("dashboard"))


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
        "client_secret_present": yes_no(
            bool(MICROSOFT_CLIENT_SECRET)
            and MICROSOFT_CLIENT_SECRET != "VUL_HIER_JE_NIEUWE_SECRET_IN"
        ),
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "allowed_outlook_email": ALLOWED_OUTLOOK_EMAIL,
        "outlook_connect_route": yes_no(route_exists("/outlook/connect")),
        "outlook_callback_route": yes_no(route_exists("/outlook/callback")),
    }
    return render_template("debug_outlook_config.html", config=config)


@app.route("/auth/microsoft/login")
@app.route("/outlook/connect")
def microsoft_login():
    if not graph_service.configured:
        database.log("Outlook", "ERROR", "Microsoft OAuth niet geconfigureerd")
        flash("Vul eerst de Microsoft .env variabelen in, inclusief een echte client secret.", "error")
        return redirect(url_for("outlook_accounts"))

    state = uuid.uuid4().hex
    code_verifier, code_challenge = create_pkce_pair()
    session["microsoft_oauth_state"] = state
    session["microsoft_code_verifier"] = code_verifier
    authorization_url = graph_service.auth_url(state, code_challenge)
    database.log("Outlook OAuth", "INFO", "Microsoft OAuth login gestart")
    database.log("Outlook OAuth", "INFO", "Gebruikte tenant id", MICROSOFT_TENANT_ID)
    database.log("Outlook OAuth", "INFO", "Gebruikte client id gemaskeerd", mask_value(MICROSOFT_CLIENT_ID))
    database.log("Outlook OAuth", "INFO", "Gebruikte redirect uri", MICROSOFT_REDIRECT_URI)
    database.log("Outlook OAuth", "INFO", "Gebruikte scopes", " ".join(OUTLOOK_SCOPES))
    database.log("Outlook OAuth", "INFO", "Authorization URL", authorization_url)
    return redirect(authorization_url)


@app.route("/auth/microsoft/callback")
@app.route("/outlook/callback")
def microsoft_callback():
    database.log("Outlook OAuth", "INFO", "Callback aangeroepen", "ja")
    code = request.args.get("code")
    database.log("Outlook OAuth", "INFO", "Code ontvangen", yes_no(bool(code)))
    state_valid = request.args.get("state") == session.get("microsoft_oauth_state")
    database.log("Outlook OAuth", "INFO", "State geldig", yes_no(state_valid))

    error = request.args.get("error")
    if error:
        details = request.args.get("error_description", error)
        database.log("Outlook", "ERROR", "Microsoft OAuth mislukt", details)
        flash(f"Microsoft login mislukt: {details}", "error")
        return redirect(url_for("outlook_accounts"))

    if not state_valid:
        database.log("Outlook", "ERROR", "Microsoft OAuth state ongeldig")
        flash("Microsoft login afgebroken: ongeldige sessiestatus.", "error")
        return redirect(url_for("outlook_accounts"))

    code_verifier = session.get("microsoft_code_verifier")
    if not code or not code_verifier:
        database.log("Outlook", "ERROR", "Microsoft OAuth callback zonder code")
        flash("Microsoft login gaf geen autorisatiecode terug.", "error")
        return redirect(url_for("outlook_accounts"))

    try:
        database.log("Outlook OAuth", "INFO", "Token exchange gestart")
        token_data = graph_service.exchange_code(code, code_verifier)
        database.log("Outlook OAuth", "INFO", "Token exchange geslaagd")
    except Exception as exc:
        database.log("Outlook OAuth", "ERROR", "Token exchange mislukt", str(exc))
        database.log("Outlook", "ERROR", "Microsoft token ophalen mislukt", str(exc))
        flash(f"Outlook verbinden is mislukt: {exc}", "error")
        session.pop("microsoft_oauth_state", None)
        session.pop("microsoft_code_verifier", None)
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
        session.pop("microsoft_oauth_state", None)
        session.pop("microsoft_code_verifier", None)

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
        result = mailbox_monitor.run_once()
        flash(
            f"Synchronisatie voltooid: {result['imported']} nieuwe mails, {result['duplicates']} duplicaten, {result['failed']} fouten.",
            "success",
        )
    except Exception as exc:
        database.log("Outlook", "ERROR", "Handmatige synchronisatie mislukt", str(exc))
        flash(f"Synchronisatie mislukt: {exc}", "error")
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
    messages = graph_service.fetch_messages(mode)
    database.log("Outlook", "INFO", "Graph OK", f"{len(messages)} mails opgehaald")
    imported = 0
    duplicates = 0
    failed = 0

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
        analysis = mail_agent.run(mail_data)
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
        except Exception as exc:
            failed += 1
            database.log("Database", "ERROR", "Database fout", str(exc))

    database.log(
        "Outlook",
        "INFO",
        "Mailbox scan afgerond",
        f"batch={import_batch_id}; geimporteerd={imported}; duplicaten={duplicates}; fouten={failed}",
    )
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
