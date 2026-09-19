"""Unit tests for signals_wall.py.

Tests all 5 signal calculations with controlled/mock data,
format_message() output, and scan_watchlist() edge cases.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# Set required env vars BEFORE importing signals_wall
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test_chat_id")
os.environ.setdefault("ALERT_ENABLED", "true")

# Mock heavy dependencies that signals_wall imports
mock_alert_config = MagicMock()
mock_alert_config.WATCH_LIST = []
mock_alert_config.TELEGRAM_BOT_TOKEN = "test_token"
mock_alert_config.TELEGRAM_CHAT_ID = "test_chat_id"
mock_alert_config.ALERT_ENABLED = True

mock_fetch_data = MagicMock()
mock_database = MagicMock()

sys.modules["alert_config_cloud"] = mock_alert_config
sys.modules["fetch_data"] = mock_fetch_data
sys.modules["database"] = mock_database

# Now import signals_wall
sys.path.insert(0, str(Path(__file__).parent))
import signals_wall as sw


# ---------------------------------------------------------------------------
# Helper: build a minimal OHLCV DataFrame
# ---------------------------------------------------------------------------
def make_df(
    closes: list[float],
    volumes: list[int] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    start_date: str = "2024-01-01",
) -> pd.DataFrame:
    """Build a DataFrame with date index and required columns."""
    dates = pd.bdate_range(start=start_date, periods=len(closes))
    n = len(closes)

    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]

    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. breakthrough_ma20
# ---------------------------------------------------------------------------
class TestBreakthroughMA20(unittest.TestCase):
    def test_triggers_on_cross_above(self):
        """Price below MA20 yesterday, above MA20 today → trigger."""
        # Build 30 days: first 29 with price ~10, last day spike above MA20
        # MA20 will be ~10 (from first 20 days), then price jumps to 15
        closes = [10.0] * 29 + [15.0]
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertIn("突破月線↑", result)

    def test_no_trigger_when_stay_above(self):
        """Price already above MA20 both days → no trigger."""
        closes = [20.0] * 30  # All same → MA20=20, close=20 (not strictly >)
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("突破月線↑", result)

    def test_no_trigger_when_stay_below(self):
        """Price below MA20 both days → no trigger."""
        closes = [10.0] * 30  # MA20=10, close=10 (not >)
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("突破月線↑", result)

    def test_no_trigger_on_cross_below(self):
        """Price above MA20 yesterday, below today → no trigger."""
        # Day 29 at 15, day 30 at 5 → crosses below, not above
        closes = [10.0] * 10 + [15.0] * 19 + [5.0]
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("突破月線↑", result)


# ---------------------------------------------------------------------------
# 2. KD golden cross
# ---------------------------------------------------------------------------
class TestKDGoldenCross(unittest.TestCase):
    def test_triggers_on_k_cross_above_d(self):
        """K crosses above D → trigger."""
        # Long slow decline then massive spike on last day
        closes = []
        for i in range(28):
            closes.append(100 - i * 0.3)
        closes.append(91.6)  # day 29 - still declining
        closes.append(300)  # day 30 - massive spike

        dates = pd.bdate_range(start="2024-01-01", periods=len(closes))
        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c * 1.005 for c in closes],
                "Low": [c * 0.995 for c in closes],
                "Close": closes,
                "Volume": [1_000_000] * len(closes),
            },
            index=dates,
        )

        k, d = sw.calculate_kd(df["High"], df["Low"], df["Close"])

        # Verify crossover condition
        crossover = (k.iloc[-2] <= d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1])
        self.assertTrue(
            crossover,
            f"K/D values: k[-2]={k.iloc[-2]:.2f}, d[-2]={d.iloc[-2]:.2f}, "
            f"k[-1]={k.iloc[-1]:.2f}, d[-1]={d.iloc[-1]:.2f}"
        )

        result = sw.check_signals(df)
        self.assertIn("KD 黃金交叉↑", result)

    def test_no_trigger_when_k_stays_above(self):
        """K already above D → no trigger."""
        # Strong uptrend throughout → K stays above D
        closes = [100 + i * 3 for i in range(30)]
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("KD 黃金交叉↑", result)

    def test_no_trigger_when_k_stays_below(self):
        """K already below D → no trigger."""
        # Strong downtrend throughout → K stays below D
        closes = [100 - i * 3 for i in range(30)]
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("KD 黃金交叉↑", result)


# ---------------------------------------------------------------------------
# 3. MACD flip (DIF turns positive)
# ---------------------------------------------------------------------------
class TestMACDFlip(unittest.TestCase):
    def test_triggers_on_dif_turns_positive(self):
        """DIF ≤ 0 yesterday, DIF > 0 today → trigger."""
        # Long decline then massive spike on last day
        closes = []
        for i in range(28):
            closes.append(100 - i * 0.5)
        closes.append(50)  # day 29 - still declining
        closes.append(300)  # day 30 - massive spike

        dates = pd.bdate_range(start="2024-01-01", periods=len(closes))
        df = pd.DataFrame(
            {
                "Open": closes,
                "High": [c * 1.005 for c in closes],
                "Low": [c * 0.995 for c in closes],
                "Close": closes,
                "Volume": [1_000_000] * len(closes),
            },
            index=dates,
        )

        dif, dea, hist = sw.calculate_macd(df["Close"])

        # Verify DIF crossed from ≤0 to >0
        self.assertTrue(
            dif.iloc[-2] <= 0 and dif.iloc[-1] > 0,
            f"DIF values: dif[-2]={dif.iloc[-2]:.4f}, dif[-1]={dif.iloc[-1]:.4f}"
        )

        result = sw.check_signals(df)
        self.assertIn("MACD 翻多↑", result)

    def test_no_trigger_when_dif_stays_positive(self):
        """DIF already positive → no trigger."""
        closes = [100 + i * 2 for i in range(30)]
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("MACD 翻多↑", result)

    def test_no_trigger_when_dif_stays_negative(self):
        """DIF stays negative → no trigger."""
        closes = [100 - i * 2 for i in range(30)]
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertNotIn("MACD 翻多↑", result)


# ---------------------------------------------------------------------------
# 4. Volume spike
# ---------------------------------------------------------------------------
class TestVolumeSpike(unittest.TestCase):
    def _make_volume_spike_df(self, base_vol: int, spike_vol: int, n_days: int = 30):
        """Create a DataFrame with controlled volume pattern.
        
        MA5 uses last 5 days. To get a clean ratio:
        - Days 1-25: base_vol
        - Days 26-29: adjusted so MA5 of last day = base_vol
        - Day 30: spike_vol
        
        For MA5 at last day to equal base_vol:
        vol[-5] + vol[-4] + vol[-3] + vol[-2] + vol[-1] = 5 * base_vol
        If vol[-1] = spike_vol, then vol[-5:-1] sum = 5*base_vol - spike_vol
        Set days 26-29 to (5*base_vol - spike_vol) / 4 each
        """
        closes = [100.0] * n_days
        volumes = [base_vol] * (n_days - 5)
        
        # Days 26-29 (indices 25-28): adjusted volume
        adj_vol = (5 * base_vol - spike_vol) // 4
        volumes.extend([adj_vol] * 4)
        # Day 30: spike
        volumes.append(spike_vol)
        
        return make_df(closes, volumes=volumes)

    def test_triggers_on_volume_spike(self):
        """Volume > 1.5x MA5 volume → trigger."""
        # base_vol=1M, spike=2M, MA5 should be 1M, ratio=2.0x
        df = self._make_volume_spike_df(base_vol=1_000_000, spike_vol=2_000_000)
        result = sw.check_signals(df)
        self.assertIn("放量 2.0x↑", result)

    def test_no_trigger_when_volume_low(self):
        """Volume below 1.5x threshold → no trigger."""
        # base_vol=1M, spike=1.2M, ratio=1.2x
        df = self._make_volume_spike_df(base_vol=1_000_000, spike_vol=1_200_000)
        result = sw.check_signals(df)
        volume_signals = [s for s in result if s.startswith("放量")]
        self.assertEqual(len(volume_signals), 0)

    def test_triggers_at_exact_threshold(self):
        """Volume exactly 1.5x → triggers (>= 1.5)."""
        # base_vol=1M, spike=1.5M, MA5=1M, ratio=1.5x
        df = self._make_volume_spike_df(base_vol=1_000_000, spike_vol=1_500_000)
        result = sw.check_signals(df)
        self.assertIn("放量 1.5x↑", result)

    def test_no_trigger_just_below_threshold(self):
        """Volume at 1.49x → no trigger."""
        # base_vol=1M, spike=1.49M, ratio=1.49x
        df = self._make_volume_spike_df(base_vol=1_000_000, spike_vol=1_490_000)
        result = sw.check_signals(df)
        volume_signals = [s for s in result if s.startswith("放量")]
        self.assertEqual(len(volume_signals), 0)


# ---------------------------------------------------------------------------
# 5. RSI oversold
# ---------------------------------------------------------------------------
class TestRSIOversold(unittest.TestCase):
    def test_triggers_on_rsi_below_30(self):
        """RSI < 30 → trigger."""
        # Strong downtrend → RSI drops below 30
        closes = [100.0 - i * 3 for i in range(30)]  # Big declines
        df = make_df(closes)
        rsi = sw.calculate_rsi(df["Close"])
        self.assertLess(rsi.iloc[-1], 30, f"RSI should be < 30, got {rsi.iloc[-1]:.2f}")

        result = sw.check_signals(df)
        rsi_signals = [s for s in result if s.startswith("RSI")]
        self.assertEqual(len(rsi_signals), 1)
        self.assertIn("超賣", rsi_signals[0])

    def test_no_trigger_when_rsi_above_30(self):
        """RSI > 30 → no trigger."""
        closes = [100 + i for i in range(30)]  # Uptrend
        df = make_df(closes)
        result = sw.check_signals(df)
        rsi_signals = [s for s in result if s.startswith("RSI")]
        self.assertEqual(len(rsi_signals), 0)

    def test_no_trigger_at_rsi_30(self):
        """RSI exactly 30 → no trigger (strictly < 30)."""
        # This is hard to hit exactly, so we test the boundary logic directly
        # by mocking RSI
        with patch.object(sw, "calculate_rsi") as mock_rsi:
            mock_rsi.return_value = pd.Series([35.0] * 29 + [30.0])
            closes = [100.0] * 30
            df = make_df(closes)
            result = sw.check_signals(df)
            rsi_signals = [s for s in result if s.startswith("RSI")]
            self.assertEqual(len(rsi_signals), 0)

    def test_triggers_at_rsi_29(self):
        """RSI = 29 → triggers."""
        with patch.object(sw, "calculate_rsi") as mock_rsi:
            mock_rsi.return_value = pd.Series([50.0] * 29 + [29.0])
            closes = [100.0] * 30
            df = make_df(closes)
            result = sw.check_signals(df)
            rsi_signals = [s for s in result if s.startswith("RSI")]
            self.assertEqual(len(rsi_signals), 1)


# ---------------------------------------------------------------------------
# format_message()
# ---------------------------------------------------------------------------
class TestFormatMessage(unittest.TestCase):
    def test_empty_triggered_returns_empty_string(self):
        """No triggered stocks → empty string."""
        result = sw.format_message([])
        self.assertEqual(result, "")

    def test_single_stock_single_signal(self):
        """One stock, one signal → correct format."""
        triggered = [{"code": "2330", "label": "台積電", "signals": ["突破月線↑"]}]
        result = sw.format_message(triggered)
        today = date.today().strftime("%Y-%m-%d")
        self.assertIn(f"🔔 訊號牆 {today}", result)
        self.assertIn("2330 台積電：突破月線↑", result)

    def test_single_stock_multiple_signals(self):
        """Multiple signals joined with ' + '."""
        triggered = [
            {"code": "2330", "label": "台積電", "signals": ["突破月線↑", "KD 黃金交叉↑"]}
        ]
        result = sw.format_message(triggered)
        self.assertIn("突破月線↑ + KD 黃金交叉↑", result)

    def test_multiple_stocks(self):
        """Multiple stocks each on their own line."""
        triggered = [
            {"code": "2330", "label": "台積電", "signals": ["突破月線↑"]},
            {"code": "2317", "label": "鴻海", "signals": ["放量 2.0x↑"]},
        ]
        result = sw.format_message(triggered)
        self.assertIn("2330 台積電：突破月線↑", result)
        self.assertIn("2317 鴻海：放量 2.0x↑", result)

    def test_date_in_header(self):
        """Header contains today's date in YYYY-MM-DD format."""
        triggered = [{"code": "0050", "label": "元大台灣50", "signals": ["RSI 28 超賣↑"]}]
        result = sw.format_message(triggered)
        today = date.today().strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(f"🔔 訊號牆 {today}"))

    def test_newline_separated(self):
        """Lines are separated by newline."""
        triggered = [
            {"code": "A", "label": "A", "signals": ["s1"]},
            {"code": "B", "label": "B", "signals": ["s2"]},
        ]
        result = sw.format_message(triggered)
        lines = result.split("\n")
        self.assertEqual(len(lines), 3)  # header + 2 stocks


