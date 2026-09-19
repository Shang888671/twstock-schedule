"""Unit tests for morning_brief.py — US market data formatting, VIX detection, futures fallback."""

import io
import sys
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

# Ensure stock-model is in path
sys.path.insert(0, r"C:\Users\Ryzen USER\Documents\ClaudeMemory\stock-model")

import morning_brief as mb


class TestFormatPct(unittest.TestCase):
    """Test percentage formatting helper."""

    def test_positive(self):
        self.assertEqual(mb._fmt_pct(1.234), "+1.23%")

    def test_negative(self):
        self.assertEqual(mb._fmt_pct(-2.567), "-2.57%")

    def test_zero(self):
        self.assertEqual(mb._fmt_pct(0.0), "+0.00%")

    def test_none(self):
        self.assertEqual(mb._fmt_pct(None), "N/A")

    def test_small_negative(self):
        self.assertEqual(mb._fmt_pct(-0.004), "-0.00%")


class TestFormatPts(unittest.TestCase):
    """Test points formatting helper."""

    def test_positive(self):
        self.assertEqual(mb._fmt_pts(123.4), "+123.4 點")

    def test_negative(self):
        self.assertEqual(mb._fmt_pts(-56.78), "-56.8 點")

    def test_none(self):
        self.assertEqual(mb._fmt_pts(None), "N/A")


class TestFormatPrice(unittest.TestCase):
    """Test price formatting helper."""

    def test_normal(self):
        self.assertEqual(mb._fmt_price(4567.89), "4567.89")

    def test_none(self):
        self.assertEqual(mb._fmt_price(None), "N/A")


class TestFormatMessage(unittest.TestCase):
    """Test full message formatting with all data present."""

    def test_full_message(self):
        us = {
            "S&P 500": {"last": 5700.0, "prev_close": 5650.0, "change_pct": 0.88},
            "NASDAQ": {"last": 18000.0, "prev_close": 17800.0, "change_pct": 1.12},
            "費半": {"last": 5500.0, "prev_close": 5450.0, "change_pct": 0.92},
            "VIX": {"last": 15.5, "prev_close": 16.0, "change_pct": -3.12},
        }
        futures = {
            "label": "台指期",
            "last": 22500.0,
            "prev_close": 22400.0,
            "change_pts": 100.0,
            "source": "yfinance",
        }
        msg = mb.format_message(us, futures)
        self.assertIn("S&P +0.88%", msg)
        self.assertIn("NASDAQ +1.12%", msg)
        self.assertIn("VIX 15.50", msg)
        self.assertIn("台指期：+100.0 點（22500.00）", msg)

    def test_message_with_none_values(self):
        us = {
            "S&P 500": {"last": None, "prev_close": None, "change_pct": None},
            "NASDAQ": {"last": None, "prev_close": None, "change_pct": None},
            "費半": {"last": None, "prev_close": None, "change_pct": None},
            "VIX": {"last": None, "prev_close": None, "change_pct": None},
        }
        futures = {"label": "台指期", "last": None, "prev_close": None, "change_pts": None, "source": "none"}
        msg = mb.format_message(us, futures)
        self.assertIn("N/A", msg)


class TestVIXDetection(unittest.TestCase):
    """Test VIX level detection in highlights."""

    def _us_with_vix(self, vix_value):
        return {
            "S&P 500": {"last": 5700, "prev_close": 5700, "change_pct": 0.0},
            "NASDAQ": {"last": 18000, "prev_close": 18000, "change_pct": 0.0},
            "費半": {"last": 5500, "prev_close": 5500, "change_pct": 0.0},
            "VIX": {"last": vix_value, "prev_close": 15.0, "change_pct": 0.0},
        }

    def test_vix_panic(self):
        us = self._us_with_vix(30.0)
        fut = {"change_pts": 0}
        highlights = mb._generate_highlights(us, fut)
        self.assertIn("VIX 飆升至 30.0，市場恐慌", highlights)

    def test_vix_elevated(self):
        us = self._us_with_vix(22.0)
        fut = {"change_pts": 0}
        highlights = mb._generate_highlights(us, fut)
        self.assertIn("VIX 22.0，避險情緒升溫", highlights)

    def test_vix_optimistic(self):
        us = self._us_with_vix(11.0)
        fut = {"change_pts": 0}
        highlights = mb._generate_highlights(us, fut)
        self.assertIn("VIX 11.0，市場偏樂觀", highlights)

    def test_vix_normal_range_no_mention(self):
        us = self._us_with_vix(16.5)
        fut = {"change_pts": 0}
        highlights = mb._generate_highlights(us, fut)
        # Should not contain VIX mention in normal range
        self.assertNotIn("VIX", highlights)


class TestSPXHighlights(unittest.TestCase):
    """Test S&P 500 trend detection in highlights."""

    def _make_us(self, spx_pct):
        return {
            "S&P 500": {"last": 5700, "prev_close": 5600, "change_pct": spx_pct},
            "NASDAQ": {"last": 18000, "prev_close": 17800, "change_pct": 1.0},
            "費半": {"last": 5500, "prev_close": 5450, "change_pct": 0.9},
            "VIX": {"last": 15.0, "prev_close": 16.0, "change_pct": -6.0},
        }

    def test_spx_big_rise(self):
        us = self._make_us(2.0)
        fut = {"change_pts": 50}
        hl = mb._generate_highlights(us, fut)
        self.assertIn("美股大漲，多方強勢", hl)

    def test_spx_moderate_rise(self):
        us = self._make_us(0.8)
        fut = {"change_pts": 50}
        hl = mb._generate_highlights(us, fut)
        self.assertIn("美股偏多，穩步走高", hl)

    def test_spx_flat(self):
        us = self._make_us(-0.3)
        fut = {"change_pts": 0}
        hl = mb._generate_highlights(us, fut)
        self.assertIn("美股平盤震盪，觀望", hl)

    def test_spx_pullback(self):
        us = self._make_us(-1.0)
        fut = {"change_pts": -50}
        hl = mb._generate_highlights(us, fut)
        self.assertIn("美股拉回，注意支撐", hl)

    def test_spx_crash(self):
        us = self._make_us(-3.0)
        fut = {"change_pts": -150}
        hl = mb._generate_highlights(us, fut)
        self.assertIn("美股大跌，留意風險", hl)


