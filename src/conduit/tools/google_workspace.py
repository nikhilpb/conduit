"""Google Workspace tools backed by the local `gws` CLI."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from email.message import EmailMessage
import json
import os
from typing import Any

from bs4 import BeautifulSoup

from conduit.config import Settings

GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"

DRIVE_FILE_TYPE_MIME_MAP = {
    "google_doc": GOOGLE_DOC_MIME_TYPE,
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
    "folder": "application/vnd.google-apps.folder",
    "pdf": "application/pdf",
}


class GwsCliError(RuntimeError):
    """Raised when the local `gws` CLI fails."""


class GwsCliRunner:
    """Execute the Google Workspace CLI with Conduit-controlled env and parsing."""

    def __init__(self, settings: Settings):
        settings.validate_gws_configuration()
        resolved_binary = settings.resolve_gws_binary()
        if resolved_binary is None:
            raise ValueError(
                f"Google Workspace CLI binary not found: {settings.gws_binary_path!r}"
            )
        self._binary = resolved_binary
        self._settings = settings

    async def run_json(
        self,
        *command: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        argv = [self._binary, *command, "--format", "json"]
        compact_params = _compact_mapping(params)
        compact_body = _compact_mapping(body)
        if compact_params is not None:
            argv.extend(["--params", json.dumps(compact_params, separators=(",", ":"))])
        if compact_body is not None:
            argv.extend(["--json", json.dumps(compact_body, separators=(",", ":"))])

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_env(),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._settings.gws_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise GwsCliError(
                "Google Workspace CLI timed out after "
                f"{self._settings.gws_timeout_seconds:.0f} seconds."
            ) from exc

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            raise GwsCliError(_extract_cli_error(stdout_text, stderr_text))

        if not stdout_text:
            return None

        try:
            return json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise GwsCliError("Google Workspace CLI returned invalid JSON output.") from exc

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = (
            self._settings.gws_credentials_file
        )
        if self._settings.gws_account:
            env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = self._settings.gws_account
        return env


def build_google_workspace_tools(settings: Settings) -> list:
    """Create Google Workspace tool closures exposed to the agent."""

    if not settings.gws_enabled:
        return []

    runner = GwsCliRunner(settings)

    async def gmail_search_messages(
        query: str = "in:inbox",
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Search Gmail and return message summaries."""

        cleaned_query = _coerce_text(query) or "in:inbox"
        bounded_limit = max(1, min(max_results, 25))

        try:
            payload = await runner.run_json(
                "gmail",
                "users",
                "messages",
                "list",
                params={
                    "userId": "me",
                    "q": cleaned_query,
                    "maxResults": bounded_limit,
                },
            )
            messages = payload.get("messages", []) if isinstance(payload, dict) else []

            summaries: list[dict[str, Any]] = []
            for message_ref in messages[:bounded_limit]:
                message_id = _coerce_text(message_ref.get("id"))
                if not message_id:
                    continue
                message = await _gmail_get_message_payload(
                    runner,
                    message_id=message_id,
                    format_name="full",
                )
                summaries.append(_normalize_gmail_message_summary(message))

            return {
                "ok": True,
                "query": cleaned_query,
                "messages": summaries,
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), query=cleaned_query)

    async def gmail_get_message(message_id: str) -> dict[str, Any]:
        """Fetch one Gmail message with normalized headers and plain text body."""

        cleaned_message_id = _coerce_text(message_id)
        if not cleaned_message_id:
            return _error_result(message="message_id must not be empty")

        try:
            payload = await _gmail_get_message_payload(
                runner,
                message_id=cleaned_message_id,
                format_name="full",
            )
            return {
                "ok": True,
                "message": _normalize_gmail_message(payload, settings=settings),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), message_id=cleaned_message_id)

    async def gmail_create_draft(
        to: list[str],
        subject: str,
        body_text: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a Gmail draft email."""

        cleaned_to = _clean_email_list(to)
        cleaned_subject = _coerce_text(subject)
        cleaned_body = _coerce_text(body_text)
        cleaned_cc = _clean_email_list(cc)
        cleaned_bcc = _clean_email_list(bcc)

        if not cleaned_to:
            return _error_result(message="to must include at least one email address")
        if not cleaned_subject:
            return _error_result(message="subject must not be empty")
        if not cleaned_body:
            return _error_result(message="body_text must not be empty")

        message = EmailMessage()
        message["To"] = ", ".join(cleaned_to)
        message["Subject"] = cleaned_subject
        if cleaned_cc:
            message["Cc"] = ", ".join(cleaned_cc)
        if cleaned_bcc:
            message["Bcc"] = ", ".join(cleaned_bcc)
        message.set_content(cleaned_body)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")

        try:
            payload = await runner.run_json(
                "gmail",
                "users",
                "drafts",
                "create",
                params={"userId": "me"},
                body={"message": {"raw": raw}},
            )
            draft = payload if isinstance(payload, dict) else {}
            draft_id = _coerce_text(draft.get("id"))
            message_payload = draft.get("message", {}) if isinstance(draft, dict) else {}
            return {
                "ok": True,
                "draft_id": draft_id,
                "message_id": _coerce_text(message_payload.get("id")),
                "thread_id": _coerce_text(message_payload.get("threadId")),
                "to": cleaned_to,
                "cc": cleaned_cc,
                "bcc": cleaned_bcc,
                "subject": cleaned_subject,
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(
                message=str(exc),
                to=cleaned_to,
                subject=cleaned_subject,
            )

    async def calendar_list_events(
        start_time: str | None = None,
        end_time: str | None = None,
        calendar_id: str = "primary",
        max_results: int = 10,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List upcoming events for a calendar."""

        cleaned_calendar_id = _coerce_text(calendar_id) or "primary"
        cleaned_query = _coerce_text(query)
        bounded_limit = max(1, min(max_results, 25))

        try:
            time_min, time_max = _resolve_calendar_window(start_time, end_time)
            payload = await runner.run_json(
                "calendar",
                "events",
                "list",
                params={
                    "calendarId": cleaned_calendar_id,
                    "maxResults": bounded_limit,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "q": cleaned_query,
                },
            )
            items = payload.get("items", []) if isinstance(payload, dict) else []
            return {
                "ok": True,
                "calendar_id": cleaned_calendar_id,
                "start_time": time_min,
                "end_time": time_max,
                "events": [_normalize_calendar_event(item) for item in items],
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(
                message=str(exc),
                calendar_id=cleaned_calendar_id,
            )

    async def calendar_create_event(
        summary: str,
        start_time: str,
        end_time: str,
        calendar_id: str = "primary",
        location: str | None = None,
        description: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a timed calendar event without sending guest notifications."""

        cleaned_calendar_id = _coerce_text(calendar_id) or "primary"
        cleaned_summary = _coerce_text(summary)
        cleaned_location = _coerce_text(location)
        cleaned_description = _coerce_text(description)
        cleaned_attendees = _clean_email_list(attendees)

        if not cleaned_summary:
            return _error_result(message="summary must not be empty")

        try:
            body = _build_calendar_event_body(
                summary=cleaned_summary,
                start_time=start_time,
                end_time=end_time,
                location=cleaned_location,
                description=cleaned_description,
                attendees=cleaned_attendees,
            )
            payload = await runner.run_json(
                "calendar",
                "events",
                "insert",
                params={
                    "calendarId": cleaned_calendar_id,
                    "sendUpdates": "none",
                },
                body=body,
            )
            return {
                "ok": True,
                "event": _normalize_calendar_event(payload or {}),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(
                message=str(exc),
                calendar_id=cleaned_calendar_id,
                summary=cleaned_summary,
            )

    async def calendar_update_event(
        event_id: str,
        calendar_id: str = "primary",
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        location: str | None = None,
        description: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, Any]:
        """Patch a timed calendar event without sending guest notifications."""

        cleaned_event_id = _coerce_text(event_id)
        cleaned_calendar_id = _coerce_text(calendar_id) or "primary"
        cleaned_summary = _coerce_text(summary)
        cleaned_location = _coerce_text(location)
        cleaned_description = _coerce_text(description)
        cleaned_attendees = None if attendees is None else _clean_email_list(attendees)

        if not cleaned_event_id:
            return _error_result(message="event_id must not be empty")

        try:
            body: dict[str, Any] = {}
            if cleaned_summary:
                body["summary"] = cleaned_summary
            if start_time is not None or end_time is not None:
                if not _coerce_text(start_time) or not _coerce_text(end_time):
                    raise ValueError("start_time and end_time must both be provided")
                normalized_start, normalized_end = _validate_time_range(
                    start_time,
                    end_time,
                )
                body["start"] = {"dateTime": normalized_start}
                body["end"] = {"dateTime": normalized_end}
            if cleaned_location is not None:
                body["location"] = cleaned_location
            if cleaned_description is not None:
                body["description"] = cleaned_description
            if cleaned_attendees is not None:
                body["attendees"] = [{"email": email} for email in cleaned_attendees]
            if not body:
                raise ValueError("provide at least one field to update")

            payload = await runner.run_json(
                "calendar",
                "events",
                "patch",
                params={
                    "calendarId": cleaned_calendar_id,
                    "eventId": cleaned_event_id,
                    "sendUpdates": "none",
                },
                body=body,
            )
            return {
                "ok": True,
                "event": _normalize_calendar_event(payload or {}),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(
                message=str(exc),
                event_id=cleaned_event_id,
                calendar_id=cleaned_calendar_id,
            )

    async def drive_search_files(
        query: str,
        max_results: int = 10,
        file_type: str = "any",
    ) -> dict[str, Any]:
        """Search Drive for files by name or full text."""

        cleaned_query = _coerce_text(query)
        if not cleaned_query:
            return _error_result(message="query must not be empty")

        bounded_limit = max(1, min(max_results, 25))

        try:
            payload = await runner.run_json(
                "drive",
                "files",
                "list",
                params={
                    "pageSize": bounded_limit,
                    "q": _build_drive_query(cleaned_query, file_type=file_type),
                    "supportsAllDrives": False,
                    "includeItemsFromAllDrives": False,
                    "spaces": "drive",
                    "fields": (
                        "files(id,name,mimeType,webViewLink,modifiedTime,iconLink),"
                        "nextPageToken,incompleteSearch"
                    ),
                    "orderBy": "modifiedTime desc",
                },
            )
            files = payload.get("files", []) if isinstance(payload, dict) else []
            return {
                "ok": True,
                "query": cleaned_query,
                "file_type": file_type,
                "files": [_normalize_drive_file(item) for item in files],
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), query=cleaned_query)

    async def docs_get_document(document_id: str) -> dict[str, Any]:
        """Fetch one Google Doc and flatten its visible text."""

        cleaned_document_id = _coerce_text(document_id)
        if not cleaned_document_id:
            return _error_result(message="document_id must not be empty")

        try:
            payload = await runner.run_json(
                "docs",
                "documents",
                "get",
                params={"documentId": cleaned_document_id},
            )
            return {
                "ok": True,
                "document": _normalize_document(payload or {}, settings=settings),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), document_id=cleaned_document_id)

    async def docs_create_document(
        title: str,
        initial_text: str | None = None,
    ) -> dict[str, Any]:
        """Create a Google Doc and optionally seed it with initial text."""

        cleaned_title = _coerce_text(title)
        cleaned_initial_text = _coerce_text(initial_text)
        if not cleaned_title:
            return _error_result(message="title must not be empty")

        try:
            payload = await runner.run_json(
                "docs",
                "documents",
                "create",
                body={"title": cleaned_title},
            )
            created_document = payload if isinstance(payload, dict) else {}
            document_id = _coerce_text(created_document.get("documentId"))
            if not document_id:
                raise ValueError("Google Docs create response did not include documentId")

            if cleaned_initial_text:
                await _append_text_to_document(
                    runner,
                    document_id=document_id,
                    text=cleaned_initial_text,
                )

            return {
                "ok": True,
                "document_id": document_id,
                "title": cleaned_title,
                "url": _document_url(document_id),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), title=cleaned_title)

    async def docs_append_text(document_id: str, text: str) -> dict[str, Any]:
        """Append plain text to the end of a Google Doc."""

        cleaned_document_id = _coerce_text(document_id)
        cleaned_text = _coerce_text(text)
        if not cleaned_document_id:
            return _error_result(message="document_id must not be empty")
        if not cleaned_text:
            return _error_result(message="text must not be empty")

        try:
            await _append_text_to_document(
                runner,
                document_id=cleaned_document_id,
                text=cleaned_text,
            )
            return {
                "ok": True,
                "document_id": cleaned_document_id,
                "appended_chars": len(cleaned_text),
                "url": _document_url(cleaned_document_id),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), document_id=cleaned_document_id)

    async def docs_replace_text(
        document_id: str,
        search_text: str,
        replace_text: str,
        match_case: bool = False,
    ) -> dict[str, Any]:
        """Replace plain text within a Google Doc."""

        cleaned_document_id = _coerce_text(document_id)
        cleaned_search_text = _coerce_text(search_text)
        cleaned_replace_text = _coerce_text(replace_text) or ""
        if not cleaned_document_id:
            return _error_result(message="document_id must not be empty")
        if not cleaned_search_text:
            return _error_result(message="search_text must not be empty")

        try:
            payload = await runner.run_json(
                "docs",
                "documents",
                "batchUpdate",
                params={"documentId": cleaned_document_id},
                body={
                    "requests": [
                        {
                            "replaceAllText": {
                                "containsText": {
                                    "text": cleaned_search_text,
                                    "matchCase": match_case,
                                },
                                "replaceText": cleaned_replace_text,
                            }
                        }
                    ]
                },
            )
            replies = payload.get("replies", []) if isinstance(payload, dict) else []
            replacement_reply = replies[0].get("replaceAllText", {}) if replies else {}
            return {
                "ok": True,
                "document_id": cleaned_document_id,
                "occurrences_changed": replacement_reply.get("occurrencesChanged", 0),
                "url": _document_url(cleaned_document_id),
            }
        except (GwsCliError, ValueError) as exc:
            return _error_result(message=str(exc), document_id=cleaned_document_id)

    return [
        gmail_search_messages,
        gmail_get_message,
        gmail_create_draft,
        calendar_list_events,
        calendar_create_event,
        calendar_update_event,
        drive_search_files,
        docs_get_document,
        docs_create_document,
        docs_append_text,
        docs_replace_text,
    ]


async def _gmail_get_message_payload(
    runner: GwsCliRunner,
    *,
    message_id: str,
    format_name: str,
) -> dict[str, Any]:
    payload = await runner.run_json(
        "gmail",
        "users",
        "messages",
        "get",
        params={
            "userId": "me",
            "id": message_id,
            "format": format_name,
        },
    )
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected Gmail payload for message {message_id!r}")
    return payload


async def _append_text_to_document(
    runner: GwsCliRunner,
    *,
    document_id: str,
    text: str,
) -> None:
    document = await runner.run_json(
        "docs",
        "documents",
        "get",
        params={"documentId": document_id},
    )
    if not isinstance(document, dict):
        raise ValueError(f"unexpected Google Docs payload for {document_id!r}")

    end_index = _document_end_index(document)
    await runner.run_json(
        "docs",
        "documents",
        "batchUpdate",
        params={"documentId": document_id},
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": end_index},
                        "text": text,
                    }
                }
            ]
        },
    )


def _normalize_gmail_message_summary(message: dict[str, Any]) -> dict[str, Any]:
    headers = _gmail_headers(message)
    return {
        "message_id": _coerce_text(message.get("id")),
        "thread_id": _coerce_text(message.get("threadId")),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": _coerce_text(message.get("snippet")) or "",
        "label_ids": list(message.get("labelIds", []) or []),
    }


def _normalize_gmail_message(
    message: dict[str, Any],
    *,
    settings: Settings,
) -> dict[str, Any]:
    headers = _gmail_headers(message)
    full_body_text = _gmail_body_text(message)
    body_text = _truncate_text(full_body_text, settings.gws_max_content_chars)
    return {
        "message_id": _coerce_text(message.get("id")),
        "thread_id": _coerce_text(message.get("threadId")),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "bcc": headers.get("bcc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": _coerce_text(message.get("snippet")) or "",
        "label_ids": list(message.get("labelIds", []) or []),
        "body_text": body_text,
        "truncated": len(full_body_text) > settings.gws_max_content_chars,
    }


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload", {})
    headers = payload.get("headers", []) if isinstance(payload, dict) else []
    resolved: dict[str, str] = {}
    for header in headers:
        name = _coerce_text(header.get("name"))
        value = _coerce_text(header.get("value"))
        if not name or value is None:
            continue
        resolved[name.lower()] = value
    return resolved


def _gmail_body_text(message: dict[str, Any]) -> str:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""

    plain_parts: list[str] = []
    html_parts: list[str] = []
    _collect_gmail_body_parts(payload, plain_parts=plain_parts, html_parts=html_parts)
    if plain_parts:
        return "\n".join(part for part in plain_parts if part).strip()
    if html_parts:
        cleaned = []
        for part in html_parts:
            soup = BeautifulSoup(part, "html.parser")
            cleaned.append(soup.get_text("\n"))
        return "\n".join(part.strip() for part in cleaned if part.strip())
    return ""


def _collect_gmail_body_parts(
    payload: dict[str, Any],
    *,
    plain_parts: list[str],
    html_parts: list[str],
) -> None:
    mime_type = _coerce_text(payload.get("mimeType")) or ""
    filename = _coerce_text(payload.get("filename")) or ""
    if filename:
        return

    body = payload.get("body", {}) if isinstance(payload.get("body"), dict) else {}
    data = _decode_gmail_body_data(_coerce_text(body.get("data")))
    if mime_type.startswith("text/plain") and data:
        plain_parts.append(data)
    elif mime_type.startswith("text/html") and data:
        html_parts.append(data)

    for part in payload.get("parts", []) or []:
        if isinstance(part, dict):
            _collect_gmail_body_parts(
                part,
                plain_parts=plain_parts,
                html_parts=html_parts,
            )


def _decode_gmail_body_data(value: str | None) -> str:
    if not value:
        return ""

    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
    return raw.decode("utf-8", errors="replace")


def _normalize_calendar_event(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("start", {}) if isinstance(event.get("start"), dict) else {}
    end = event.get("end", {}) if isinstance(event.get("end"), dict) else {}
    attendees = event.get("attendees", []) if isinstance(event.get("attendees"), list) else []
    organizer = event.get("organizer", {}) if isinstance(event.get("organizer"), dict) else {}
    return {
        "event_id": _coerce_text(event.get("id")),
        "summary": _coerce_text(event.get("summary")) or "",
        "description": _coerce_text(event.get("description")) or "",
        "location": _coerce_text(event.get("location")) or "",
        "status": _coerce_text(event.get("status")) or "",
        "calendar_html_link": _coerce_text(event.get("htmlLink")) or "",
        "start_time": _coerce_text(start.get("dateTime") or start.get("date")) or "",
        "end_time": _coerce_text(end.get("dateTime") or end.get("date")) or "",
        "all_day": bool(start.get("date") and not start.get("dateTime")),
        "attendees": [
            _coerce_text(attendee.get("email")) or ""
            for attendee in attendees
            if _coerce_text(attendee.get("email"))
        ],
        "organizer_email": _coerce_text(organizer.get("email")) or "",
    }


def _build_calendar_event_body(
    *,
    summary: str,
    start_time: str,
    end_time: str,
    location: str | None,
    description: str | None,
    attendees: list[str],
) -> dict[str, Any]:
    normalized_start, normalized_end = _validate_time_range(start_time, end_time)
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": normalized_start},
        "end": {"dateTime": normalized_end},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]
    return body


def _resolve_calendar_window(
    start_time: str | None,
    end_time: str | None,
) -> tuple[str, str]:
    start_dt = _parse_rfc3339(start_time) if _coerce_text(start_time) else datetime.now(UTC)
    if _coerce_text(end_time):
        end_dt = _parse_rfc3339(end_time)
    else:
        end_dt = start_dt + timedelta(days=7)
    if end_dt <= start_dt:
        raise ValueError("end_time must be after start_time")
    return _format_rfc3339(start_dt), _format_rfc3339(end_dt)


def _validate_time_range(start_time: str, end_time: str) -> tuple[str, str]:
    start_dt = _parse_rfc3339(start_time)
    end_dt = _parse_rfc3339(end_time)
    if end_dt <= start_dt:
        raise ValueError("end_time must be after start_time")
    return _format_rfc3339(start_dt), _format_rfc3339(end_dt)


def _parse_rfc3339(value: str) -> datetime:
    cleaned_value = _coerce_text(value)
    if not cleaned_value:
        raise ValueError("time value must not be empty")
    normalized = cleaned_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"invalid RFC3339 timestamp: {cleaned_value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"timestamp must include a timezone offset: {cleaned_value!r}"
        )
    return parsed


def _format_rfc3339(value: datetime) -> str:
    return value.isoformat()


def _build_drive_query(query: str, *, file_type: str) -> str:
    cleaned_file_type = _coerce_text(file_type) or "any"
    mime_type = DRIVE_FILE_TYPE_MIME_MAP.get(cleaned_file_type)
    if cleaned_file_type != "any" and mime_type is None:
        raise ValueError(
            "file_type must be one of any, google_doc, spreadsheet, presentation, "
            "folder, pdf"
        )

    escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")
    parts = [
        "trashed = false",
        f"(name contains '{escaped_query}' or fullText contains '{escaped_query}')",
    ]
    if mime_type:
        parts.append(f"mimeType = '{mime_type}'")
    return " and ".join(parts)


def _normalize_drive_file(file_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": _coerce_text(file_payload.get("id")),
        "name": _coerce_text(file_payload.get("name")) or "",
        "mime_type": _coerce_text(file_payload.get("mimeType")) or "",
        "web_view_link": _coerce_text(file_payload.get("webViewLink")) or "",
        "modified_time": _coerce_text(file_payload.get("modifiedTime")) or "",
        "icon_link": _coerce_text(file_payload.get("iconLink")) or "",
    }


def _normalize_document(
    payload: dict[str, Any],
    *,
    settings: Settings,
) -> dict[str, Any]:
    document_id = _coerce_text(payload.get("documentId")) or ""
    content = _flatten_document_content(payload.get("body", {}))
    truncated_content = _truncate_text(content, settings.gws_max_content_chars)
    return {
        "document_id": document_id,
        "title": _coerce_text(payload.get("title")) or "",
        "content": truncated_content,
        "truncated": len(content) > settings.gws_max_content_chars,
        "url": _document_url(document_id),
    }


def _flatten_document_content(body: Any) -> str:
    if not isinstance(body, dict):
        return ""

    chunks: list[str] = []
    _collect_document_content(body.get("content", []), chunks)
    text = "".join(chunks)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def _collect_document_content(elements: Any, chunks: list[str]) -> None:
    if not isinstance(elements, list):
        return

    for element in elements:
        if not isinstance(element, dict):
            continue

        paragraph = element.get("paragraph")
        if isinstance(paragraph, dict):
            for paragraph_element in paragraph.get("elements", []) or []:
                if not isinstance(paragraph_element, dict):
                    continue
                text_run = paragraph_element.get("textRun")
                if isinstance(text_run, dict):
                    content = _coerce_text(text_run.get("content"))
                    if content:
                        chunks.append(content)

        table = element.get("table")
        if isinstance(table, dict):
            for row in table.get("tableRows", []) or []:
                if not isinstance(row, dict):
                    continue
                for cell in row.get("tableCells", []) or []:
                    if isinstance(cell, dict):
                        _collect_document_content(cell.get("content", []), chunks)

        table_of_contents = element.get("tableOfContents")
        if isinstance(table_of_contents, dict):
            _collect_document_content(table_of_contents.get("content", []), chunks)


def _document_end_index(document: dict[str, Any]) -> int:
    body = document.get("body", {}) if isinstance(document.get("body"), dict) else {}
    content = body.get("content", []) if isinstance(body.get("content"), list) else []
    if not content:
        return 1

    last_end_index = 1
    for element in content:
        if not isinstance(element, dict):
            continue
        end_index = element.get("endIndex")
        if isinstance(end_index, int):
            last_end_index = max(last_end_index, end_index)

    return max(1, last_end_index - 1)


def _document_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


def _clean_email_list(values: Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        candidate = _coerce_text(values)
        return [candidate] if candidate else []

    cleaned = []
    for value in values:
        candidate = _coerce_text(value)
        if candidate:
            cleaned.append(candidate)
    return cleaned


def _compact_mapping(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {key: item for key, item in value.items() if item is not None}


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]


def _error_result(*, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def _extract_cli_error(stdout_text: str, stderr_text: str) -> str:
    for candidate in (stdout_text, stderr_text):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            error = payload.get("error", {})
            if isinstance(error, dict):
                message = _coerce_text(error.get("message"))
                if message:
                    return message
            message = _coerce_text(payload.get("message"))
            if message:
                return message

    return stderr_text or stdout_text or "Google Workspace CLI command failed."
