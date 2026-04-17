"""Phase 3 smoke tests for the ``debug_trade`` CLI.

The CLI is a terminal-facing debugger — these tests don't lock
formatting (format can evolve) but DO lock the contract callers
depend on:

  1. Returns 0 for an existing trade_id, prints a Summary section,
     a "trades row" section, and a Timeline section.
  2. Returns 1 for a missing trade_id.
  3. Returns 2 for a missing DB path.
  4. Timeline section surfaces events actually emitted by the
     tracer for this trade_id.
"""

import uuid

import pytest

from polyphemus.performance_db import PerformanceDB
from polyphemus.trade_tracer import TradeTracer, EventType
from polyphemus.tools.debug_trade import main


def _db_with_trade(tmp_path, trade_id: str = "t-debug") -> str:
    path = str(tmp_path / f"debug_{uuid.uuid4().hex[:8]}.db")
    db = PerformanceDB(db_path=path)
    db.record_entry(
        trade_id=trade_id, token_id="0xtok", slug="debug-market",
        entry_time=1700000000.0, entry_price=0.55, entry_size=10.0,
        entry_tx_hash="0xhash", outcome="YES", market_title="Debug Market",
        fill_model="v2_probabilistic", fill_model_reason="prob_hit",
        signal_source="signal_bot_test",
        entry_mode="maker",
    )
    return path


class TestCliContract:
    def test_happy_path_returns_zero(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("POLYPHEMUS_TRACER_ENABLED", "true")
        path = _db_with_trade(tmp_path, "t-ok")
        tr = TradeTracer(db_path=path)
        tr.emit("t-ok", EventType.SIGNAL_FIRED, {"momentum": 0.42})
        tr.emit("t-ok", EventType.ORDER_PLACED, {"price": 0.55})
        tr.emit("t-ok", EventType.ORDER_FILLED, {"price": 0.55})

        rc = main(["t-ok", "--db", path])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Summary" in out
        assert "trades row" in out
        assert "Timeline" in out
        # Fields from the summary land verbatim
        assert "v2_probabilistic" in out
        assert "prob_hit" in out
        # Events show up
        assert "signal_fired" in out
        assert "order_placed" in out
        assert "order_filled" in out

    def test_missing_trade_id_returns_one(self, tmp_path, capsys):
        path = _db_with_trade(tmp_path, "t-real")
        rc = main(["t-not-here", "--db", path])
        err = capsys.readouterr().err
        assert rc == 1
        assert "not found" in err

    def test_missing_db_returns_two(self, tmp_path, capsys):
        bogus = str(tmp_path / "nowhere.db")
        rc = main(["t-anything", "--db", bogus])
        err = capsys.readouterr().err
        assert rc == 2
        assert "db not found" in err

    def test_empty_timeline_is_labeled(self, tmp_path, capsys):
        """A trade with no tracer events must still render — Phase 3
        landed with the flag off, so most trades will have an empty
        timeline until Phase 5 flips POLYPHEMUS_TRACER_ENABLED.
        """
        path = _db_with_trade(tmp_path, "t-empty")
        rc = main(["t-empty", "--db", path])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Timeline (0 events)" in out
        assert "tracer likely disabled" in out