# ---------------------------------------------------------------------------
# scan_watchlist() with empty WATCH_LIST
# ---------------------------------------------------------------------------
class TestScanWatchlist(unittest.TestCase):
    def test_empty_watchlist_returns_empty_list(self):
        """Empty WATCH_LIST → empty triggered list."""
        with patch("signals_wall.WATCH_LIST", []):
            result = sw.scan_watchlist()
        self.assertEqual(result, [])

    def test_empty_watchlist_no_api_calls(self):
        """Empty WATCH_LIST → no API calls made."""
        with patch("signals_wall.WATCH_LIST", []):
            sw.scan_watchlist()
        mock_fetch_data.get_history.assert_not_called()


# ---------------------------------------------------------------------------
# check_signals() edge cases
# ---------------------------------------------------------------------------
class TestCheckSignalsEdgeCases(unittest.TestCase):
    def test_insufficient_data_returns_empty(self):
        """Less than 30 rows → empty list."""
        closes = [100.0] * 10
        df = make_df(closes)
        result = sw.check_signals(df)
        self.assertEqual(result, [])

    def test_exactly_30_rows_works(self):
        """Exactly 30 rows → processes signals."""
        closes = [100.0] * 29 + [105.0]
        df = make_df(closes)
        result = sw.check_signals(df)
        # Should return a list (may or may not have signals)
        self.assertIsInstance(result, list)

    def test_no_signals_returns_empty(self):
        """Flat data with no trend → no signals."""
        # All same price → no MA crossover, no KD cross, no MACD flip, normal volume, RSI neutral
        closes = [100.0] * 30
        volumes = [1_000_000] * 30
        df = make_df(closes, volumes=volumes)
        result = sw.check_signals(df)
        self.assertEqual(result, [])

    def test_multiple_signals_same_day(self):
        """Multiple signals can trigger simultaneously."""
        # breakthrough_ma20: price below MA20 for 29 days, then above on day 30
        # volume_spike: control volume so MA5 = 1M, spike = 2M → 2.0x
        closes = [10.0] * 29 + [15.0]
        
        # Volume: days 1-25 = 1M, days 26-29 = 750k (so MA5 at day 30 = 1M), day 30 = 2M
        # MA5 = (750k + 750k + 750k + 750k + 2M) / 5 = 5M/5 = 1M
        # Ratio = 2M / 1M = 2.0x
        volumes = [1_000_000] * 25 + [750_000] * 4 + [2_000_000]
        
        df = make_df(closes, volumes=volumes)
        result = sw.check_signals(df)
        self.assertIn("突破月線↑", result)
        self.assertIn("放量 2.0x↑", result)


