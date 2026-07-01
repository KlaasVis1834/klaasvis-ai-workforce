from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class BaseAgent(ABC):
    def __init__(self, name: str, version: str, description: str, model: str) -> None:
        self.name = name
        self.version = version
        self.description = description
        self.status = "beschikbaar"
        self.model = model
        self.last_activity: str | None = None

    def touch(self) -> None:
        self.last_activity = datetime.now().isoformat(timespec="seconds")

    def metadata(self) -> dict:
        return {
            "naam": self.name,
            "versie": self.version,
            "omschrijving": self.description,
            "status": self.status,
            "model": self.model,
            "laatste_activiteit": self.last_activity,
        }

    @abstractmethod
    def run(self, input_data: dict) -> dict:
        raise NotImplementedError
