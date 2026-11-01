from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, parseaddr

from ..config import STATE_DIR
from ..util import atomic_write, trim
from .base import Tool, ToolContext, ToolError

_REMINDERS = STATE_DIR / "reminders.json"
_EMAIL_DIR = STATE_DIR / "email-drafts"
_EMAIL_ID = re.compile(r"^\d{20}\.eml$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@contextmanager
def _reminder_lock():
    _REMINDERS.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _REMINDERS.with_suffix(".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _notified(item: dict[str, str]) -> bool:
    return str(item.get("notified", "")).lower() == "true"


def due_reminders(now: datetime | None = None) -> list[str]:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    messages: list[str] = []
    with _reminder_lock():
        reminders = _load_reminders()
        changed = False
        for item in reminders:
            if _notified(item):
                continue
            try:
                due = _parse_datetime(item.get("due", ""))
            except ValueError:
                continue
            if due <= current:
                item["notified"] = "true"
                messages.append(item.get("text", "Reminder due."))
                changed = True
        if changed:
            _save_reminders(reminders)
    return messages


def _reminder_lines(reminders: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{item.get('id', '')}: {item.get('due', '')} - {item.get('text', '')}"
        + (" [notified]" if _notified(item) else "")
        for item in reminders
    )


def list_reminders() -> str:
    with _reminder_lock():
        reminders = _load_reminders()
    if not reminders:
        return "No local reminders are scheduled."
    return _reminder_lines(reminders)


def create_reminder(text: str, due: str) -> str:
    text = text.strip()
    due = due.strip()
    if not text or len(text) > 500:
        raise ToolError("Reminder text must contain 1 to 500 characters.")
    try:
        due_value = _parse_datetime(due)
    except ValueError as exc:
        raise ToolError("Reminder due time must be an ISO-8601 datetime.") from exc
    reminder_id = datetime.now().astimezone().strftime("%Y%m%d%H%M%S%f")
    with _reminder_lock():
        reminders = _load_reminders()
        reminders.append({"id": reminder_id, "due": due_value.isoformat(), "text": text})
        _save_reminders(reminders)
    return f"Created reminder {reminder_id} for {due_value.isoformat()}."


def cancel_reminder(reminder_id: str) -> str:
    reminder_id = reminder_id.strip()
    with _reminder_lock():
        reminders = _load_reminders()
        remaining = [item for item in reminders if item.get("id") != reminder_id]
        if len(remaining) == len(reminders):
            raise ToolError("Reminder ID was not found.")
        _save_reminders(remaining)
    return f"Cancelled reminder {reminder_id}."


def _reminder(args: dict, ctx: ToolContext) -> str:
    action = str(args.get("action", "list")).lower()
    if action == "list":
        return list_reminders()
    if action == "cancel":
        return cancel_reminder(str(args.get("id", "")))
    if action == "create":
        return create_reminder(str(args.get("text", "")), str(args.get("due", "")))
    raise ToolError("Reminder action must be create, list, or cancel.")


def _addresses(value: str, field: str) -> list[str]:
    addresses: list[str] = []
    for part in re.split(r"[,;]", value):
        part = part.strip()
        if not part:
            continue
        address = parseaddr(part)[1]
        if not _EMAIL_RE.fullmatch(address):
            raise ToolError(f"Email {field} address is invalid: {trim(part, 80)}")
        addresses.append(address)
    if len(addresses) > 10:
        raise ToolError(f"Email {field} is limited to 10 addresses.")
    return addresses


def _draft_path(draft_id: str) -> os.PathLike[str]:
    value = draft_id.strip()
    if not _EMAIL_ID.fullmatch(value):
        raise ToolError("Email draft ID is invalid.")
    return _EMAIL_DIR / value


def _draft_summary(path: os.PathLike[str]) -> str:
    try:
        with open(path, "rb") as handle:
            message = BytesParser(policy=policy.default).parse(handle)
    except OSError as exc:
        raise ToolError(f"Cannot read email draft: {exc}") from exc
    return (
        f"{os.path.basename(path)}: {message.get('subject', '(no subject)')} "
        f"to {message.get('to', '(no recipient)')}"
    )


def _create_draft(args: dict) -> str:
    to = _addresses(str(args.get("to", "")).strip(), "recipient")
    cc = _addresses(str(args.get("cc", "")).strip(), "cc")
    bcc = _addresses(str(args.get("bcc", "")).strip(), "bcc")
    if not to:
        raise ToolError("At least one email recipient is required.")
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not subject or len(subject) > 200 or len(body) > 100_000:
        raise ToolError("Email subject or body is invalid or too large.")
    message = EmailMessage()
    message["From"] = "Jarvis <voice-agent@localhost>"
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now().astimezone())
    message.set_content(body)
    _EMAIL_DIR.mkdir(parents=True, exist_ok=True)
    path = _EMAIL_DIR / (datetime.now().astimezone().strftime("%Y%m%d%H%M%S%f") + ".eml")
    atomic_write(path, message.as_string(), mode=0o600)
    return f"Saved email draft to {path}. It was not sent."


def _email_draft(args: dict, ctx: ToolContext) -> str:
    action = str(args.get("action", "create")).lower()
    if action == "create":
        return _create_draft(args)
    if action == "list":
        if not _EMAIL_DIR.is_dir():
            return "No local email drafts are saved."
        drafts = sorted(_EMAIL_DIR.glob("*.eml"))
        if not drafts:
            return "No local email drafts are saved."
        lines = [_draft_summary(path) for path in drafts[:100]]
        if len(drafts) > 100:
            lines.append(f"... {len(drafts) - 100} more drafts")
        return "\n".join(lines)
    if action not in {"show", "delete"}:
        raise ToolError("Email draft action must be create, list, show, or delete.")
    path = _draft_path(str(args.get("id", "")))
    if action == "show":
        if not path.is_file():
            raise ToolError("Email draft was not found.")
        try:
            with open(path, "rb") as handle:
                message = BytesParser(policy=policy.default).parse(handle)
        except OSError as exc:
            raise ToolError(f"Cannot read email draft: {exc}") from exc
        body = message.get_body(preferencelist=("plain",))
        text = body.get_content() if body else ""
        headers = "\n".join(
            f"{name}: {message.get(name, '')}"
            for name in ("Date", "From", "To", "Cc", "Bcc", "Subject")
            if message.get(name)
        )
        return f"{headers}\n\n{trim(str(text), 4000)}"
    if action == "delete":
        if not path.is_file():
            raise ToolError("Email draft was not found.")
        try:
            path.unlink()
        except OSError as exc:
            raise ToolError(f"Cannot delete email draft: {exc}") from exc
        return f"Deleted email draft {os.path.basename(path)}."
    raise ToolError("Email draft action must be create, list, show, or delete.")


def build_tools(ctx: ToolContext) -> list[Tool]:
    return [
        Tool(
            "reminder",
            "Create, list, or cancel local reminders. The reminders command can notify in the background.",
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
            "Create, list, show, or delete local .eml drafts. Never sends email.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "show", "delete"]},
                    "id": {"type": "string", "maxLength": 40},
                    "to": {"type": "string", "maxLength": 320},
                    "cc": {"type": "string", "maxLength": 320},
                    "bcc": {"type": "string", "maxLength": 320},
                    "subject": {"type": "string", "maxLength": 200},
                    "body": {"type": "string", "maxLength": 100000},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            _email_draft,
            dangerous=True,
            summary=lambda args: f"{args.get('action', 'create')} local email draft",
        ),
    ]
