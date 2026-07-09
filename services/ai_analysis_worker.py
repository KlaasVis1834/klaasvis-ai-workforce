from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable


class AIAnalysisWorker:
    def __init__(
        self,
        process_callback: Callable[[], dict[str, int]],
        log_callback: Callable[[str, str, str, str | None], None],
        interval_seconds: int = 10,
        agent_name: str = "Mail Intake Agent",
    ) -> None:
        self.process_callback = process_callback
        self.log_callback = log_callback
        self.interval_seconds = interval_seconds
        self.agent_name = agent_name
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.status = {
            "running": False,
            "last_run": None,
            "last_result": None,
            "last_error": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.status["running"] = True
        thread_name = f"{self.agent_name.lower().replace(' ', '-')}-worker"
        self._thread = threading.Thread(target=self._run, name=thread_name, daemon=True)
        self._thread.start()
        self.log_callback(self.agent_name, "INFO", "AI analyse worker gestart", None)

    def snapshot(self) -> dict:
        return dict(self.status)

    def run_once(self) -> dict[str, int]:
        with self._lock:
            self.status["last_run"] = self._now()
            result = self.process_callback()
            self.status["last_result"] = result
            self.status["last_error"] = None
            return result

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self.status["last_error"] = str(exc)
                self.log_callback(self.agent_name, "ERROR", "AI analyse worker mislukt", str(exc))
            self._stop_event.wait(self.interval_seconds)

    def _now(self) -> str:
        return datetime.now().strftime("%H:%M:%S")
