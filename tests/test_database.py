"""Tests for database.py — SQLite layer for stock data."""

import pytest
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd

# Patch DATA_DIR/DB_PATH before importing database module
@pytest.fixture(autouse=True)
def setup_db_env(tmp_path):
    """Redirect database to a temp path for all tests."""
    db_path = tmp_path / "stock.db"
    data_dir = tmp_path
    data_dir.mkdir(exist_ok=True)
    with patch("database.DATA_DIR", data_dir), patch("database.DB_PATH", db_path):
        yield


class TestInitDB:
    """Tests for init_db() function."""

    def test_init_db_creates_tables(self, setup_db_env):
        """init_db creates all required tables."""
        from database import init_db
        init_db()
        from database import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        conn.close()
        # database.py creates these core tables; positions/trades/daily_pnl are in risk.py
        expected = [
            "code_market", "institutional_flow", "margin_balance",
            "warrant_flow", "warrant_large_trade", "twse_mis_cache",
            "history_cache",
        ]
        for t in expected:
            assert t in tables, f"Table {t} missing"

    def test_init_db_is_idempotent(self, setup_db_env):
        """Calling init_db twice should not raise."""
        from database import init_db
        init_db()
        init_db()  # Should not raise


class TestUpsertAndQuery:
    """Tests for upsert and query operations."""

    def test_upsert_and_get_institutional_flow(self, setup_db_env, sample_institutional_df):
        """Round-trip: upsert then query institutional flow data."""
        from database import init_db, upsert_institutional, get_institutional_flow
        init_db()
        upsert_institutional(sample_institutional_df, "2330", market="listed")
        result = get_institutional_flow("2330", "2026-09-01", "2026-09-07")
        assert len(result) == 5
        assert "foreign_net" in result.columns
        assert "trust_net" in result.columns
        assert "dealer_net" in result.columns
        assert "total_net" in result.columns

    def test_upsert_institutional_empty_df(self, setup_db_env):
        """Upsert with empty DataFrame should be a no-op."""
        from database import init_db, upsert_institutional, get_institutional_flow
        init_db()
        empty_df = pd.DataFrame(columns=["foreign_net", "trust_net", "dealer_net", "total_net"])
        upsert_institutional(empty_df, "2330")
        result = get_institutional_flow("2330", "2026-09-01", "2026-09-07")
        assert result.empty

    def test_get_institutional_flow_empty_result(self, setup_db_env):
        """Query with no matching data returns empty DataFrame."""
        from database import init_db, get_institutional_flow
        init_db()
        result = get_institutional_flow("9999", "2026-01-01", "2026-12-31")
        assert result.empty

    def test_upsert_and_get_margin(self, setup_db_env):
        """Round-trip margin balance data."""
        from database import init_db, upsert_margin, get_margin
        init_db()
        dates = pd.date_range("2026-09-01", periods=3, freq="B")
        df = pd.DataFrame({
            "margin_balance": [5000, 5200, 4800],
            "short_balance": [100, 120, 90],
        }, index=dates)
        upsert_margin(df, "2330")
        result = get_margin("2330", "2026-09-01", "2026-09-07")
        assert len(result) == 3
        assert result.iloc[0]["margin_balance"] == 5000

    def test_upsert_and_get_warrant_flow(self, setup_db_env, sample_warrant_df):
        """Round-trip warrant flow data."""
        from database import init_db, upsert_warrant_flow, get_warrant_flow
        init_db()
        upsert_warrant_flow(sample_warrant_df, "2330")
        result = get_warrant_flow("2330", "2026-09-01", "2026-09-07")
        assert len(result) == 3
        assert "call_volume" in result.columns
        assert "put_volume" in result.columns

    def test_upsert_history_and_get(self, setup_db_env, sample_ohlcv_df):
        """Round-trip history cache data."""
        from database import init_db, upsert_history, get_history
        init_db()
        upsert_history(sample_ohlcv_df, "2330")
        result = get_history("2330", period="1y")
        assert result is not None
        assert len(result) == 5
        assert "open" in result.columns
        assert "close" in result.columns

    def test_upsert_institutional_overwrite(self, setup_db_env, sample_institutional_df):
        """Upserting same data should update, not duplicate."""
        from database import init_db, upsert_institutional, get_institutional_flow
        init_db()
        upsert_institutional(sample_institutional_df, "2330")
        # Upsert again with modified values
        modified_df = sample_institutional_df.copy()
        modified_df["foreign_net"] = 9999
        upsert_institutional(modified_df, "2330")
        result = get_institutional_flow("2330", "2026-09-01", "2026-09-07")
        assert len(result) == 5  # Still 5 rows (upsert, not append)
        assert result.iloc[0]["foreign_net"] == 9999


