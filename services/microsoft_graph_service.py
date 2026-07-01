from __future__ import annotations

import base64
import hashlib
import secrets
import time
from html import unescape
from typing import Any
from urllib.parse import urlencode

import requests

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
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_store = token_store

    @property
    def configured(self) -> bool:
        return all(
            [
                self.tenant_id,
                self.client_id,
                self.client_secret,
                self.redirect_uri,
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
        token_data = self._post_token(
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
        return self._save_token_response(token_data)

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
            return {"connected": False, "configured": self.configured, "user": None}
        return {
            "connected": bool(token.get("access_token") or token.get("refresh_token")),
            "configured": self.configured,
            "user": token.get("user"),
            "expires_at": token.get("expires_at"),
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
        top = 25 if mode == "poll" else 10
        params = {
            "$top": str(top),
            "$orderby": "receivedDateTime desc",
            "$select": ",".join(
                [
                    "id",
                    "internetMessageId",
                    "conversationId",
                    "subject",
                    "sender",
                    "toRecipients",
                    "body",
                    "receivedDateTime",
                    "hasAttachments",
                    "isRead",
                    "webLink",
                ]
            ),
        }
        response = self._graph_get(
            f"/me/mailFolders/inbox/messages?{urlencode(params)}",
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        messages = response.get("value", [])
        return [self.normalize_message(message) for message in messages[:top]]

    def normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
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
            "message_id": message.get("id"),
            "internet_message_id": message.get("internetMessageId"),
            "conversation_id": message.get("conversationId"),
            "sender": sender.get("address") or sender.get("name") or "",
            "recipient": ", ".join(recipients),
            "subject": message.get("subject") or "",
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

    def _save_token_response(self, token_data: dict[str, Any]) -> dict[str, Any]:
        current = self.token_store.load() or {}
        merged = {**current, **token_data}
        merged["expires_at"] = int(time.time()) + int(token_data.get("expires_in", 3600))
        self.token_store.save(merged)
        try:
            profile = self.get_profile()
            merged["user"] = {
                "display_name": profile.get("displayName"),
                "mail": profile.get("mail") or profile.get("userPrincipalName"),
            }
            self.token_store.save(merged)
        except requests.RequestException:
            pass
        return merged

    def _authority_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge
