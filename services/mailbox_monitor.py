from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable


class MailboxMonitor:
    def __init__(
        self,
        scan_callback: Callable[[], dict[str, int]],
        connected_callback: Callable[[], bool],
        log_callback: Callable[[str, str, str, str | None], None],
        interval_seconds: int = 15,
    ) -> None:
        self.scan_callback = scan_callback
        self.connected_callback = connected_callback
        self.log_callback = log_callback
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.status = {
            "running": False,
            "last_sync": None,
            "last_scan": None,
            "new_mails": 0,
            "last_error": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.status["running"] = True
        self._thread = threading.Thread(target=self._run, name="mailbox-monitor", daemon=True)
        self._thread.start()
        self.log_callback("Mail Agent", "INFO", "Background mailbox monitor gestart", None)

    def snapshot(self) -> dict:
        return dict(self.status)

    def run_once(self) -> dict[str, int]:
        with self._lock:
            self.status["last_scan"] = self._now()
            if not self.connected_callback():
                self.status["new_mails"] = 0
                return {"imported": 0, "duplicates": 0, "failed": 0}
            result = self.scan_callback()
            self.status["new_mails"] = result.get("imported", 0)
            self.status["last_sync"] = self._now()
            self.status["last_error"] = None
            return result

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self.status["last_error"] = str(exc)
                self.log_callback("Mail Agent", "ERROR", "Mailbox scan mislukt", str(exc))
            self._stop_event.wait(self.interval_seconds)

    def _now(self) -> str:
        return datetime.now().strftime("%H:%M:%S")