# ---------------------------------------------------------------------------
# Helper functions (calculate_ma, calculate_rsi, calculate_kd, calculate_macd)
# ---------------------------------------------------------------------------
class TestCalculateMA(unittest.TestCase):
    def test_ma_basic(self):
        series = pd.Series([1, 2, 3, 4, 5])
        result = sw.calculate_ma(series, 3)
        expected = pd.Series([np.nan, np.nan, 2.0, 3.0, 4.0])
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_ma_min_periods(self):
        series = pd.Series([1, 2])
        result = sw.calculate_ma(series, 20)
        self.assertTrue(result.isna().all())


class TestCalculateRSI(unittest.TestCase):
    def test_rsi_all_up(self):
        """All gains → RSI = 100."""
        series = pd.Series([float(i) for i in range(1, 50)])
        result = sw.calculate_rsi(series)
        self.assertAlmostEqual(result.iloc[-1], 100.0, places=1)

    def test_rsi_all_down(self):
        """All losses → RSI = 0."""
        series = pd.Series([float(i) for i in range(50, 0, -1)])
        result = sw.calculate_rsi(series)
        self.assertAlmostEqual(result.iloc[-1], 0.0, places=1)

    def test_rsi_insufficient_data(self):
        series = pd.Series([100.0] * 5)
        result = sw.calculate_rsi(series)
        # First 14 should be NaN
        self.assertTrue(result.iloc[:14].isna().all())


