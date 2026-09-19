"""Tests for chip_data.py — institutional/warrant chip data fetching."""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import time


@pytest.fixture(autouse=True)
def setup_chip_env(tmp_path):
    """Setup temp environment for chip data tests."""
    db_path = tmp_path / "stock.db"
    data_dir = tmp_path
    data_dir.mkdir(exist_ok=True)
    with patch("chip_data.CACHE_DIR", tmp_path), \
         patch("database.DATA_DIR", data_dir), \
         patch("database.DB_PATH", db_path):
        import chip_data
        chip_data._session = None
        # Initialize DB tables in the test DB
        from database import init_db
        init_db()
        yield


class TestToInt:
    """Tests for _to_int() helper."""

    def test_valid_integer(self):
        """Valid integer string returns int."""
        from chip_data import _to_int
        assert _to_int("12345") == 12345

    def test_comma_separated(self):
        """Comma-separated number."""
        from chip_data import _to_int
        assert _to_int("1,234,567") == 1234567

    def test_empty_string(self):
        """Empty string returns 0."""
        from chip_data import _to_int
        assert _to_int("") == 0

    def test_dash_string(self):
        """Dash returns 0."""
        from chip_data import _to_int
        assert _to_int("--") == 0

    def test_none_value(self):
        """None value returns 0."""
        from chip_data import _to_int
        assert _to_int(None) == 0


class TestParsePriceDirection:
    """Tests for _parse_price_direction()."""

    def test_red_color_is_up(self):
        """color:red means price went up."""
        from chip_data import _parse_price_direction
        assert _parse_price_direction('<span style="color:red">+5</span>') is True

    def test_green_color_is_down(self):
        """color:green means price went down."""
        from chip_data import _parse_price_direction
        assert _parse_price_direction('<span style="color:green">-3</span>') is False

    def test_no_color_ambiguous(self):
        """No color means None."""
        from chip_data import _parse_price_direction
        assert _parse_price_direction("+5") is None


class TestIsListed:
    """Tests for _is_listed()."""

    def test_4_digit_is_listed(self):
        """4-digit code is listed."""
        from chip_data import _is_listed
        assert _is_listed("2330") is True

    def test_non_4_digit_is_otc(self):
        """Non-4-digit code is OTC."""
        from chip_data import _is_listed
        assert _is_listed("6488") is True  # Still 4-digit

    def test_5_digit_is_otc(self):
        """5-digit code is OTC."""
        from chip_data import _is_listed
        assert _is_listed("12345") is False


