from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from agents import MailIntakeAgent
from database import Database
from services import OllamaService


load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/klaasvis_ai.db")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-this-secret")

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

database = Database(DATABASE_PATH)
database.initialize()
ollama_service = OllamaService(OLLAMA_BASE_URL, OLLAMA_MODEL)
mail_agent = MailIntakeAgent(ollama_service, OLLAMA_MODEL)

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
