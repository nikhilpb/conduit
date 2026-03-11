import asyncio
import base64

from conduit.config import Settings
from conduit.tools import google_workspace as google_workspace_module


def test_gws_cli_runner_passes_credentials_env_and_json_flags(tmp_path):
    credentials_path = tmp_path / "gws-credentials.json"
    credentials_path.write_text("{}")
    binary_path = tmp_path / "fake-gws"
    binary_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "print(json.dumps({\n"
        "    'credentials': os.environ.get('GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE', ''),\n"
        "    'account': os.environ.get('GOOGLE_WORKSPACE_CLI_ACCOUNT', ''),\n"
        "    'args': sys.argv[1:],\n"
        "}))\n"
    )
    binary_path.chmod(0o755)

    runner = google_workspace_module.GwsCliRunner(
        Settings(
            _env_file=None,
            gws_enabled=True,
            gws_binary_path=str(binary_path),
            gws_credentials_file=str(credentials_path),
            gws_account="me@example.com",
        )
    )

    result = asyncio.run(
        runner.run_json(
            "gmail",
            "users",
            "messages",
            "list",
            params={"userId": "me"},
            body={"label": "draft"},
        )
    )

    assert result["credentials"] == str(credentials_path)
    assert result["account"] == "me@example.com"
    assert result["args"] == [
        "gmail",
        "users",
        "messages",
        "list",
        "--format",
        "json",
        "--params",
        '{"userId":"me"}',
        "--json",
        '{"label":"draft"}',
    ]


def test_gmail_get_message_normalizes_headers_and_body(monkeypatch):
    payload = {
        "id": "msg-1",
        "threadId": "thread-1",
        "snippet": "Hello world",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Trip update"},
                {"name": "Date", "value": "Tue, 11 Mar 2026 10:00:00 +0000"},
            ],
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": _gmail_b64("Line one\nLine two"),
                    },
                }
            ],
        },
    }
    class FakeRunner:
        def __init__(self, settings):
            del settings

        async def run_json(self, *command, params=None, body=None):
            assert command == ("gmail", "users", "messages", "get")
            assert params == {
                "userId": "me",
                "id": "msg-1",
                "format": "full",
            }
            return payload

    monkeypatch.setattr(google_workspace_module, "GwsCliRunner", FakeRunner)
    settings = Settings(_env_file=None, gws_enabled=True)
    tools = _tools_by_name(google_workspace_module.build_google_workspace_tools(settings))

    result = asyncio.run(tools["gmail_get_message"]("msg-1"))

    assert result["ok"] is True
    assert result["message"] == {
        "message_id": "msg-1",
        "thread_id": "thread-1",
        "from": "Alice <alice@example.com>",
        "to": "me@example.com",
        "cc": "",
        "bcc": "",
        "subject": "Trip update",
        "date": "Tue, 11 Mar 2026 10:00:00 +0000",
        "snippet": "Hello world",
        "label_ids": ["INBOX", "UNREAD"],
        "body_text": "Line one\nLine two",
        "truncated": False,
    }


def test_docs_append_text_fetches_document_then_batch_updates(monkeypatch):
    calls = []

    class FakeRunner:
        def __init__(self, settings):
            del settings

        async def run_json(self, *command, params=None, body=None):
            calls.append(
                {
                    "command": command,
                    "params": params,
                    "body": body,
                }
            )
            if command == ("docs", "documents", "get"):
                return {
                    "documentId": "doc-1",
                    "body": {
                        "content": [
                            {"endIndex": 1},
                            {
                                "paragraph": {
                                    "elements": [
                                        {
                                            "textRun": {
                                                "content": "Hello\n",
                                            }
                                        }
                                    ]
                                },
                                "endIndex": 7,
                            },
                        ]
                    },
                }
            if command == ("docs", "documents", "batchUpdate"):
                return {"replies": [{}]}
            raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(google_workspace_module, "GwsCliRunner", FakeRunner)
    settings = Settings(_env_file=None, gws_enabled=True)
    tools = _tools_by_name(google_workspace_module.build_google_workspace_tools(settings))

    result = asyncio.run(tools["docs_append_text"]("doc-1", "World"))

    assert result == {
        "ok": True,
        "document_id": "doc-1",
        "appended_chars": 5,
        "url": "https://docs.google.com/document/d/doc-1/edit",
    }
    assert calls == [
        {
            "command": ("docs", "documents", "get"),
            "params": {"documentId": "doc-1"},
            "body": None,
        },
        {
            "command": ("docs", "documents", "batchUpdate"),
            "params": {"documentId": "doc-1"},
            "body": {
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 6},
                            "text": "World",
                        }
                    }
                ]
            },
        },
    ]


def test_drive_search_files_builds_google_doc_query(monkeypatch):
    calls = []

    class FakeRunner:
        def __init__(self, settings):
            del settings

        async def run_json(self, *command, params=None, body=None):
            calls.append({"command": command, "params": params, "body": body})
            return {
                "files": [
                    {
                        "id": "file-1",
                        "name": "Trip Notes",
                        "mimeType": "application/vnd.google-apps.document",
                        "webViewLink": "https://docs.google.com/document/d/file-1/edit",
                        "modifiedTime": "2026-03-11T10:00:00Z",
                        "iconLink": "https://example.com/icon.png",
                    }
                ]
            }

    monkeypatch.setattr(google_workspace_module, "GwsCliRunner", FakeRunner)
    settings = Settings(_env_file=None, gws_enabled=True)
    tools = _tools_by_name(google_workspace_module.build_google_workspace_tools(settings))

    result = asyncio.run(
        tools["drive_search_files"]("Trip", max_results=5, file_type="google_doc")
    )

    assert result["ok"] is True
    assert result["files"] == [
        {
            "file_id": "file-1",
            "name": "Trip Notes",
            "mime_type": "application/vnd.google-apps.document",
            "web_view_link": "https://docs.google.com/document/d/file-1/edit",
            "modified_time": "2026-03-11T10:00:00Z",
            "icon_link": "https://example.com/icon.png",
        }
    ]
    assert calls[0]["command"] == ("drive", "files", "list")
    assert (
        calls[0]["params"]["q"]
        == "trashed = false and (name contains 'Trip' or fullText contains 'Trip') "
        "and mimeType = 'application/vnd.google-apps.document'"
    )


def test_calendar_update_event_requires_complete_time_range(monkeypatch):
    class FakeRunner:
        def __init__(self, settings):
            del settings

        async def run_json(self, *command, params=None, body=None):
            raise AssertionError("runner should not be called for invalid input")

    monkeypatch.setattr(google_workspace_module, "GwsCliRunner", FakeRunner)
    settings = Settings(_env_file=None, gws_enabled=True)
    tools = _tools_by_name(google_workspace_module.build_google_workspace_tools(settings))

    result = asyncio.run(
        tools["calendar_update_event"](
            "event-1",
            start_time="2026-03-11T10:00:00+00:00",
        )
    )

    assert result == {
        "ok": False,
        "error": "start_time and end_time must both be provided",
        "event_id": "event-1",
        "calendar_id": "primary",
    }


def _tools_by_name(tools):
    return {tool.__name__: tool for tool in tools}


def _gmail_b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
