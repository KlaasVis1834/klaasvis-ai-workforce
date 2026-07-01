from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TokenStore:
    def __init__(self, path: str = "database/microsoft_token.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, token_data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