class TestFetchUSIndices(unittest.TestCase):
    """Test fetch_us_indices with mocked yfinance."""

    @patch("morning_brief.yf.Ticker")
    def test_normal_fetch(self, mock_ticker_cls):
        """Normal 2-day history returns correct change_pct."""
        hist = pd.DataFrame(
            {"Close": [5600.0, 5700.0]},
            index=pd.date_range("2026-09-17", periods=2),
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        mock_ticker_cls.return_value = mock_ticker

        result = mb.fetch_us_indices()

        self.assertAlmostEqual(result["S&P 500"]["change_pct"], 1.7857, places=2)
        self.assertAlmostEqual(result["NASDAQ"]["change_pct"], 1.7857, places=2)
        self.assertAlmostEqual(result["費半"]["change_pct"], 1.7857, places=2)
        self.assertAlmostEqual(result["VIX"]["change_pct"], 1.7857, places=2)

    @patch("morning_brief.yf.Ticker")
    def test_empty_history(self, mock_ticker_cls):
        """Empty history yields None values."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        result = mb.fetch_us_indices()
        for label, data in result.items():
            self.assertIsNone(data["last"])
            self.assertIsNone(data["change_pct"])

    @patch("morning_brief.yf.Ticker")
    def test_exception_handling(self, mock_ticker_cls):
        """Exception during fetch yields None values."""
        mock_ticker_cls.side_effect = Exception("network error")

        result = mb.fetch_us_indices()
        for label, data in result.items():
            self.assertIsNone(data["last"])


class TestFuturesFallbackChain(unittest.TestCase):
    """Test the yfinance → TAIFEX fallback chain for TW futures."""

    @patch("morning_brief.yf.Ticker")
    def test_yfinance_tx_success(self, mock_ticker_cls):
        """TX=F yfinance succeeds — returns immediately without fallback."""
        hist = pd.DataFrame(
            {"Close": [22400.0, 22500.0]},
            index=pd.date_range("2026-09-17", periods=2),
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        mock_ticker_cls.return_value = mock_ticker

        result = mb.fetch_twse_futures()

        self.assertEqual(result["source"], "yfinance")
        self.assertEqual(result["symbol"], "TX=F")
        self.assertEqual(result["last"], 22500.0)
        self.assertEqual(result["change_pts"], 100.0)

    @patch("morning_brief.yf.Ticker")
    def test_yfinance_wtx_fallback(self, mock_ticker_cls):
        """TX=F empty, WTX=F succeeds."""
        def side_effect(symbol):
            mock_ticker = MagicMock()
            if symbol == "TX=F":
                mock_ticker.history.return_value = pd.DataFrame()
            else:
                hist = pd.DataFrame(
                    {"Close": [22300.0, 22400.0]},
                    index=pd.date_range("2026-09-17", periods=2),
                )
                mock_ticker.history.return_value = hist
            return mock_ticker

        mock_ticker_cls.side_effect = side_effect

        result = mb.fetch_twse_futures()

        self.assertEqual(result["source"], "yfinance")
        self.assertEqual(result["symbol"], "WTX=F")

    @patch("morning_brief.requests.get")
    @patch("morning_brief.yf.Ticker")
    def test_taifex_fallback(self, mock_ticker_cls, mock_get):
        """Both yfinance calls fail → fall back to TAIFEX."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "TX,2026/09,22500,22600,22400,22550,100000\n"
            "TX,2026/08,22000,22100,21900,22050,90000\n"
        )
        mock_get.return_value = mock_resp

        result = mb.fetch_twse_futures()

        self.assertEqual(result["source"], "taifex")
        self.assertEqual(result["last"], 22550.0)

    @patch("morning_brief.requests.get")
    @patch("morning_brief.yf.Ticker")
    def test_all_sources_fail(self, mock_ticker_cls, mock_get):
        """All sources fail — returns source='none' with None values."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        result = mb.fetch_twse_futures()

        self.assertEqual(result["source"], "none")
        self.assertIsNone(result["last"])


class TestSendTelegram(unittest.TestCase):
    """Test send_telegram_message with mocked HTTP session."""

    @patch("morning_brief._get_tg_session")
    def test_send_success(self, mock_session_fn):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.post.return_value = mock_resp
        mock_session_fn.return_value = mock_session

        result = mb.send_telegram_message("test message")
        self.assertTrue(result)
        mock_session.post.assert_called_once()

    @patch("morning_brief._get_tg_session")
    def test_send_failure_status(self, mock_session_fn):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Too Many Requests"
        mock_session.post.return_value = mock_resp
        mock_session_fn.return_value = mock_session

        result = mb.send_telegram_message("test")
        self.assertFalse(result)

    @patch("morning_brief._get_tg_session")
    def test_send_exception(self, mock_session_fn):
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("connection refused")
        mock_session_fn.return_value = mock_session

        result = mb.send_telegram_message("test")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