class TestCalculateKD(unittest.TestCase):
    def test_kd_range(self):
        """K and D should be in [0, 100] range."""
        np.random.seed(42)
        closes = 100 + np.random.randn(50).cumsum()
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        df = make_df(closes.tolist(), highs=highs, lows=lows)
        k, d = sw.calculate_kd(df["High"], df["Low"], df["Close"])
        # K and D should be in valid range
        self.assertTrue((k.dropna() >= 0).all() and (k.dropna() <= 100).all())
        self.assertTrue((d.dropna() >= 0).all() and (d.dropna() <= 100).all())


class TestCalculateMACD(unittest.TestCase):
    def test_macd_flat_series(self):
        """Flat close → DIF = 0."""
        series = pd.Series([100.0] * 50)
        dif, dea, hist = sw.calculate_macd(series)
        self.assertAlmostEqual(dif.iloc[-1], 0.0, places=6)
        self.assertAlmostEqual(dea.iloc[-1], 0.0, places=6)
        self.assertAlmostEqual(hist.iloc[-1], 0.0, places=6)

    def test_macd_uptrend(self):
        """Uptrend → DIF > 0."""
        series = pd.Series([float(i) for i in range(1, 50)])
        dif, dea, hist = sw.calculate_macd(series)
        self.assertGreater(dif.iloc[-1], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
