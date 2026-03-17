"""Feature 1: Log viewer + SSE streaming.

Acceptance criteria:
- GET /api/logs returns recent journalctl lines
- Line count respects `lines` param
- Sensitive data (PK, tokens) is filtered from output
- SSE endpoint streams live log events
- Unknown bot returns 404
- Auth required
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_get_logs_returns_recent_lines(client, auth_headers, mock_bots, mock_subprocess_journalctl):
    resp = await client.get("/api/logs?bot=testbot&lines=50", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "lines" in data
    assert isinstance(data["lines"], list)
    assert len(data["lines"]) > 0


async def test_get_logs_respects_line_limit(client, auth_headers, mock_bots, mock_subprocess_journalctl):
    resp = await client.get("/api/logs?bot=testbot&lines=10", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) <= 10


async def test_get_logs_filters_sensitive_data(client, auth_headers, mock_bots, mock_subprocess):
    """Private keys and tokens must NEVER appear in log output."""
    sensitive_log = (
        "Loading PRIVATE_KEY=0xdeadbeef from .env\n"
        "DASHBOARD_TOKEN=secret123 loaded\n"
        "API_KEY=abc123xyz loaded\n"
        "Normal log line here\n"
    )
    mock_subprocess.return_value.stdout = sensitive_log
    mock_subprocess.return_value.returncode = 0

    resp = await client.get("/api/logs?bot=testbot&lines=100", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    full_output = "\n".join(data["lines"])
    assert "0xdeadbeef" not in full_output
    assert "secret123" not in full_output
    assert "abc123xyz" not in full_output


async def test_get_logs_unknown_bot_returns_404(client, auth_headers, mock_bots):
    resp = await client.get("/api/logs?bot=nonexistent&lines=10", headers=auth_headers)
    assert resp.status_code == 404


async def test_get_logs_requires_auth(client, bad_auth_headers, mock_bots):
    resp = await client.get("/api/logs?bot=testbot", headers=bad_auth_headers)
    assert resp.status_code == 401


async def test_get_logs_no_auth_returns_401(client, mock_bots):
    resp = await client.get("/api/logs?bot=testbot")
    # No auth header at all
    assert resp.status_code in (401, 403)


async def test_get_logs_filter_by_keyword(client, auth_headers, mock_bots, mock_subprocess_journalctl):
    """Optional filter param narrows log output."""
    resp = await client.get("/api/logs?bot=testbot&lines=50&filter=SIGNAL", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # All returned lines should contain the filter keyword
    for line in data["lines"]:
        assert "SIGNAL" in line.upper() or len(data["lines"]) == 0


async def test_log_stream_returns_sse_content_type(client, auth_headers, mock_bots, mock_subprocess):
    """SSE endpoint must return text/event-stream content type."""
    # SSE is a streaming response -- we just verify the endpoint exists and returns correct type
    resp = await client.get("/api/logs/stream?bot=testbot", headers=auth_headers)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
