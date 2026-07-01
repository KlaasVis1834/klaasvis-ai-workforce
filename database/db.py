from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mail_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    sender TEXT,
                    recipient TEXT,
                    subject TEXT,
                    body TEXT,
                    has_attachments INTEGER,
                    attachment_names TEXT,
                    category TEXT,
                    confidence REAL,
                    priority TEXT,
                    sender_type TEXT,
                    insurer TEXT,
                    customer_name TEXT,
                    relation_number TEXT,
                    policy_number TEXT,
                    claim_number TEXT,
                    license_plate TEXT,
                    amount TEXT,
                    summary TEXT,
                    suggested_action TEXT,
                    next_agent TEXT,
                    human_review_required INTEGER,
                    raw_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    agent_name TEXT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT
                )
                """
            )

    def status(self) -> str:
        try:
            with self.connect() as connection:
                connection.execute("SELECT 1")
            return "online"
        except sqlite3.Error:
            return "offline"

    def save_mail_analysis(self, mail_data: dict[str, Any], analysis: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO mail_analyses (
                    created_at, sender, recipient, subject, body, has_attachments,
                    attachment_names, category, confidence, priority, sender_type,
                    insurer, customer_name, relation_number, policy_number,
                    claim_number, license_plate, amount, summary, suggested_action,
                    next_agent, human_review_required, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    mail_data.get("sender"),
                    mail_data.get("recipient"),
                    mail_data.get("subject"),
                    mail_data.get("body"),
                    int(bool(mail_data.get("has_attachments"))),
                    mail_data.get("attachment_names"),
                    analysis.get("categorie"),
                    analysis.get("vertrouwen"),
                    analysis.get("prioriteit"),
                    analysis.get("afzender_type"),
                    analysis.get("maatschappij"),
                    analysis.get("klantnaam"),
                    analysis.get("relatienummer"),
                    analysis.get("polisnummer"),
                    analysis.get("schadenummer"),
                    analysis.get("kenteken"),
                    analysis.get("bedrag"),
                    analysis.get("samenvatting"),
                    analysis.get("voorgestelde_actie"),
                    analysis.get("volgende_agent"),
                    int(bool(analysis.get("menselijke_controle_nodig"))),
                    json.dumps(analysis, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def log(self, agent_name: str, level: str, message: str, details: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_logs (created_at, agent_name, level, message, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (datetime.now().isoformat(timespec="seconds"), agent_name, level, message, details),
            )

    def dashboard_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM mail_analyses").fetchone()[0]
            categories = connection.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM mail_analyses
                GROUP BY category
                ORDER BY count DESC, category ASC
                """
            ).fetchall()
            latest = connection.execute(
                """
                SELECT id, created_at, sender, subject, category, confidence, human_review_required
                FROM mail_analyses
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
        return {
            "total_analyses": total,
            "categories": [dict(row) for row in categories],
            "latest_analyses": [dict(row) for row in latest],
        }

    def latest_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, agent_name, level, message, details
                FROM agent_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
