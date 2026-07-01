from __future__ import annotations

import json
import hashlib
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
                    source TEXT DEFAULT 'Handmatig',
                    received_at TEXT,
                    message_id TEXT,
                    internet_message_id TEXT,
                    conversation_id TEXT,
                    source_hash TEXT,
                    import_batch_id TEXT,
                    sender TEXT,
                    recipient TEXT,
                    subject TEXT,
                    body TEXT,
                    has_attachments INTEGER,
                    attachment_names TEXT,
                    attachment_metadata TEXT,
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
            self._migrate_mail_analyses(connection)
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

    def _migrate_mail_analyses(self, connection: sqlite3.Connection) -> None:
        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(mail_analyses)").fetchall()
        }
        migrations = {
            "source": "ALTER TABLE mail_analyses ADD COLUMN source TEXT DEFAULT 'Handmatig'",
            "received_at": "ALTER TABLE mail_analyses ADD COLUMN received_at TEXT",
            "message_id": "ALTER TABLE mail_analyses ADD COLUMN message_id TEXT",
            "internet_message_id": "ALTER TABLE mail_analyses ADD COLUMN internet_message_id TEXT",
            "conversation_id": "ALTER TABLE mail_analyses ADD COLUMN conversation_id TEXT",
            "source_hash": "ALTER TABLE mail_analyses ADD COLUMN source_hash TEXT",
            "import_batch_id": "ALTER TABLE mail_analyses ADD COLUMN import_batch_id TEXT",
            "attachment_metadata": "ALTER TABLE mail_analyses ADD COLUMN attachment_metadata TEXT",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_analyses_message_id
            ON mail_analyses(message_id)
            WHERE message_id IS NOT NULL AND message_id != ''
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_analyses_internet_message_id
            ON mail_analyses(internet_message_id)
            WHERE internet_message_id IS NOT NULL AND internet_message_id != ''
            """
        )
        connection.execute("DROP INDEX IF EXISTS idx_mail_analyses_source_hash")

    def status(self) -> str:
        try:
            with self.connect() as connection:
                connection.execute("SELECT 1")
            return "online"
        except sqlite3.Error:
            return "offline"

    def save_mail_analysis(self, mail_data: dict[str, Any], analysis: dict[str, Any]) -> int:
        source_hash = mail_data.get("source_hash") or self.build_source_hash(mail_data)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO mail_analyses (
                    created_at, source, received_at, message_id, internet_message_id, conversation_id,
                    source_hash, import_batch_id, sender, recipient, subject, body, has_attachments,
                    attachment_names, attachment_metadata, category, confidence, priority, sender_type,
                    insurer, customer_name, relation_number, policy_number,
                    claim_number, license_plate, amount, summary, suggested_action,
                    next_agent, human_review_required, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    mail_data.get("source", "Handmatig"),
                    mail_data.get("received_at"),
                    mail_data.get("message_id"),
                    mail_data.get("internet_message_id"),
                    mail_data.get("conversation_id"),
                    source_hash,
                    mail_data.get("import_batch_id"),
                    mail_data.get("sender"),
                    mail_data.get("recipient"),
                    mail_data.get("subject"),
                    mail_data.get("body"),
                    int(bool(mail_data.get("has_attachments"))),
                    mail_data.get("attachment_names"),
                    json.dumps(mail_data.get("attachment_metadata") or [], ensure_ascii=False),
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

    def is_duplicate_mail(self, mail_data: dict[str, Any]) -> bool:
        source_hash = mail_data.get("source_hash") or self.build_source_hash(mail_data)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM mail_analyses
                WHERE (message_id IS NOT NULL AND message_id != '' AND message_id = ?)
                   OR (internet_message_id IS NOT NULL AND internet_message_id != '' AND internet_message_id = ?)
                   OR (source_hash IS NOT NULL AND source_hash != '' AND source_hash = ?)
                LIMIT 1
                """,
                (
                    mail_data.get("message_id"),
                    mail_data.get("internet_message_id"),
                    source_hash,
                ),
            ).fetchone()
        return row is not None

    def build_source_hash(self, mail_data: dict[str, Any]) -> str:
        hash_input = "|".join(
            [
                str(mail_data.get("sender") or ""),
                str(mail_data.get("subject") or ""),
                str(mail_data.get("received_at") or ""),
                str(mail_data.get("body") or ""),
            ]
        )
        return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

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
        today = datetime.now().date().isoformat()
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM mail_analyses").fetchone()[0]
            today_total = connection.execute(
                "SELECT COUNT(*) FROM mail_analyses WHERE created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]
            error_total = connection.execute(
                "SELECT COUNT(*) FROM agent_logs WHERE level = 'ERROR'"
            ).fetchone()[0]
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
                SELECT id, created_at, source, received_at, sender, subject, category,
                       confidence, human_review_required, import_batch_id
                FROM mail_analyses
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
            sources = connection.execute(
                """
                SELECT source, COUNT(*) AS count
                FROM mail_analyses
                GROUP BY source
                ORDER BY count DESC, source ASC
                """
            ).fetchall()
            latest_imports = connection.execute(
                """
                SELECT id, created_at, source, received_at, sender, subject, category,
                       confidence, import_batch_id
                FROM mail_analyses
                WHERE source = 'Outlook'
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
            latest_errors = connection.execute(
                """
                SELECT id, created_at, agent_name, message, details
                FROM agent_logs
                WHERE level = 'ERROR'
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
        return {
            "total_analyses": total,
            "today_analyses": today_total,
            "error_count": error_total,
            "categories": [dict(row) for row in categories],
            "latest_analyses": [dict(row) for row in latest],
            "sources": [dict(row) for row in sources],
            "latest_imports": [dict(row) for row in latest_imports],
            "latest_errors": [dict(row) for row in latest_errors],
        }

    def mail_analyses(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, source, received_at, sender, recipient, subject,
                       category, confidence, priority, human_review_required, summary
                FROM mail_analyses
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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
