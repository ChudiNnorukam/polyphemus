"""Shared fixtures for dashboard API tests.

Mocks VPS-specific dependencies (systemctl, journalctl, file paths)
so tests run on any dev machine.
"""

import os
import sys
import sqlite3
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Set test env BEFORE importing app
os.environ["DASHBOARD_TOKEN"] = "test-token-123"
os.environ["DASHBOARD_READ_ONLY"] = "false"
os.environ.pop("DASHBOARD_ALLOWED_IPS", None)

# Add parent dir to path so we can import api
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pytest_asyncio
from api import app, init_audit_db


# ---------------------------------------------------------------------------
# Auto-reset rate limiter state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_middleware_state():
    """Clear rate limiter _hits dicts to prevent 429s across tests."""
    def _clear(mw, visited=None):
        if visited is None:
            visited = set()
        if mw is None or id(mw) in visited:
            return
        visited.add(id(mw))
        if hasattr(mw, '_hits'):
            mw._hits.clear()
        _clear(getattr(mw, 'app', None), visited)

    _clear(app.middleware_stack)
    yield
    _clear(app.middleware_stack)


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token-123"}


@pytest.fixture
def bad_auth_headers():
    return {"Authorization": "Bearer wrong-token"}


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Temp environment fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_bot_dir(tmp_path):
    """Create a temporary bot directory with .env and data dir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DRY_RUN=true\n"
        "MAX_BET=100\n"
        "MIN_ENTRY_PRICE=0.85\n"
        "MAX_ENTRY_PRICE=0.95\n"
        "PRIVATE_KEY=0xdeadbeef_secret\n"
        "DASHBOARD_TOKEN=shouldnotleak\n"
        "ASSET_FILTER=BTC,ETH\n"
    )

    # Create a performance.db
    db_path = data_dir / "performance.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE trades (
        trade_id TEXT, slug TEXT, token_id TEXT,
        entry_time REAL, entry_price REAL, entry_size REAL,
        exit_time REAL, exit_price REAL, exit_reason TEXT,
        pnl REAL, market_title TEXT, source TEXT
    )""")
    conn.commit()
    conn.close()

    # Create signals.db
    sig_path = data_dir / "signals.db"
    conn = sqlite3.connect(str(sig_path))
    conn.execute("""CREATE TABLE signals (
        timestamp TEXT, asset TEXT, direction TEXT, midpoint REAL,
        guard_passed INTEGER, guard_reasons TEXT, slug TEXT,
        time_remaining_secs REAL, pnl REAL, outcome TEXT
    )""")
    conn.commit()
    conn.close()

    return tmp_path


@pytest.fixture
def mock_bots(tmp_bot_dir):
    """Patch BOTS config and DEPLOY_TARGET_DIR to use temp directories."""
    deploy_dir = tmp_bot_dir / "deploy"
    deploy_dir.mkdir()
    bots = {
        "testbot": {
            "db": str(tmp_bot_dir / "data" / "performance.db"),
            "signals_db": str(tmp_bot_dir / "data" / "signals.db"),
            "env": str(tmp_bot_dir / ".env"),
            "service": "lagbot@testbot",
            "health_dir": str(tmp_bot_dir / "data"),
            "kill_switch": str(tmp_bot_dir / "KILL_SWITCH"),
        },
    }
    with patch("api.BOTS", bots), patch("api.DEPLOY_TARGET_DIR", str(deploy_dir)):
        yield bots


@pytest.fixture
def mock_audit_db(tmp_path):
    """Provide a temp path for the audit database and init the table."""
    db_path = str(tmp_path / "audit.db")
    with patch("api.AUDIT_DB_PATH", db_path):
        init_audit_db()
        yield db_path


# ---------------------------------------------------------------------------
# Subprocess mock helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run for systemctl/journalctl calls."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="active\n",
            stderr="",
        )
        yield mock_run


@pytest.fixture
def mock_subprocess_journalctl():
    """Mock subprocess for journalctl with sample log output."""
    sample_logs = (
        "Mar 17 10:00:01 vps lagbot[1234]: [SIGNAL] BTC UP momentum 0.87\n"
        "Mar 17 10:00:02 vps lagbot[1234]: [GUARD PASS] entry_price=0.87\n"
        "Mar 17 10:00:03 vps lagbot[1234]: [ORDER] BUY 10 shares at 0.87\n"
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=sample_logs,
            stderr="",
        )
        yield mock_run


# ---------------------------------------------------------------------------
# Deploy fixtures
# ---------------------------------------------------------------------------

VALID_DEPLOY_FILE = {
    "name": "signal_bot.py",
    "content": "# valid python\nprint('hello')\n",
}

INVALID_SYNTAX_FILE = {
    "name": "signal_bot.py",
    "content": "def broken(\n",
}

UNLISTED_FILE = {
    "name": "malicious_backdoor.py",
    "content": "import os; os.system('rm -rf /')\n",
}


@pytest.fixture
def valid_deploy_payload():
    return {"bot": "testbot", "files": [VALID_DEPLOY_FILE]}


@pytest.fixture
def invalid_syntax_payload():
    return {"bot": "testbot", "files": [INVALID_SYNTAX_FILE]}


@pytest.fixture
def unlisted_file_payload():
    return {"bot": "testbot", "files": [UNLISTED_FILE]}
