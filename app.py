from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from agents import MailIntakeAgent
from database import Database
from services import MicrosoftGraphService, OllamaService, TokenStore
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


def future_agents() -> list[dict]:
    return [
        {"naam": "Document Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Klant Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "DDI Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "ANVA Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Schade Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Polis Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Communicatie Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
        {"naam": "Compliance Agent", "status": "placeholder", "omschrijving": "Nog niet functioneel."},
    ]


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
    }

    database.log("Mail Intake Agent", "INFO", "Nieuwe analyse gestart", mail_data.get("subject"))
    analysis = mail_agent.run(mail_data)

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
    return render_template(
        "mailbox.html",
        outlook_status=graph_service.connection_status(),
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
        flash("Outlook is verbonden.", "success")
    except Exception as exc:
        database.log("Outlook", "ERROR", "Microsoft token ophalen mislukt", str(exc))
        flash(f"Outlook verbinden is mislukt: {exc}", "error")
    finally:
        session.pop("microsoft_oauth_state", None)
        session.pop("microsoft_code_verifier", None)

    return redirect(url_for("mailbox"))


@app.route("/mailbox/import", methods=["POST"])
def import_mailbox():
    mode = request.form.get("mode", "latest")
    if mode not in {"latest", "unread", "attachments"}:
        flash("Onbekende importkeuze.", "error")
        return redirect(url_for("mailbox"))

    try:
        result = import_outlook_messages(mode)
        flash(
            f"Import afgerond: {result['imported']} geimporteerd, {result['duplicates']} duplicaten overgeslagen, {result['failed']} fouten.",
            "success",
        )
    except Exception as exc:
        database.log("Outlook", "ERROR", "Outlook import mislukt", str(exc))
        flash(f"Outlook import mislukt: {exc}", "error")
    return redirect(url_for("mailbox"))


def import_outlook_messages(mode: str) -> dict[str, int]:
    import_batch_id = uuid.uuid4().hex
    database.log("Outlook", "INFO", "Outlook import gestart", f"modus={mode}; batch={import_batch_id}")
    messages = graph_service.fetch_messages(mode)
    imported = 0
    duplicates = 0
    failed = 0

    for mail_data in messages:
        mail_data["import_batch_id"] = import_batch_id
        mail_data["source_hash"] = database.build_source_hash(mail_data)
        if database.is_duplicate_mail(mail_data):
            duplicates += 1
            database.log("Outlook", "INFO", "Duplicaat overgeslagen", mail_data.get("subject"))
            continue

        database.log("Mail Intake Agent", "INFO", "Nieuwe Outlook analyse gestart", mail_data.get("subject"))
        analysis = mail_agent.run(mail_data)
        try:
            analysis_id = database.save_mail_analysis(mail_data, analysis)
            imported += 1
            if mail_agent.last_error:
                database.log("Mail Intake Agent", "ERROR", "Analyse mislukt", mail_agent.last_error)
                if "json" in mail_agent.last_error.lower():
                    database.log("Mail Intake Agent", "ERROR", "JSON parse fout", mail_agent.last_error)
            else:
                database.log("Mail Intake Agent", "INFO", "Outlook analyse geslaagd", f"Analyse ID {analysis_id}")
        except Exception as exc:
            failed += 1
            database.log("Database", "ERROR", "Database fout", str(exc))

    database.log(
        "Outlook",
        "INFO",
        "Outlook import afgerond",
        f"batch={import_batch_id}; geimporteerd={imported}; duplicaten={duplicates}; fouten={failed}",
    )
    return {"imported": imported, "duplicates": duplicates, "failed": failed}


@app.route("/logs")
def logs():
    return render_template("logs.html", logs=database.latest_logs(100))


@app.route("/settings")
def settings():
    return render_template("settings.html")


@app.errorhandler(Exception)
def handle_error(error):
    database.log("Applicatie", "ERROR", "Analyse mislukt" if request.path == "/mail-test" else "Applicatiefout", str(error))
    return render_template("error.html", error=error), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
