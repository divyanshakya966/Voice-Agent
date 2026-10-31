from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from pathlib import Path

from ..audit import AuditLog
from ..config import cfg_get
from . import browser, files, personal, security, system
from .base import Tool, ToolContext, ToolError

_PLUGIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _plugin_tools(config: dict, context: ToolContext) -> list[Tool]:
    loaded: list[Tool] = []
    for raw_name in cfg_get(config, "agent.plugin_modules", []) or []:
        name = str(raw_name)
        if not _PLUGIN_NAME.fullmatch(name) or name.startswith("_") or ".__" in name:
            raise RuntimeError(f"Invalid plugin module name: {name!r}")
        try:
            module = importlib.import_module(name)
        except Exception as exc:
            raise RuntimeError(f"Cannot load plugin module {name!r}: {exc}") from exc
        builder = getattr(module, "build_tools", None)
        if not callable(builder):
            raise TypeError(f"Plugin module {name!r} must expose build_tools(context).")
        tools = builder(context)
        if not isinstance(tools, list) or not all(isinstance(tool, Tool) for tool in tools):
            raise RuntimeError(f"Plugin module {name!r} returned invalid tools.")
        loaded.extend(tools)
    return loaded


def build_registry(
    config: dict,
    audit: AuditLog,
    cwd: Path,
    notifier: Callable[[str, str], None] | None = None,
) -> tuple[ToolContext, dict[str, Tool], list[dict]]:
    context = ToolContext(config=config, audit=audit, cwd=cwd, notifier=notifier)
    tools = [
        *system.build_tools(context),
        *files.build_tools(context),
        *security.build_tools(context),
        *personal.build_tools(context),
        *(browser.build_tools(context) if cfg_get(config, "agent.browser.enabled", False) else []),
        *_plugin_tools(config, context),
    ]
    registry: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in registry:
            raise RuntimeError(f"Duplicate tool name: {tool.name}")
        registry[tool.name] = tool
    schemas = [tool.schema() for tool in tools]
    return context, registry, schemas


__all__ = ["Tool", "ToolContext", "ToolError", "build_registry"]
