"""Shared fixtures for stock-model test suite."""

import pytest
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
import time


@pytest.fixture
def tmp_db_path(tmp_path):
    """Create a temporary database path for testing."""
    return tmp_path / "stock.db"


@pytest.fixture
def sample_institutional_df():
    """Sample institutional flow DataFrame."""
    dates = pd.date_range("2026-09-01", periods=5, freq="B")
    return pd.DataFrame({
        "foreign_net": [1000, 2000, -500, 3000, 1500],
        "trust_net": [500, -200, 800, 1000, -300],
        "dealer_net": [200, 300, -100, 400, 500],
        "total_net": [1700, 2100, 200, 4400, 1700],
    }, index=dates)


@pytest.fixture
def sample_ohlcv_df():
    """Sample OHLCV DataFrame for history cache."""
    dates = pd.date_range("2026-09-01", periods=5, freq="B")
    return pd.DataFrame({
        "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "High": [105.0, 106.0, 107.0, 108.0, 109.0],
        "Low": [95.0, 96.0, 97.0, 98.0, 99.0],
        "Close": [102.0, 103.0, 104.0, 105.0, 106.0],
        "Volume": [1000000, 1100000, 1200000, 1300000, 1400000],
    }, index=dates)


@pytest.fixture
def sample_warrant_df():
    """Sample warrant flow DataFrame."""
    dates = pd.date_range("2026-09-01", periods=3, freq="B")
    return pd.DataFrame({
        "warrant_call_volume": [500000.0, 600000.0, 700000.0],
        "warrant_put_volume": [300000.0, 400000.0, 350000.0],
        "warrant_call_value": [5000000.0, 6000000.0, 7000000.0],
        "warrant_put_value": [3000000.0, 4000000.0, 3500000.0],
        "warrant_vol_pc_ratio": [60.0, 66.67, 50.0],
    }, index=dates)


@pytest.fixture
def mock_response():
    """Factory for mock requests responses."""
    def _make_response(json_data=None, status_code=200, text=""):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.status_code = status_code
        resp.text = text
        return resp
    return _make_response


@pytest.fixture
def mock_session():
    """Create a mock requests session."""
    return MagicMock()
