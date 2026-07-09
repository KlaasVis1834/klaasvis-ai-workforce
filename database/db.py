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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    mail_analysis_id INTEGER,
                    outlook_message_id TEXT,
                    attachment_id TEXT,
                    document_name TEXT,
                    file_path TEXT,
                    content_type TEXT,
                    file_size INTEGER,
                    document_kind TEXT,
                    extracted_text TEXT,
                    document_type TEXT,
                    category TEXT,
                    summary TEXT,
                    confidence REAL,
                    relation_proposal TEXT,
                    policy_proposal TEXT,
                    claim_proposal TEXT,
                    customer_name TEXT,
                    policy_number TEXT,
                    claim_number TEXT,
                    license_plate TEXT,
                    insurer TEXT,
                    document_date TEXT,
                    amount TEXT,
                    requires_human_review INTEGER,
                    review_reason TEXT,
                    status TEXT DEFAULT 'queued',
                    ai_model TEXT,
                    ai_raw_response TEXT,
                    ai_parse_status TEXT,
                    ai_latency_ms INTEGER,
                    raw_json TEXT
                )
                """
            )
            self._migrate_document_analyses(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    document_id INTEGER,
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

    def _migrate_document_analyses(self, connection: sqlite3.Connection) -> None:
        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(document_analyses)").fetchall()
        }
        migrations = {
            "updated_at": "ALTER TABLE document_analyses ADD COLUMN updated_at TEXT",
            "mail_analysis_id": "ALTER TABLE document_analyses ADD COLUMN mail_analysis_id INTEGER",
            "outlook_message_id": "ALTER TABLE document_analyses ADD COLUMN outlook_message_id TEXT",
            "attachment_id": "ALTER TABLE document_analyses ADD COLUMN attachment_id TEXT",
            "document_name": "ALTER TABLE document_analyses ADD COLUMN document_name TEXT",
            "file_path": "ALTER TABLE document_analyses ADD COLUMN file_path TEXT",
            "content_type": "ALTER TABLE document_analyses ADD COLUMN content_type TEXT",
            "file_size": "ALTER TABLE document_analyses ADD COLUMN file_size INTEGER",
            "document_kind": "ALTER TABLE document_analyses ADD COLUMN document_kind TEXT",
            "extracted_text": "ALTER TABLE document_analyses ADD COLUMN extracted_text TEXT",
            "document_type": "ALTER TABLE document_analyses ADD COLUMN document_type TEXT",
            "category": "ALTER TABLE document_analyses ADD COLUMN category TEXT",
            "summary": "ALTER TABLE document_analyses ADD COLUMN summary TEXT",
            "confidence": "ALTER TABLE document_analyses ADD COLUMN confidence REAL",
            "relation_proposal": "ALTER TABLE document_analyses ADD COLUMN relation_proposal TEXT",
            "policy_proposal": "ALTER TABLE document_analyses ADD COLUMN policy_proposal TEXT",
            "claim_proposal": "ALTER TABLE document_analyses ADD COLUMN claim_proposal TEXT",
            "customer_name": "ALTER TABLE document_analyses ADD COLUMN customer_name TEXT",
            "policy_number": "ALTER TABLE document_analyses ADD COLUMN policy_number TEXT",
            "claim_number": "ALTER TABLE document_analyses ADD COLUMN claim_number TEXT",
            "license_plate": "ALTER TABLE document_analyses ADD COLUMN license_plate TEXT",
            "insurer": "ALTER TABLE document_analyses ADD COLUMN insurer TEXT",
            "document_date": "ALTER TABLE document_analyses ADD COLUMN document_date TEXT",
            "amount": "ALTER TABLE document_analyses ADD COLUMN amount TEXT",
            "requires_human_review": "ALTER TABLE document_analyses ADD COLUMN requires_human_review INTEGER",
            "review_reason": "ALTER TABLE document_analyses ADD COLUMN review_reason TEXT",
            "status": "ALTER TABLE document_analyses ADD COLUMN status TEXT DEFAULT 'queued'",
            "ai_model": "ALTER TABLE document_analyses ADD COLUMN ai_model TEXT",
            "ai_raw_response": "ALTER TABLE document_analyses ADD COLUMN ai_raw_response TEXT",
            "ai_parse_status": "ALTER TABLE document_analyses ADD COLUMN ai_parse_status TEXT",
            "ai_latency_ms": "ALTER TABLE document_analyses ADD COLUMN ai_latency_ms INTEGER",
            "raw_json": "ALTER TABLE document_analyses ADD COLUMN raw_json TEXT",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_document_attachment
            ON document_analyses(mail_analysis_id, attachment_id, document_name)
            WHERE document_name IS NOT NULL AND document_name != ''
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

    def save_document_queue_item(self, document_data: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO document_analyses (
                    created_at, updated_at, mail_analysis_id, outlook_message_id, attachment_id,
                    document_name, file_path, content_type, file_size, document_kind,
                    status, ai_parse_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'queued')
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    datetime.now().isoformat(timespec="seconds"),
                    document_data.get("mail_analysis_id"),
                    document_data.get("outlook_message_id"),
                    document_data.get("attachment_id"),
                    document_data.get("document_name"),
                    document_data.get("file_path"),
                    document_data.get("content_type"),
                    document_data.get("file_size"),
                    document_data.get("document_kind"),
                ),
            )
            if cursor.rowcount:
                return int(cursor.lastrowid)
            row = connection.execute(
                """
                SELECT id
                FROM document_analyses
                WHERE mail_analysis_id = ?
                  AND COALESCE(attachment_id, '') = COALESCE(?, '')
                  AND document_name = ?
                LIMIT 1
                """,
                (
                    document_data.get("mail_analysis_id"),
                    document_data.get("attachment_id"),
                    document_data.get("document_name"),
                ),
            ).fetchone()
            return int(row["id"]) if row else 0

    def get_next_queued_document(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, m.subject AS mail_subject, m.sender AS mail_sender, m.received_at AS mail_received_at
                FROM document_analyses d
                LEFT JOIN mail_analyses m ON m.id = d.mail_analysis_id
                WHERE status = 'queued'
                ORDER BY d.created_at ASC, d.id ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def update_document_status(self, document_id: int, status: str, details: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE document_analyses
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, datetime.now().isoformat(timespec="seconds"), document_id),
            )
        self.log_document(document_id, "INFO", f"Documentstatus bijgewerkt naar {status}", details)

    def update_document_analysis_result(self, document_id: int, result: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE document_analyses
                SET updated_at = ?,
                    status = ?,
                    extracted_text = ?,
                    document_type = ?,
                    category = ?,
                    summary = ?,
                    confidence = ?,
                    relation_proposal = ?,
                    policy_proposal = ?,
                    claim_proposal = ?,
                    customer_name = ?,
                    policy_number = ?,
                    claim_number = ?,
                    license_plate = ?,
                    insurer = ?,
                    document_date = ?,
                    amount = ?,
                    requires_human_review = ?,
                    review_reason = ?,
                    ai_model = ?,
                    ai_raw_response = ?,
                    ai_parse_status = ?,
                    ai_latency_ms = ?,
                    raw_json = ?
                WHERE id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    result.get("status", "needs_human"),
                    result.get("extracted_text"),
                    result.get("documenttype"),
                    result.get("categorie"),
                    result.get("samenvatting"),
                    result.get("vertrouwen_score"),
                    result.get("relatievoorstel"),
                    result.get("polisvoorstel"),
                    result.get("schadevoorstel"),
                    result.get("klantnaam"),
                    result.get("polisnummer"),
                    result.get("schadenummer"),
                    result.get("kenteken"),
                    result.get("maatschappij"),
                    result.get("datum"),
                    result.get("bedrag"),
                    int(bool(result.get("menselijke_controle_nodig"))),
                    result.get("reden_controle"),
                    result.get("ai_model"),
                    result.get("ai_raw_response"),
                    result.get("ai_parse_status"),
                    result.get("ai_latency_ms"),
                    json.dumps(result, ensure_ascii=False),
                    document_id,
                ),
            )

    def document_analyses(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, m.subject AS mail_subject, m.sender AS mail_sender, m.received_at AS mail_received_at
                FROM document_analyses d
                LEFT JOIN mail_analyses m ON m.id = d.mail_analysis_id
                ORDER BY d.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def document_stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM document_analyses").fetchone()[0]
            queued = connection.execute(
                "SELECT COUNT(*) FROM document_analyses WHERE status = 'queued'"
            ).fetchone()[0]
            needs_human = connection.execute(
                "SELECT COUNT(*) FROM document_analyses WHERE requires_human_review = 1"
            ).fetchone()[0]
        return {"total": total, "queued": queued, "needs_human": needs_human}

    def log_document(
        self,
        document_id: int | None,
        level: str,
        message: str,
        details: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO document_logs (created_at, document_id, level, message, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    document_id,
                    level,
                    message,
                    details,
                ),
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
