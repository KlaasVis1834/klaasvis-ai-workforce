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
)

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
    return render_template(
        "mailbox.html",
        outlook_status=graph_service.connection_status(),
        monitor_status=mailbox_monitor.snapshot(),
        latest_imports=database.dashboard_stats()["latest_imports"],
    )


@app.route("/auth/microsoft/login")
def microsoft_login():
    if not graph_service.configured:
        database.log("Outlook", "ERROR", "Microsoft OAuth niet geconfigureerd")
        flash("Vul eerst de Microsoft .env variabelen in, inclusief een echte client secret.", "error")
        return redirect(url_for("mailbox"))

    state = uuid.uuid4().hex
    code_verifier, code_challenge = create_pkce_pair()
    session["microsoft_oauth_state"] = state
    session["microsoft_code_verifier"] = code_verifier
    database.log("Outlook", "INFO", "Microsoft OAuth login gestart")
    return redirect(graph_service.auth_url(state, code_challenge))


@app.route("/auth/microsoft/callback")
def microsoft_callback():
    error = request.args.get("error")
    if error:
        details = request.args.get("error_description", error)
        database.log("Outlook", "ERROR", "Microsoft OAuth mislukt", details)
        flash(f"Microsoft login mislukt: {details}", "error")
        return redirect(url_for("mailbox"))

    if request.args.get("state") != session.get("microsoft_oauth_state"):
        database.log("Outlook", "ERROR", "Microsoft OAuth state ongeldig")
        flash("Microsoft login afgebroken: ongeldige sessiestatus.", "error")
        return redirect(url_for("mailbox"))

    code = request.args.get("code")
    code_verifier = session.get("microsoft_code_verifier")
    if not code or not code_verifier:
        database.log("Outlook", "ERROR", "Microsoft OAuth callback zonder code")
        flash("Microsoft login gaf geen autorisatiecode terug.", "error")
        return redirect(url_for("mailbox"))

    try:
        graph_service.exchange_code(code, code_verifier)
        database.log("Outlook", "INFO", "Outlook verbonden")
        database.log("Outlook", "INFO", "OAuth OK")
        mailbox_monitor.run_once()
        flash("Outlook is verbonden.", "success")
    except Exception as exc:
        database.log("Outlook", "ERROR", "Microsoft token ophalen mislukt", str(exc))
        flash(f"Outlook verbinden is mislukt: {exc}", "error")
    finally:
        session.pop("microsoft_oauth_state", None)
        session.pop("microsoft_code_verifier", None)

    return redirect(url_for("mailbox"))


@app.route("/mailbox/import", methods=["GET", "POST"])
def import_mailbox():
    database.log("Mail Agent", "INFO", "Handmatige import route aangeroepen; automatische monitor actief")
    flash("De Mail Intake Agent scant Outlook automatisch elke 15 seconden.", "success")
    return redirect(url_for("mailbox"))


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
    return jsonify(
        {
            "mail_agent": mail_agent.metadata(),
            "outlook": graph_service.connection_status(),
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
