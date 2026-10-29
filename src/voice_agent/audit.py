from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .config import STATE_DIR
from .util import now_iso, redact


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (STATE_DIR / "audit.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        record = {"ts": now_iso(), "event": event}
        for key, value in fields.items():
            if isinstance(value, str):
                record[key] = redact(value)
            else:
                record[key] = value
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)

    def tail(self, count: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        records = []
        for line in lines[-count:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
