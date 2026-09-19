"""Tests for risk.py — RiskManager module."""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


@pytest.fixture(autouse=True)
def setup_risk_env(tmp_path):
    """Redirect database path for all risk tests."""
    db_path = tmp_path / "risk.db"
    data_dir = tmp_path
    data_dir.mkdir(exist_ok=True)
    with patch("risk.DATA_DIR", data_dir), patch("risk.DB_PATH", db_path):
        # Ensure twse_mis_cache exists (created by database.init_db, but risk module doesn't call it)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS twse_mis_cache (
                code TEXT NOT NULL PRIMARY KEY,
                last_price REAL,
                previous_close REAL,
                day_high REAL,
                day_low REAL,
                volume INTEGER,
                fetched_at REAL
            )
        """)
        conn.commit()
        conn.close()
        yield


class TestCalcPositionSize:
    """Tests for calc_position_size()."""

    def test_atr_based_sizing(self, setup_risk_env):
        """ATR-based position sizing should calculate correct shares."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000, risk_per_trade_pct=0.10)
        # max_capital = 100000, atr=30 -> shares = int(100000 / (30*1000)) * 1000 = 3000
        result = rm.calc_position_size(price=2460, atr=30)
        assert result == 3000

    def test_atr_based_sizing_rounds_down(self, setup_risk_env):
        """Position size should round down to nearest 1000 shares."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000, risk_per_trade_pct=0.10)
        # max_capital = 100000, atr=35 -> 100000/(35*1000) = 2.857 -> 2000
        result = rm.calc_position_size(price=2460, atr=35)
        assert result == 2000

    def test_fixed_pct_sizing_no_atr(self, setup_risk_env):
        """Without ATR, use fixed percentage of capital."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000, risk_per_trade_pct=0.10)
        # max_capital = 100000, price=2460 -> int(100000/2460)//1000*1000 = 0 (40//1000=0) -> max(0,1000) = 1000
        result = rm.calc_position_size(price=2460, atr=None)
        assert result == 1000

    def test_minimum_position_size(self, setup_risk_env):
        """Position size should never be less than 1000 (1 lot)."""
        from risk import RiskManager
        rm = RiskManager(total_capital=500_000, risk_per_trade_pct=0.01)
        # max_capital = 5000, price=100000 -> int(5000/100000)//1000*1000 = 0 -> max(0,1000) = 1000
        result = rm.calc_position_size(price=100_000, atr=None)
        assert result >= 1000

    def test_atr_zero_falls_back_to_fixed(self, setup_risk_env):
        """ATR=0 should fall back to fixed percentage sizing."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000, risk_per_trade_pct=0.10)
        # atr=0 -> falls to fixed: int(100000/500)//1000*1000 = 200
        result = rm.calc_position_size(price=500, atr=0)
        assert result >= 1000


class TestStopLossTakeProfit:
    """Tests for should_stop_loss and should_take_profit."""

    def test_should_stop_loss_triggered(self, setup_risk_env):
        """Stop loss triggered when current price <= stop_loss."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450, stop_loss=2400, take_profit=2600)
        assert rm.should_stop_loss("2330", 2390) is True
        assert rm.should_stop_loss("2330", 2400) is True  # Equal triggers

    def test_should_stop_loss_not_triggered(self, setup_risk_env):
        """Stop loss not triggered when price above stop_loss."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450, stop_loss=2400, take_profit=2600)
        assert rm.should_stop_loss("2330", 2410) is False
        assert rm.should_stop_loss("2330", 2500) is False

    def test_should_stop_loss_no_position(self, setup_risk_env):
        """No position means no stop loss trigger."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        assert rm.should_stop_loss("9999", 100) is False

    def test_should_stop_loss_none_stop_loss(self, setup_risk_env):
        """Position with stop_loss=None should not trigger."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450, stop_loss=None, take_profit=None)
        assert rm.should_stop_loss("2330", 100) is False

    def test_should_take_profit_triggered(self, setup_risk_env):
        """Take profit triggered when current price >= take_profit."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450, stop_loss=2400, take_profit=2600)
        assert rm.should_take_profit("2330", 2610) is True
        assert rm.should_take_profit("2330", 2600) is True  # Equal triggers

    def test_should_take_profit_not_triggered(self, setup_risk_env):
        """Take profit not triggered when price below take_profit."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450, stop_loss=2400, take_profit=2600)
        assert rm.should_take_profit("2330", 2590) is False
        assert rm.should_take_profit("2330", 2500) is False

    def test_should_take_profit_no_position(self, setup_risk_env):
        """No position means no take profit trigger."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        assert rm.should_take_profit("9999", 99999) is False


