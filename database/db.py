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
                    outlook_message_id TEXT,
                    message_id TEXT,
                    internet_message_id TEXT,
                    conversation_id TEXT,
                    source_folder TEXT DEFAULT 'unknown',
                    processing_status TEXT DEFAULT 'new',
                    direction TEXT DEFAULT 'incoming',
                    source_hash TEXT,
                    import_batch_id TEXT,
                    sender TEXT,
                    recipient TEXT,
                    subject TEXT,
                    body_preview TEXT,
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
                    requires_human_review INTEGER,
                    reason_for_human_review TEXT,
                    ai_model TEXT,
                    ai_raw_response TEXT,
                    ai_parse_status TEXT,
                    ai_latency_ms INTEGER,
                    analysis_attempts INTEGER DEFAULT 0,
                    routed_at TEXT,
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
            "outlook_message_id": "ALTER TABLE mail_analyses ADD COLUMN outlook_message_id TEXT",
            "message_id": "ALTER TABLE mail_analyses ADD COLUMN message_id TEXT",
            "internet_message_id": "ALTER TABLE mail_analyses ADD COLUMN internet_message_id TEXT",
            "conversation_id": "ALTER TABLE mail_analyses ADD COLUMN conversation_id TEXT",
            "source_folder": "ALTER TABLE mail_analyses ADD COLUMN source_folder TEXT DEFAULT 'unknown'",
            "processing_status": "ALTER TABLE mail_analyses ADD COLUMN processing_status TEXT DEFAULT 'new'",
            "direction": "ALTER TABLE mail_analyses ADD COLUMN direction TEXT DEFAULT 'incoming'",
            "source_hash": "ALTER TABLE mail_analyses ADD COLUMN source_hash TEXT",
            "import_batch_id": "ALTER TABLE mail_analyses ADD COLUMN import_batch_id TEXT",
            "attachment_metadata": "ALTER TABLE mail_analyses ADD COLUMN attachment_metadata TEXT",
            "body_preview": "ALTER TABLE mail_analyses ADD COLUMN body_preview TEXT",
            "next_agent": "ALTER TABLE mail_analyses ADD COLUMN next_agent TEXT",
            "requires_human_review": "ALTER TABLE mail_analyses ADD COLUMN requires_human_review INTEGER",
            "reason_for_human_review": "ALTER TABLE mail_analyses ADD COLUMN reason_for_human_review TEXT",
            "ai_model": "ALTER TABLE mail_analyses ADD COLUMN ai_model TEXT",
            "ai_raw_response": "ALTER TABLE mail_analyses ADD COLUMN ai_raw_response TEXT",
            "ai_parse_status": "ALTER TABLE mail_analyses ADD COLUMN ai_parse_status TEXT",
            "ai_latency_ms": "ALTER TABLE mail_analyses ADD COLUMN ai_latency_ms INTEGER",
            "analysis_attempts": "ALTER TABLE mail_analyses ADD COLUMN analysis_attempts INTEGER DEFAULT 0",
            "routed_at": "ALTER TABLE mail_analyses ADD COLUMN routed_at TEXT",
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
                    created_at, source, received_at, outlook_message_id, message_id, internet_message_id, conversation_id,
                    source_folder, processing_status, direction, source_hash, import_batch_id,
                    sender, recipient, subject, body_preview, body, has_attachments,
                    attachment_names, attachment_metadata, category, confidence, priority, sender_type,
                    insurer, customer_name, relation_number, policy_number,
                    claim_number, license_plate, amount, summary, suggested_action,
                    next_agent, human_review_required, requires_human_review, reason_for_human_review,
                    ai_model, ai_raw_response, ai_parse_status, ai_latency_ms, routed_at, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    mail_data.get("source", "Handmatig"),
                    mail_data.get("received_at"),
                    mail_data.get("outlook_message_id") or mail_data.get("message_id"),
                    mail_data.get("message_id"),
                    mail_data.get("internet_message_id"),
                    mail_data.get("conversation_id"),
                    mail_data.get("source_folder", "unknown"),
                    analysis.get("processing_status", "new"),
                    mail_data.get("direction", "incoming"),
                    source_hash,
                    mail_data.get("import_batch_id"),
                    mail_data.get("sender"),
                    mail_data.get("recipient"),
                    mail_data.get("subject"),
                    mail_data.get("body_preview"),
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
                    int(bool(analysis.get("requires_human_review", analysis.get("menselijke_controle_nodig")))),
                    analysis.get("reason_for_human_review"),
                    analysis.get("ai_model"),
                    analysis.get("ai_raw_response"),
                    analysis.get("ai_parse_status"),
                    analysis.get("ai_latency_ms"),
                    analysis.get("routed_at"),
                    json.dumps(analysis, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def update_processing_status(
        self,
        analysis_id: int,
        status: str,
        details: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE mail_analyses
                SET processing_status = ?,
                    routed_at = CASE WHEN ? IN ('routed', 'needs_human', 'ignored', 'ai_timeout', 'completed', 'moved_or_archived', 'deleted_or_not_found') THEN ? ELSE routed_at END
                WHERE id = ?
                """,
                (
                    status,
                    status,
                    datetime.now().isoformat(timespec="seconds"),
                    analysis_id,
                ),
            )
        if details:
            self.log("Mail Intake Agent", "INFO", f"Status bijgewerkt naar {status}", details)

    def get_next_queued_mail(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM mail_analyses
                WHERE source_folder = 'inbox'
                  AND direction = 'incoming'
                  AND processing_status = 'queued'
                ORDER BY received_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def update_mail_analysis_result(self, analysis_id: int, analysis: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE mail_analyses
                SET processing_status = ?,
                    category = ?,
                    confidence = ?,
                    priority = ?,
                    sender_type = ?,
                    insurer = ?,
                    customer_name = ?,
                    relation_number = ?,
                    policy_number = ?,
                    claim_number = ?,
                    license_plate = ?,
                    amount = ?,
                    summary = ?,
                    suggested_action = ?,
                    next_agent = ?,
                    human_review_required = ?,
                    requires_human_review = ?,
                    reason_for_human_review = ?,
                    ai_model = ?,
                    ai_raw_response = ?,
                    ai_parse_status = ?,
                    ai_latency_ms = ?,
                    analysis_attempts = COALESCE(analysis_attempts, 0) + 1,
                    routed_at = ?,
                    raw_json = ?
                WHERE id = ?
                """,
                (
                    analysis.get("processing_status"),
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
                    int(bool(analysis.get("requires_human_review", analysis.get("menselijke_controle_nodig")))),
                    analysis.get("reason_for_human_review"),
                    analysis.get("ai_model"),
                    analysis.get("ai_raw_response"),
                    analysis.get("ai_parse_status"),
                    analysis.get("ai_latency_ms"),
                    analysis.get("routed_at") or datetime.now().isoformat(timespec="seconds"),
                    json.dumps(analysis, ensure_ascii=False),
                    analysis_id,
                ),
            )

    def active_outlook_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, message_id, outlook_message_id, internet_message_id, subject, processing_status
                FROM mail_analyses
                WHERE source = 'Outlook'
                  AND source_folder = 'inbox'
                  AND direction = 'incoming'
                  AND processing_status IN ('queued', 'analyzing', 'routed', 'needs_human', 'ai_timeout')
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def retry_analysis(self, analysis_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE mail_analyses
                SET processing_status = 'queued',
                    ai_parse_status = NULL,
                    reason_for_human_review = NULL,
                    next_agent = NULL
                WHERE id = ?
                  AND processing_status IN ('ai_timeout', 'needs_human')
                """,
                (analysis_id,),
            )

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

    def dashboard_stats(self, include_hidden: bool = False) -> dict[str, Any]:
        today = datetime.now().date().isoformat()
        with self.connect() as connection:
            status_filter = (
                ""
                if include_hidden
                else "AND processing_status IN ('queued', 'analyzing', 'routed', 'needs_human', 'ai_timeout')"
            )
            queue_filter = f"""
                source_folder = 'inbox'
                AND direction = 'incoming'
                {status_filter}
            """
            total = connection.execute(
                f"SELECT COUNT(*) FROM mail_analyses WHERE {queue_filter}"
            ).fetchone()[0]
            today_total = connection.execute(
                f"SELECT COUNT(*) FROM mail_analyses WHERE {queue_filter} AND created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]
            error_total = connection.execute(
                "SELECT COUNT(*) FROM agent_logs WHERE level = 'ERROR'"
            ).fetchone()[0]
            categories = connection.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM mail_analyses
                WHERE source_folder = 'inbox' AND direction = 'incoming'
                GROUP BY category
                ORDER BY count DESC, category ASC
                """
            ).fetchall()
            latest = connection.execute(
                f"""
                SELECT id, created_at, source, source_folder, processing_status, direction,
                       received_at, sender, subject, category, confidence, summary,
                       suggested_action, next_agent, human_review_required,
                       requires_human_review, reason_for_human_review, ai_model,
                       ai_parse_status, ai_latency_ms, import_batch_id
                FROM mail_analyses
                WHERE source_folder = 'inbox'
                  AND direction = 'incoming'
                  {status_filter}
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
                       confidence, source_folder, processing_status, direction, next_agent,
                       requires_human_review, reason_for_human_review, ai_parse_status,
                       ai_latency_ms, import_batch_id
                FROM mail_analyses
                WHERE source = 'Outlook'
                  AND source_folder = 'inbox'
                  AND direction = 'incoming'
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
            latest_learning = connection.execute(
                """
                SELECT id, created_at, source, received_at, sender, subject, category,
                       confidence, source_folder, processing_status, direction, import_batch_id
                FROM mail_analyses
                WHERE source = 'Outlook'
                  AND source_folder = 'sentitems'
                  AND direction = 'outgoing'
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
            "latest_learning": [dict(row) for row in latest_learning],
            "latest_errors": [dict(row) for row in latest_errors],
        }

    def mail_analyses(self, limit: int = 100, include_hidden: bool = False) -> list[dict[str, Any]]:
        status_filter = (
            ""
            if include_hidden
            else "AND processing_status IN ('queued', 'analyzing', 'routed', 'needs_human', 'ai_timeout')"
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, created_at, source, received_at, sender, recipient, subject,
                       source_folder, processing_status, direction, category, confidence, priority,
                       suggested_action, next_agent, human_review_required, requires_human_review,
                       reason_for_human_review, summary, ai_model, ai_parse_status, ai_latency_ms
                FROM mail_analyses
                WHERE source_folder = 'inbox'
                  AND direction = 'incoming'
                  {status_filter}
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def learning_mail_analyses(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, source, received_at, sender, recipient, subject,
                       source_folder, processing_status, direction, category, confidence,
                       priority, summary
                FROM mail_analyses
                WHERE source_folder = 'sentitems'
                  AND direction = 'outgoing'
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