class TestCacheExpiration:
    """Tests for cache TTL logic."""

    def test_get_history_returns_none_when_expired(self, setup_db_env):
        """Cache older than max_age_hours should return None."""
        from database import init_db, upsert_history, get_history
        init_db()
        dates = pd.date_range("2026-09-01", periods=3, freq="B")
        df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
            "Volume": [1000000, 1100000, 1200000],
        }, index=dates)
        upsert_history(df, "2330")
        # Patch time.time to simulate expiry
        future = time.time() + 49 * 3600  # 49 hours later
        with patch("database.time") as mock_time:
            mock_time.time.return_value = future
            result = get_history("2330", period="1y")
        assert result is None

    def test_cleanup_history_cache_deletes_old(self, setup_db_env):
        """cleanup_history_cache removes entries older than threshold."""
        from database import init_db, upsert_history, cleanup_history_cache
        init_db()
        dates = pd.date_range("2026-09-01", periods=3, freq="B")
        df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
            "Volume": [1000000, 1100000, 1200000],
        }, index=dates)
        upsert_history(df, "2330")
        # Patch time.time so cleanup sees entries as > 48h old
        future = time.time() + 72 * 3600
        with patch("database.time") as mock_time:
            mock_time.time.return_value = future
            deleted = cleanup_history_cache(max_age_hours=48)
        assert deleted == 3


class TestMisCache:
    """Tests for TWSE MIS quote cache."""

    def test_upsert_and_get_mis_quote(self, setup_db_env):
        """Round-trip MIS quote cache."""
        from database import init_db, upsert_mis_quote, get_mis_quote
        init_db()
        quote = {
            "last_price": 2460.0,
            "previous_close": 2450.0,
            "day_high": 2480.0,
            "day_low": 2440.0,
            "volume": 5000,
        }
        upsert_mis_quote("2330", quote)
        result = get_mis_quote("2330", max_age_seconds=60)
        assert result is not None
        assert result["last_price"] == 2460.0
        assert result["source"] == "twse_mis_cache"

    def test_get_mis_quote_expired(self, setup_db_env):
        """Expired MIS quote should return None."""
        from database import init_db, upsert_mis_quote, get_mis_quote
        init_db()
        quote = {
            "last_price": 2460.0,
            "previous_close": 2450.0,
            "day_high": 2480.0,
            "day_low": 2440.0,
            "volume": 5000,
        }
        upsert_mis_quote("2330", quote)
        # Patch time so it's expired
        future = time.time() + 120
        with patch("database.time") as mock_time:
            mock_time.time.return_value = future
            result = get_mis_quote("2330", max_age_seconds=60)
        assert result is None

    def test_get_mis_quote_not_found(self, setup_db_env):
        """Query for non-existent code returns None."""
        from database import init_db, get_mis_quote
        init_db()
        result = get_mis_quote("9999", max_age_seconds=60)
        assert result is None

    def test_cleanup_old_cache(self, setup_db_env):
        """cleanup_old_cache removes expired entries."""
        from database import init_db, upsert_mis_quote, cleanup_old_cache
        init_db()
        quote = {"last_price": 100.0, "previous_close": 99.0,
                 "day_high": 105.0, "day_low": 95.0, "volume": 1000}
        upsert_mis_quote("1101", quote)
        future = time.time() + 7200  # 2 hours later
        with patch("database.time") as mock_time:
            mock_time.time.return_value = future
            cleanup_old_cache(max_age_seconds=3600)
        # After cleanup, the entry should be expired
        result = __import__("database").get_mis_quote("1101", max_age_seconds=60)
        assert result is None


class TestVacuum:
    """Tests for vacuum function."""

    def test_vacuum_runs_without_error(self, setup_db_env):
        """vacuum should execute without raising."""
        from database import init_db, vacuum
        init_db()
        vacuum()  # Should not raise
