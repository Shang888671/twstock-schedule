"""Tests for fetch_data.py — stock data fetching module."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import pandas as pd
from io import StringIO


@pytest.fixture(autouse=True)
def setup_fetch_env(tmp_path):
    """Setup temp cache directory for fetch tests."""
    with patch("fetch_data.CACHE_DIR", tmp_path):
        # Reset module-level singletons
        import fetch_data
        fetch_data._session = None
        fetch_data._mis_session = None
        fetch_data._mis_session_primed = False
        yield


class TestIsListed:
    """Tests for _is_listed()."""

    def test_4_digit_code_is_listed(self):
        """4-digit numeric code is listed."""
        from fetch_data import _is_listed
        assert _is_listed("2330") is True

    def test_5_digit_code_is_not_listed(self):
        """5-digit code is not listed."""
        from fetch_data import _is_listed
        assert _is_listed("12345") is False

    def test_alpha_code_is_not_listed(self):
        """Alphabetic code is not listed."""
        from fetch_data import _is_listed
        assert _is_listed("ABC") is False

    def test_empty_code_is_not_listed(self):
        """Empty code is not listed."""
        from fetch_data import _is_listed
        assert _is_listed("") is False

    def test_whitespace_stripped(self):
        """Code with whitespace is stripped."""
        from fetch_data import _is_listed
        assert _is_listed(" 2330 ") is True


class TestToYfSymbol:
    """Tests for to_yf_symbol()."""

    def test_listed_code_gets_tw_suffix(self):
        """Listed code gets .TW suffix."""
        from fetch_data import to_yf_symbol
        assert to_yf_symbol("2330") == "2330.TW"

    def test_otc_code_gets_two_suffix(self):
        """OTC code gets .TWO suffix."""
        from fetch_data import to_yf_symbol
        assert to_yf_symbol("6488", otc=True) == "6488.TWO"

    def test_existing_suffix_unchanged(self):
        """Code with existing suffix is unchanged."""
        from fetch_data import to_yf_symbol
        assert to_yf_symbol("2330.TW") == "2330.TW"
        assert to_yf_symbol("^TWII") == "^TWII"


class TestParseFloat:
    """Tests for _parse_float()."""

    def test_valid_float(self):
        """Valid string returns float."""
        from fetch_data import _parse_float
        assert _parse_float({"v": "123.45"}, "v") == 123.45

    def test_none_value(self):
        """None value returns None."""
        from fetch_data import _parse_float
        assert _parse_float({"v": None}, "v") is None

    def test_dash_value(self):
        """Dash value returns None."""
        from fetch_data import _parse_float
        assert _parse_float({"v": "-"}, "v") is None

    def test_empty_string(self):
        """Empty string returns None."""
        from fetch_data import _parse_float
        assert _parse_float({"v": ""}, "v") is None

    def test_missing_key(self):
        """Missing key returns None."""
        from fetch_data import _parse_float
        assert _parse_float({}, "v") is None

    def test_invalid_value(self):
        """Invalid value returns None."""
        from fetch_data import _parse_float
        assert _parse_float({"v": "abc"}, "v") is None


class TestGetQuote:
    """Tests for get_quote() — real-time quote fetching."""

    def test_listed_quote_from_twse_mis(self, setup_fetch_env):
        """Listed stock quote from TWSE MIS API."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "msgArray": [{
                "z": "2460.0",
                "y": "2450.0",
                "h": "2480.0",
                "l": "2440.0",
                "v": "5000",
            }]
        }
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("fetch_data._get_mis_session", return_value=mock_session):
            from fetch_data import get_quote
            result = get_quote("2330")

        assert result["symbol"] == "2330.TW"
        assert result["last_price"] == 2460.0
        assert result["previous_close"] == 2450.0
        assert result["source"] == "twse_mis"

    def test_otc_quote_from_tpex_mis(self, setup_fetch_env):
        """OTC stock quote from TPEX MIS API."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "msgArray": [{
                "z": "150.0",
                "y": "148.0",
                "h": "152.0",
                "l": "147.0",
                "v": "2000",
            }]
        }
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("fetch_data._get_mis_session", return_value=mock_session):
            from fetch_data import get_quote
            result = get_quote("6488", otc=True)

        assert result["symbol"] == "6488.TWO"
        assert result["last_price"] == 150.0
        assert result["source"] == "tpex_mis"

    def test_twse_mis_fallback_to_yahoo(self, setup_fetch_env):
        """TWSE MIS failure falls back to Yahoo Finance."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"msgArray": []}  # Empty response
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        mock_ticker = MagicMock()
        mock_ticker.fast_info = {
            "lastPrice": 2460.0,
            "previousClose": 2450.0,
            "dayHigh": 2480.0,
            "dayLow": 2440.0,
            "lastVolume": 5000000,
        }

        with patch("fetch_data._get_mis_session", return_value=mock_session), \
             patch("fetch_data.yf.Ticker", return_value=mock_ticker):
            from fetch_data import get_quote
            result = get_quote("2330")

        assert result["source"] == "yahoo_delayed"
        assert result["last_price"] == 2460.0

    def test_yahoo_finance_failure(self, setup_fetch_env):
        """Both TWSE MIS and Yahoo fail — return failed dict."""
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Connection error")

        with patch("fetch_data._get_mis_session", return_value=mock_session), \
             patch("fetch_data.yf.Ticker", side_effect=Exception("YF error")):
            from fetch_data import get_quote
            result = get_quote("2330")

        assert result["source"] == "failed"
        assert result["symbol"] == "2330.TW"

    def test_twse_mis_none_last_price_fallback(self, setup_fetch_env):
        """If TWSE MIS returns None for last_price, try trade.z."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "msgArray": [{
                "z": None,
                "trade": {"z": "2460.0"},
                "y": "2450.0",
                "h": "2480.0",
                "l": "2440.0",
                "v": "5000",
            }]
        }
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("fetch_data._get_mis_session", return_value=mock_session):
            from fetch_data import get_quote
            result = get_quote("2330")

        assert result["last_price"] == 2460.0


class TestGetHistory:
    """Tests for get_history() — historical OHLCV data."""

    def test_cache_hit_returns_cached(self, setup_fetch_env):
        """When cache hit, return from SQLite without calling yfinance."""
        mock_df = pd.DataFrame({
            "Open": [100.0], "High": [105.0], "Low": [95.0],
            "Close": [102.0], "Volume": [1000000]
        }, index=pd.date_range("2026-09-01", periods=1))

        with patch("database.init_db"), \
             patch("database.get_history", return_value=mock_df), \
             patch("database.upsert_history") as mock_upsert, \
             patch("fetch_data.yf.Ticker") as mock_yf:
            from fetch_data import get_history
            result = get_history("2330", period="1y")

        assert not result.empty
        mock_yf.assert_not_called()
        mock_upsert.assert_not_called()

    def test_cache_miss_calls_yfinance(self, setup_fetch_env):
        """When cache miss, call yfinance and write to cache."""
        dates = pd.date_range("2026-01-01", periods=3, freq="B")
        mock_yf_df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
            "Volume": [1000000, 1100000, 1200000],
        }, index=dates)
        mock_yf_df.index.name = "date"

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_yf_df

        with patch("database.init_db"), \
             patch("database.get_history", return_value=None), \
             patch("database.upsert_history") as mock_upsert, \
             patch("fetch_data.yf.Ticker", return_value=mock_ticker):
            from fetch_data import get_history
            result = get_history("2330", period="1y")

        assert len(result) == 3
        mock_upsert.assert_called_once()

    def test_no_cache_when_start_end_specified(self, setup_fetch_env):
        """When start/end specified, skip cache."""
        dates = pd.date_range("2026-09-01", periods=3, freq="B")
        mock_yf_df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
            "Volume": [1000000, 1100000, 1200000],
        }, index=dates)
        mock_yf_df.index.name = "date"

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_yf_df

        with patch("database.init_db"), \
             patch("database.get_history") as mock_get_cache, \
             patch("database.upsert_history") as mock_upsert, \
             patch("fetch_data.yf.Ticker", return_value=mock_ticker):
            from fetch_data import get_history
            result = get_history("2330", start="2026-09-01", end="2026-09-03")

        mock_get_cache.assert_not_called()
        mock_upsert.assert_not_called()

    def test_empty_yfinance_result(self, setup_fetch_env):
        """yfinance returns empty DataFrame."""
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        empty_df.index.name = "date"

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = empty_df

        with patch("database.init_db"), \
             patch("database.get_history", return_value=None), \
             patch("database.upsert_history") as mock_upsert, \
             patch("fetch_data.yf.Ticker", return_value=mock_ticker):
            from fetch_data import get_history
            result = get_history("2330", period="1y")

        assert result.empty
        mock_upsert.assert_not_called()


class TestGetChineseName:
    """Tests for get_chinese_name()."""

    def test_name_found_in_table(self, setup_fetch_env):
        """Find name for existing code."""
        mock_df = pd.DataFrame({"code": ["2330"], "name": ["台積電"]})

        with patch("fetch_data._load_isin_name_table", return_value=mock_df):
            from fetch_data import get_chinese_name
            result = get_chinese_name("2330")

        assert result == "台積電"

    def test_name_not_found(self, setup_fetch_env):
        """Return None for unknown code."""
        mock_df = pd.DataFrame({"code": ["2330"], "name": ["台積電"]})

        with patch("fetch_data._load_isin_name_table", return_value=mock_df):
            from fetch_data import get_chinese_name
            result = get_chinese_name("9999")

        assert result is None

    def test_otc_name_lookup(self, setup_fetch_env):
        """Lookup OTC name."""
        mock_df = pd.DataFrame({"code": ["6488"], "name": ["環球晶"]})

        with patch("fetch_data._load_isin_name_table", return_value=mock_df):
            from fetch_data import get_chinese_name
            result = get_chinese_name("6488", otc=True)

        assert result == "環球晶"


class TestGetSession:
    """Tests for _get_session() singleton."""

    def test_session_reused(self):
        """Session is created only once and reused."""
        import fetch_data
        fetch_data._session = None

        session1 = fetch_data._get_session()
        session2 = fetch_data._get_session()

        assert session1 is session2

    def test_session_has_retry_configured(self):
        """Session should have retry adapter mounted."""
        import fetch_data
        fetch_data._session = None

        session = fetch_data._get_session()
        assert "https://" in session.adapters
