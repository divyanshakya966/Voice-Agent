from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..audit import AuditLog


class ToolError(RuntimeError):
    pass


@dataclass
class ToolContext:
    config: dict
    audit: AuditLog
    cwd: Path
    notifier: Callable[[str, str], None] | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], str]
    dangerous: bool = False
    summary: Callable[[dict], str] | None = None
    metadata: dict = field(default_factory=dict)

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def describe(self, args: dict[str, Any]) -> str:
        if self.summary:
            try:
                return self.summary(args)
            except Exception:
                return self.name
        return self.name
