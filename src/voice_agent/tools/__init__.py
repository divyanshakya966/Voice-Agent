from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..audit import AuditLog
from . import files, security, system
from .base import Tool, ToolContext, ToolError


def build_registry(
    config: dict,
    audit: AuditLog,
    cwd: Path,
    notifier: Callable[[str, str], None] | None = None,
) -> tuple[ToolContext, dict[str, Tool], list[dict]]:
    context = ToolContext(config=config, audit=audit, cwd=cwd, notifier=notifier)
    tools = [*system.build_tools(context), *files.build_tools(context), *security.build_tools(context)]
    registry: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in registry:
            raise RuntimeError(f"Duplicate tool name: {tool.name}")
        registry[tool.name] = tool
    schemas = [tool.schema() for tool in tools]
    return context, registry, schemas


__all__ = ["Tool", "ToolContext", "ToolError", "build_registry"]