class TestGetInstitutionalFlow:
    """Tests for get_institutional_flow()."""

    def test_cache_hit_returns_cached(self, setup_chip_env):
        """When data is fully cached, return from SQLite."""
        mock_df = pd.DataFrame({
            "foreign_net": [1000, 2000],
            "trust_net": [500, 600],
            "dealer_net": [200, 300],
            "total_net": [1700, 2900],
        }, index=pd.date_range("2026-09-01", periods=2, freq="B"))
        mock_df.index.name = "date"

        with patch("chip_data.db_get_institutional", return_value=mock_df), \
             patch("chip_data._get_session") as mock_session:
            from chip_data import get_institutional_flow
            result = get_institutional_flow("2330", "2026-09-01", "2026-09-02")

        assert len(result) == 2
        mock_session.assert_not_called()

    def test_cache_miss_fetches_from_twse(self, setup_chip_env):
        """When data missing, fetch from TWSE T86 API."""
        empty_df = pd.DataFrame()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "stat": "OK",
            "data": [
                ["2330", "台積電", "1000", "500", "200", "300", "400", "500",
                 "1500", "1200", "800", "600", "200", "300", "100", "50", "25",
                 "75", "1700"],
            ]
        }
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        def mock_db_get(code, start, end):
            return pd.DataFrame({
                "foreign_net": [2500],
                "trust_net": [800],
                "dealer_net": [600],
                "total_net": [3900],
            }, index=pd.to_datetime(["2026-09-01"]))
        mock_df_result = mock_db_get("2330", "2026-09-01", "2026-09-02")

        with patch("chip_data.db_get_institutional", side_effect=[
            empty_df,  # First call: cache miss
            mock_df_result,  # Second call after upsert
        ]), patch("chip_data._get_session", return_value=mock_session), \
             patch("chip_data.upsert_institutional") as mock_upsert, \
             patch("chip_data.time.sleep"):
            from chip_data import get_institutional_flow
            result = get_institutional_flow("2330", "2026-09-01", "2026-09-02")

        mock_upsert.assert_called_once()
        assert not result.empty

    def test_network_error_handled(self, setup_chip_env):
        """Network errors are caught and logged."""
        empty_df = pd.DataFrame()

        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Connection error")

        with patch("chip_data.db_get_institutional", return_value=empty_df), \
             patch("chip_data._get_session", return_value=mock_session), \
             patch("chip_data.time.sleep"):
            from chip_data import get_institutional_flow
            # Should not raise
            result = get_institutional_flow("2330", "2026-09-01", "2026-09-02")

        assert result.empty

    def test_otc_uses_tpex_endpoint(self, setup_chip_env):
        """OTC stock uses TPEX endpoint."""
        empty_df = pd.DataFrame()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [{
            "SecuritiesCompanyCode": "6488",
            "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "1000",
            "SecuritiesInvestmentTrustCompanies-Difference": "500",
            "Dealers-Difference": "200",
        }]
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        mock_df_result = pd.DataFrame({
            "foreign_net": [1000],
            "trust_net": [500],
            "dealer_net": [200],
            "total_net": [1700],
        }, index=pd.to_datetime(["2026-09-01"]))

        with patch("chip_data.db_get_institutional", side_effect=[
            empty_df, mock_df_result
        ]), patch("chip_data._get_session", return_value=mock_session), \
             patch("chip_data.upsert_institutional"), \
             patch("chip_data._is_listed", return_value=False), \
             patch("chip_data.time.sleep"):
            from chip_data import get_institutional_flow
            result = get_institutional_flow("6488", "2026-09-01", "2026-09-02")

        # Verify TPEX endpoint was called
        call_args = mock_session.get.call_args
        assert "tpex" in str(call_args).lower() or "3insti" in str(call_args).lower()

    def test_stat_not_ok_returns_empty(self, setup_chip_env):
        """API response with stat != OK returns empty."""
        empty_df = pd.DataFrame()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stat": "查無資料"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("chip_data.db_get_institutional", return_value=empty_df), \
             patch("chip_data._get_session", return_value=mock_session), \
             patch("chip_data.time.sleep"):
            from chip_data import get_institutional_flow
            result = get_institutional_flow("2330", "2026-09-01", "2026-09-02")

        assert result.empty

    def test_partial_cache_miss_fetches_only_missing(self, setup_chip_env):
        """Only missing dates are fetched."""
        partial_df = pd.DataFrame({
            "foreign_net": [1000],
            "trust_net": [500],
            "dealer_net": [200],
            "total_net": [1700],
        }, index=pd.to_datetime(["2026-09-01"]))
        partial_df.index.name = "date"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "stat": "OK",
            "data": [
                ["2330", "台積電", "2000", "600", "300", "400", "500", "600",
                 "1800", "1400", "900", "700", "300", "400", "200", "100", "50",
                 "75", "2900"],
            ]
        }
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        full_df = pd.DataFrame({
            "foreign_net": [1000, 2500],
            "trust_net": [500, 900],
            "dealer_net": [200, 600],
            "total_net": [1700, 4000],
        }, index=pd.to_datetime(["2026-09-01", "2026-09-02"]))
        full_df.index.name = "date"

        with patch("chip_data.db_get_institutional", side_effect=[
            partial_df,  # First call: has one date cached
            full_df,  # Second call after upsert
        ]), patch("chip_data._get_session", return_value=mock_session), \
             patch("chip_data.upsert_institutional") as mock_upsert, \
             patch("chip_data.time.sleep"):
            from chip_data import get_institutional_flow
            result = get_institutional_flow("2330", "2026-09-01", "2026-09-02")

        assert len(result) == 2
        mock_upsert.assert_called_once()


class TestGetWarrantFlow:
    """Tests for get_warrant_flow()."""



    def test_fetch_warrant_table_stat_not_ok(self, setup_chip_env):
        """_fetch_warrant_table with stat != OK returns empty."""
        from chip_data import _fetch_warrant_table

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"stat": "查無資料"}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("chip_data._get_session", return_value=mock_session):
            result = _fetch_warrant_table("20260901", put=False)

        assert result.empty

    def test_fetch_warrant_table_exception(self, setup_chip_env):
        """_fetch_warrant_table handles exception."""
        from chip_data import _fetch_warrant_table

        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Timeout")

        with patch("chip_data._get_session", return_value=mock_session):
            result = _fetch_warrant_table("20260901", put=False)

        assert result.empty

    def test_warrant_flow_empty_result(self, setup_chip_env):
        """When both call and put DataFrames are empty."""
        empty_df = pd.DataFrame()

        def mock_fetch(date_str, put):
            return pd.DataFrame()

        with patch("chip_data.db_get_warrant_flow", side_effect=[
            empty_df, empty_df
        ]), patch("chip_data._fetch_warrant_table", side_effect=mock_fetch), \
             patch("chip_data.time.sleep"):
            from chip_data import get_warrant_flow
            result = get_warrant_flow("2330", "2026-09-01", "2026-09-02")

        assert result.empty


class TestGetSession:
    """Tests for _get_session() singleton."""

    def test_session_reused(self, setup_chip_env):
        """Session is created only once."""
        import chip_data
        chip_data._session = None

        session1 = chip_data._get_session()
        session2 = chip_data._get_session()

        assert session1 is session2
