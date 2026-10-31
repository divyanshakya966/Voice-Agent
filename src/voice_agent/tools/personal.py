from __future__ import annotations

import json
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr

from ..config import STATE_DIR
from ..util import atomic_write, trim
from .base import Tool, ToolContext, ToolError

_REMINDERS = STATE_DIR / "reminders.json"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load_reminders() -> list[dict[str, str]]:
    if not _REMINDERS.exists():
        return []
    try:
        data = json.loads(_REMINDERS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"Cannot read reminders: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ToolError("Reminder storage is invalid.")
    return [
        {str(key): str(value) for key, value in item.items() if isinstance(key, str)}
        for item in data
    ]


def _save_reminders(reminders: list[dict[str, str]]) -> None:
    atomic_write(_REMINDERS, json.dumps(reminders, indent=2) + "\n", mode=0o600)


def due_reminders(now: datetime | None = None) -> list[str]:
    current = now or datetime.now().astimezone()
    reminders = _load_reminders()
    messages: list[str] = []
    changed = False
    for item in reminders:
        if item.get("notified") == "true":
            continue
        try:
            due = datetime.fromisoformat(item.get("due", ""))
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.astimezone()
        if due <= current:
            item["notified"] = "true"
            messages.append(item.get("text", "Reminder due."))
            changed = True
    if changed:
        _save_reminders(reminders)
    return messages


def _reminder(args: dict, ctx: ToolContext) -> str:
    action = str(args.get("action", "list")).lower()
    reminders = _load_reminders()
    if action == "list":
        if not reminders:
            return "No local reminders are scheduled."
        return "\n".join(
            f"{item.get('id', '')}: {item.get('due', '')} - {item.get('text', '')}"
            for item in reminders
        )
    if action == "cancel":
        reminder_id = str(args.get("id", ""))
        remaining = [item for item in reminders if item.get("id") != reminder_id]
        if len(remaining) == len(reminders):
            raise ToolError("Reminder ID was not found.")
        _save_reminders(remaining)
        return f"Cancelled reminder {reminder_id}."
    if action != "create":
        raise ToolError("Reminder action must be create, list, or cancel.")
    text = str(args.get("text", "")).strip()
    due = str(args.get("due", "")).strip()
    if not text or len(text) > 500:
        raise ToolError("Reminder text must contain 1 to 500 characters.")
    try:
        due_value = datetime.fromisoformat(due)
    except ValueError as exc:
        raise ToolError("Reminder due time must be an ISO-8601 datetime.") from exc
    reminder_id = datetime.now().astimezone().strftime("%Y%m%d%H%M%S%f")
    reminders.append({"id": reminder_id, "due": due_value.isoformat(), "text": text})
    _save_reminders(reminders)
    return f"Created reminder {reminder_id} for {due_value.isoformat()}."


def _email_draft(args: dict, ctx: ToolContext) -> str:
    recipient = str(args.get("to", "")).strip()
    address = parseaddr(recipient)[1]
    if not _EMAIL_RE.fullmatch(address):
        raise ToolError("Email recipient is invalid.")
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not subject or len(subject) > 200 or len(body) > 100_000:
        raise ToolError("Email subject or body is invalid or too large.")
    message = EmailMessage()
    message["To"] = address
    message["Subject"] = subject
    message.set_content(body)
    directory = STATE_DIR / "email-drafts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.now().astimezone().strftime("%Y%m%d%H%M%S%f") + ".eml")
    atomic_write(path, message.as_string(), mode=0o600)
    return f"Saved email draft to {path}. It was not sent."


def build_tools(ctx: ToolContext) -> list[Tool]:
    return [
        Tool(
            "reminder",
            "Create, list, or cancel local reminders. Due reminders notify during an active wake session.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "cancel"]},
                    "text": {"type": "string", "maxLength": 500},
                    "due": {"type": "string", "maxLength": 80},
                    "id": {"type": "string", "maxLength": 40},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            _reminder,
            dangerous=True,
            summary=lambda args: f"{args.get('action', 'list')} local reminder",
        ),
        Tool(
            "email_draft",
            "Create a local .eml email draft. Never sends email.",
            {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "maxLength": 320},
                    "subject": {"type": "string", "maxLength": 200},
                    "body": {"type": "string", "maxLength": 100000},
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
            _email_draft,
            dangerous=True,
            summary=lambda args: f"draft email to {trim(str(args.get('to', '')), 120)}",
        ),
    ]