class TestPositionManagement:
    """Tests for add_position and close_position."""

    def test_add_new_position(self, setup_risk_env):
        """Adding a new position should work."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450, stop_loss=2400, take_profit=2600)
        summary = rm.get_portfolio_summary()
        assert len(summary["positions"]) == 1
        pos = summary["positions"][0]
        assert pos["code"] == "2330"
        assert pos["shares"] == 1000
        assert pos["avg_price"] == 2450

    def test_add_position_accumulates(self, setup_risk_env):
        """Adding to existing position updates average price."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450)
        rm.add_position("2330", 1000, 2550)
        summary = rm.get_portfolio_summary()
        pos = summary["positions"][0]
        assert pos["shares"] == 2000
        assert pos["avg_price"] == 2500.0

    def test_close_position_returns_pnl(self, setup_risk_env):
        """Close position returns correct PnL."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450)
        pnl = rm.close_position("2330", 2500)
        assert pnl == 50000.0  # (2500-2450) * 1000

    def test_close_position_not_found(self, setup_risk_env):
        """Closing non-existent position returns 0."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        pnl = rm.close_position("9999", 100)
        assert pnl == 0.0

    def test_close_position_updates_daily_pnl(self, setup_risk_env):
        """Close position updates daily PnL table."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450)
        rm.close_position("2330", 2500)
        # Check daily_pnl via internal db connection
        from risk import DB_PATH
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT pnl FROM daily_pnl").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 50000.0


class TestCanOpenNewPosition:
    """Tests for can_open_new_position()."""

    def test_can_open_when_empty(self, setup_risk_env):
        """Can open new position when portfolio is empty."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        result = rm.can_open_new_position()
        assert result == (True, "")

    def test_cannot_open_when_over_limit(self, setup_risk_env):
        """Cannot open when total position value exceeds limit."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000, max_total_position_pct=0.80)
        # Mock get_total_position_value to return over limit
        with patch.object(rm, "get_total_position_value", return_value=850_000):
            result = rm.can_open_new_position()
        assert result[0] is not True  # Returns string message
        assert "上限" in str(result[0]) or "850" in str(result)

    def test_cannot_open_when_daily_loss_exceeded(self, setup_risk_env):
        """Cannot open when daily loss exceeds max_daily_loss_pct."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000, max_daily_loss_pct=0.02)
        # Mock daily loss
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        with patch("risk.DB_PATH", rm._get_conn().__enter__() if False else None):
            from risk import DB_PATH as real_db
        import sqlite3
        from risk import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (date TEXT PRIMARY KEY, pnl REAL, num_trades INTEGER)
        """)
        conn.execute("INSERT OR REPLACE INTO daily_pnl VALUES (?, ?, 1)", (today, -25000))
        conn.commit()
        conn.close()
        result = rm.can_open_new_position()
        assert result[0] is not True


class TestGetPortfolioSummary:
    """Tests for get_portfolio_summary()."""

    def test_summary_with_no_positions(self, setup_risk_env):
        """Summary with no positions returns zeros."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        summary = rm.get_portfolio_summary()
        assert summary["total_position_value"] == 0.0
        assert summary["cash"] == 1_000_000
        assert summary["positions"] == []

    def test_summary_with_position(self, setup_risk_env):
        """Summary reflects current position."""
        from risk import RiskManager
        rm = RiskManager(total_capital=1_000_000)
        rm.add_position("2330", 1000, 2450)
        summary = rm.get_portfolio_summary()
        assert len(summary["positions"]) == 1
        pos = summary["positions"][0]
        assert pos["code"] == "2330"
        assert pos["shares"] == 1000
        # current_price should be avg_price since no live quote
        assert pos["current_price"] == 2450
