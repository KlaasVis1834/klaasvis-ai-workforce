from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urlencode

import requests
from requests import HTTPError

from services.token_store import TokenStore


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
SCOPES = ["User.Read", "Mail.Read", "offline_access"]


class MicrosoftGraphService:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_store: TokenStore,
        allowed_email: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_store = token_store
        self.allowed_email = allowed_email.strip().lower()

    @property
    def configured(self) -> bool:
        return all(
            [
                self.tenant_id,
                self.client_id,
                self.client_secret,
                self.redirect_uri,
                self.allowed_email,
                self.client_secret != "VUL_HIER_JE_NIEUWE_SECRET_IN",
            ]
        )

    def auth_url(self, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "response_mode": "query",
                "scope": " ".join(SCOPES),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{self._authority_url()}/oauth2/v2.0/authorize?{query}"

    def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        return self._post_token(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": " ".join(SCOPES),
                "code": code,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }
        )

    def profile_from_token(self, access_token: str) -> dict[str, Any]:
        response = requests.get(
            f"{GRAPH_BASE_URL}/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def account_email(self, profile: dict[str, Any]) -> str:
        mail = (profile.get("mail") or "").strip().lower()
        user_principal_name = (profile.get("userPrincipalName") or "").strip().lower()
        return mail or user_principal_name

    def profile_is_allowed(self, profile: dict[str, Any]) -> bool:
        mail = (profile.get("mail") or "").strip().lower()
        user_principal_name = (profile.get("userPrincipalName") or "").strip().lower()
        return self.allowed_email in {mail, user_principal_name}

    def save_authorized_token(self, token_data: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return self._save_token_response(token_data, profile)

    def sync_started_at(self) -> str | None:
        token = self.token_store.load() or {}
        return token.get("sync_started_at")

    def last_sync_at(self) -> str | None:
        token = self.token_store.load() or {}
        return token.get("last_sync_at")

    def inbox_sync_anchor(self) -> str:
        token = self.token_store.load() or {}
        anchor = token.get("last_sync_at") or token.get("sync_started_at")
        if anchor:
            return anchor
        anchor = self._now_utc()
        token["sync_started_at"] = anchor
        token["last_sync_at"] = anchor
        self.token_store.save(token)
        return anchor

    def update_last_sync_at(self, value: str | None = None) -> None:
        token = self.token_store.load() or {}
        token["last_sync_at"] = value or self._now_utc()
        if not token.get("sync_started_at"):
            token["sync_started_at"] = token["last_sync_at"]
        self.token_store.save(token)

    def refresh_access_token(self) -> dict[str, Any] | None:
        token = self.token_store.load()
        if not token or not token.get("refresh_token"):
            return None
        token_data = self._post_token(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": " ".join(SCOPES),
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            }
        )
        return self._save_token_response(token_data)

    def connection_status(self) -> dict[str, Any]:
        token = self.token_store.load()
        if not token:
            return {
                "connected": False,
                "configured": self.configured,
                "status": "niet verbonden",
                "user": None,
                "allowed_email": self.allowed_email,
            }
        expires_at = int(token.get("expires_at", 0) or 0)
        expired = expires_at <= int(time.time()) + 60
        user = token.get("user") or {}
        user_email = (user.get("mail") or "").strip().lower()
        account_valid = user_email == self.allowed_email
        return {
            "connected": bool((token.get("access_token") or token.get("refresh_token")) and account_valid),
            "configured": self.configured,
            "status": "fout" if not account_valid else ("verlopen" if expired else "verbonden"),
            "user": user if account_valid else None,
            "expires_at": expires_at,
            "allowed_email": self.allowed_email,
        }

    def get_access_token(self) -> str | None:
        token = self.token_store.load()
        if not token:
            return None
        if token.get("access_token") and token.get("expires_at", 0) > int(time.time()) + 60:
            return token["access_token"]
        refreshed = self.refresh_access_token()
        if not refreshed:
            return None
        return refreshed.get("access_token")

    def get_profile(self) -> dict[str, Any]:
        return self._graph_get("/me")

    def fetch_messages(self, mode: str = "poll") -> list[dict[str, Any]]:
        return self.fetch_inbox_messages(mode)

    def fetch_inbox_messages(self, mode: str = "poll", since: str | None = None) -> list[dict[str, Any]]:
        return self._fetch_folder_messages("inbox", "incoming", mode, since)

    def fetch_sent_learning_messages(self, mode: str = "learning") -> list[dict[str, Any]]:
        return self._fetch_folder_messages("sentitems", "outgoing", mode)

    def _fetch_folder_messages(
        self,
        folder: str,
        direction: str,
        mode: str = "poll",
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        top = 25 if mode == "poll" else 10
        params = {
            "$top": str(top),
            "$orderby": "receivedDateTime asc",
            "$select": ",".join(
                [
                    "id",
                    "internetMessageId",
                    "conversationId",
                    "subject",
                    "sender",
                    "toRecipients",
                    "body",
                    "bodyPreview",
                    "receivedDateTime",
                    "hasAttachments",
                    "isRead",
                    "webLink",
                ]
            ),
        }
        if since and folder == "inbox":
            params["$filter"] = f"receivedDateTime ge {since}"
        response = self._graph_get(
            f"/me/mailFolders/{folder}/messages?{urlencode(params)}",
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        messages = response.get("value", [])
        return [self.normalize_message(message, folder, direction) for message in messages[:top]]

    def normalize_message(
        self,
        message: dict[str, Any],
        source_folder: str = "unknown",
        direction: str = "incoming",
    ) -> dict[str, Any]:
        attachments = self.fetch_attachment_metadata(message["id"]) if message.get("hasAttachments") else []
        body = message.get("body", {}).get("content") or ""
        sender = message.get("sender", {}).get("emailAddress", {})
        recipients = [
            item.get("emailAddress", {}).get("address", "")
            for item in message.get("toRecipients", [])
            if item.get("emailAddress", {}).get("address")
        ]
        return {
            "source": "Outlook",
            "source_folder": source_folder,
            "direction": direction,
            "message_id": message.get("id"),
            "internet_message_id": message.get("internetMessageId"),
            "conversation_id": message.get("conversationId"),
            "sender": sender.get("address") or sender.get("name") or "",
            "recipient": ", ".join(recipients),
            "subject": message.get("subject") or "",
            "body_preview": unescape(message.get("bodyPreview") or ""),
            "body": unescape(body),
            "received_at": message.get("receivedDateTime"),
            "has_attachments": bool(message.get("hasAttachments")),
            "attachment_names": ", ".join(item.get("name", "") for item in attachments if item.get("name")),
            "attachment_metadata": attachments,
        }

    def fetch_attachment_metadata(self, message_id: str) -> list[dict[str, Any]]:
        params = {"$select": "name,contentType,size,isInline"}
        response = self._graph_get(f"/me/messages/{message_id}/attachments?{urlencode(params)}")
        return [
            {
                "name": item.get("name"),
                "content_type": item.get("contentType"),
                "size": item.get("size"),
                "is_inline": item.get("isInline"),
            }
            for item in response.get("value", [])
        ]

    def inbox_message_state(self, message_id: str) -> str:
        try:
            self._graph_get(f"/me/mailFolders/inbox/messages/{message_id}")
            return "inbox"
        except HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 404:
                raise
        try:
            self._graph_get(f"/me/messages/{message_id}")
            return "moved_or_archived"
        except HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                return "deleted_or_not_found"
            raise

    def _graph_get(self, path: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        access_token = self.get_access_token()
        if not access_token:
            raise RuntimeError("Outlook is niet verbonden. Log eerst in via Microsoft.")
        request_headers = {"Authorization": f"Bearer {access_token}"}
        if headers:
            request_headers.update(headers)
        response = requests.get(f"{GRAPH_BASE_URL}{path}", headers=request_headers, timeout=30)
        if response.status_code == 401:
            refreshed = self.refresh_access_token()
            if refreshed and refreshed.get("access_token"):
                request_headers["Authorization"] = f"Bearer {refreshed['access_token']}"
                response = requests.get(f"{GRAPH_BASE_URL}{path}", headers=request_headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        response = requests.post(
            f"{self._authority_url()}/oauth2/v2.0/token",
            data=data,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _save_token_response(
        self,
        token_data: dict[str, Any],
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.token_store.load() or {}
        merged = {**current, **token_data}
        merged["expires_at"] = int(time.time()) + int(token_data.get("expires_in", 3600))
        if profile:
            merged["user"] = {
                "display_name": profile.get("displayName"),
                "mail": profile.get("mail") or profile.get("userPrincipalName"),
            }
            sync_started_at = self._now_utc()
            merged["sync_started_at"] = sync_started_at
            merged["last_sync_at"] = sync_started_at
        self.token_store.save(merged)
        return merged

    def _authority_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    def _now_utc(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge
