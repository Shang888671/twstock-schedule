"""Unit tests for daily_update.py — retry decorator, skip-if-updated logic, logging."""

import io
import logging
import sys
import time
import unittest
from unittest.mock import patch, MagicMock
from datetime import date

import pandas as pd

# Ensure stock-model is in path
sys.path.insert(0, r"C:\Users\Ryzen USER\Documents\ClaudeMemory\stock-model")

import daily_update as du


class TestRetryDecorator(unittest.TestCase):
    """Test retry decorator with simulated failures."""

    @patch("daily_update.time.sleep")
    def test_succeeds_first_attempt(self, mock_sleep):
        """No failure — function returns immediately without retry."""
        call_count = {"n": 0}

        @du.retry(max_attempts=3, backoff_factor=2)
        def always_ok():
            call_count["n"] += 1
            return "success"

        result = always_ok()
        self.assertEqual(result, "success")
        self.assertEqual(call_count["n"], 1)
        mock_sleep.assert_not_called()

    @patch("daily_update.time.sleep")
    def test_succeeds_after_retries(self, mock_sleep):
        """Fails twice, succeeds on third attempt."""
        call_count = {"n": 0}

        @du.retry(max_attempts=3, backoff_factor=2)
        def eventually_ok():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ValueError(f"fail #{call_count['n']}")
            return "ok"

        result = eventually_ok()
        self.assertEqual(result, "ok")
        self.assertEqual(call_count["n"], 3)
        # Slept 2s then 4s (backoff_factor ** attempt)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

    @patch("daily_update.time.sleep")
    def test_all_attempts_fail(self, mock_sleep):
        """All attempts fail — exception propagates after last attempt."""

        @du.retry(max_attempts=3, backoff_factor=2)
        def always_fail():
            raise RuntimeError("persistent error")

        with self.assertRaises(RuntimeError) as ctx:
            always_fail()
        self.assertEqual(str(ctx.exception), "persistent error")
        self.assertEqual(mock_sleep.call_count, 2)  # slept between attempts 1→2 and 2→3

    @patch("daily_update.time.sleep")
    def test_backoff_timing(self, mock_sleep):
        """Verify exponential backoff: 2^1=2, 2^2=4."""
        attempts = {"n": 0}

        @du.retry(max_attempts=3, backoff_factor=2)
        def failing():
            attempts["n"] += 1
            raise ValueError("no")

        with self.assertRaises(ValueError):
            failing()
        # First retry: backoff_factor^1 = 2, second retry: backoff_factor^2 = 4
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

    @patch("daily_update.time.sleep")
    def test_exception_filtering(self, mock_sleep):
        """Decorator only catches specified exception types."""

        @du.retry(max_attempts=3, backoff_factor=2, exceptions=(ValueError,))
        def raise_type_error():
            raise TypeError("not a ValueError")

        # TypeError should propagate immediately without retries
        with self.assertRaises(TypeError):
            raise_type_error()
        mock_sleep.assert_not_called()


class TestRetryLogging(unittest.TestCase):
    """Test that retry decorator logs appropriately."""

    @patch("daily_update.time.sleep")
    def test_warning_logged_on_retry(self, mock_sleep):
        """Each failed attempt (except last) logs a warning."""
        logger_name = "daily_update"

        call_count = {"n": 0}

        @du.retry(max_attempts=3, backoff_factor=2)
        def failing_func():
            call_count["n"] += 1
            raise RuntimeError("boom")

        with self.assertLogs(logger_name, level="WARNING") as cm:
            with self.assertRaises(RuntimeError):
                failing_func()

        # Check warning messages mention retry
        self.assertTrue(any("attempt 1/3 failed" in msg for msg in cm.output))
        self.assertTrue(any("attempt 2/3 failed" in msg for msg in cm.output))

    @patch("daily_update.time.sleep")
    def test_error_logged_after_final_attempt(self, mock_sleep):
        """After all attempts fail, an error is logged."""
        logger_name = "daily_update"

        @du.retry(max_attempts=3, backoff_factor=2)
        def always_fail():
            raise RuntimeError("final boom")

        with self.assertLogs(logger_name, level="ERROR") as cm:
            with self.assertRaises(RuntimeError):
                always_fail()

        self.assertTrue(any("failed after 3 attempts" in msg for msg in cm.output))


class TestIsTodayInDB(unittest.TestCase):
    """Test is_today_in_db — skip-if-already-updated logic."""

    @patch("daily_update._get_conn")
    def test_today_exists_returns_true(self, mock_conn_fn):
        """Today's date exists in institutional_flow → True."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (1,)
        mock_conn_fn.return_value = mock_conn

        result = du.is_today_in_db()
        self.assertTrue(result)

    @patch("daily_update._get_conn")
    def test_today_missing_returns_false(self, mock_conn_fn):
        """No row for today → False."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_conn_fn.return_value = mock_conn

        result = du.is_today_in_db()
        self.assertFalse(result)

    @patch("daily_update._get_conn")
    def test_exception_returns_false(self, mock_conn_fn):
        """Database exception → False (safe fallback)."""
        mock_conn_fn.side_effect = Exception("DB broken")

        result = du.is_today_in_db()
        self.assertFalse(result)


class TestUpdateInstitutional(unittest.TestCase):
    """Test update_institutional with mocked dependencies."""

    @patch("daily_update.chip_data.get_institutional_flow_multi")
    @patch("daily_update._get_conn")
    def test_institutional_success(self, mock_conn_fn, mock_fetch):
        """Normal update flow succeeds."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("2330",), ("2317",)]
        mock_conn_fn.return_value = mock_conn

        mock_fetch.return_value = {
            "2330": pd.DataFrame({"foreign_net": [100]}),
            "2317": pd.DataFrame({"foreign_net": [200]}),
        }

        # Should not raise
        du.update_institutional(target_date="2026-09-19", codes=["2330", "2317"])
        mock_fetch.assert_called_once()


class TestUpdateMargin(unittest.TestCase):
    """Test update_margin with mocked dependencies."""

    @patch("daily_update.upsert_margin")
    @patch("daily_update.margin_data.get_margin_trading_multi")
    @patch("daily_update._get_conn")
    def test_margin_success(self, mock_conn_fn, mock_fetch, mock_upsert):
        """Normal margin update succeeds."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("2330",)]
        mock_conn_fn.return_value = mock_conn

        mock_fetch.return_value = {
            "2330": pd.DataFrame({"margin_balance": [500], "short_balance": [30]}),
        }

        du.update_margin(target_date="2026-09-19", codes=["2330"])
        mock_fetch.assert_called_once()


class TestMainSkipLogic(unittest.TestCase):
    """Test main() with skip-if-already-updated."""

    @patch("daily_update.update_margin")
    @patch("daily_update.update_institutional")
    @patch("daily_update.is_today_in_db")
    def test_skip_when_already_updated(self, mock_is_in_db, mock_update_inst, mock_update_margin):
        """main() should skip updates if today is already in DB."""
        mock_is_in_db.return_value = True

        du.main()

        mock_update_inst.assert_not_called()
        mock_update_margin.assert_not_called()

    @patch("daily_update.update_margin")
    @patch("daily_update.update_institutional")
    @patch("daily_update.is_today_in_db")
    def test_proceed_when_not_updated(self, mock_is_in_db, mock_update_inst, mock_update_margin):
        """main() should proceed with updates if today is not in DB."""
        mock_is_in_db.return_value = False

        du.main()

        mock_update_inst.assert_called_once()
        mock_update_margin.assert_called_once()


if __name__ == "__main__":
    unittest.main()
