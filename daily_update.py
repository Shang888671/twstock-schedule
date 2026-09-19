"""每日自動更新腳本 — 盤後自動把當天三大法人/融資融券匯入 SQLite。

使用方式：
1. 手動跑：python daily_update.py
2. 設定 cron（Windows 排程 / Linux crontab）盤後 15:30 執行
   - 有開盤（日期在 data_cache 裡）→ 更新
   - 沒開市（週末/假日）→ 跳過

流程：
1. 檢查今天是否交易日（用 institutional_flow 表裡今天有沒有資料）
2. 是 → 抓三大法人 + 融資融券 → upsert 進 SQLite
3. 否 → 跳過
"""

import sys
import time
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from functools import wraps

import pandas as pd

# 加入 project root 到 path
sys.path.insert(0, str(Path(__file__).parent))

import chip_data
import margin_data
from database import get_institutional_flow, upsert_institutional, get_margin, upsert_margin, _get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    backoff_factor: int = 2,
    exceptions: tuple[type[Exception], ...] = (Exception,)
):
    """Retry decorator with exponential backoff.

    Matches urllib3 Retry(total=3, backoff_factor=2) behavior:
      wait = backoff_factor ** attempt  →  2s, 4s between retries
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    wait = backoff_factor ** attempt
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {wait}s..."
                    )
                    time.sleep(wait)
            raise last_exception  # unreachable, but keeps type-checker happy
        return wrapper
    return decorator


def is_today_in_db() -> bool:
    """今天是否已在資料庫（表示已更新過）。"""
    conn = None
    try:
        conn = _get_conn()
        today = date.today().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT 1 FROM institutional_flow WHERE date = ? LIMIT 1", (today,)
        ).fetchone()
        return row is not None
    except Exception as e:
        logger.error(f"查詢資料庫失敗：{e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@retry(max_attempts=3, backoff_factor=2)
def update_institutional(target_date: Optional[str] = None, codes: Optional[list[str]] = None) -> None:
    """更新三大法人。"""
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")

    if codes is None:
        conn = None
        try:
            conn = _get_conn()
            codes = [r[0] for r in conn.execute("SELECT code FROM code_market").fetchall()]
        except Exception as e:
            logger.error(f"讀取股票代號失敗：{e}")
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    logger.info(f"[Update] 三大法人 {target_date}，{len(codes)} 檔...")
    result = chip_data.get_institutional_flow_multi(
        codes, target_date, target_date, sleep=0.2
    )
    total = sum(len(df) for df in result.values() if not df.empty)
    logger.info(f"[Update] 三大法人完成：{total} 筆")


@retry(max_attempts=3, backoff_factor=2)
def update_margin(target_date: Optional[str] = None, codes: Optional[list[str]] = None) -> None:
    """更新融資融券。"""
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")

    if codes is None:
        conn = None
        try:
            conn = _get_conn()
            codes = [r[0] for r in conn.execute("SELECT code FROM code_market").fetchall()]
        except Exception as e:
            logger.error(f"讀取股票代號失敗：{e}")
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    logger.info(f"[Update] 融資融券 {target_date}，{len(codes)} 檔...")
    try:
        result = margin_data.get_margin_trading_multi(codes)
        # Write to SQLite
        total = 0
        for code, df in result.items():
            if not df.empty:
                upsert_margin(df, code)
                total += len(df)
        logger.info(f"[Update] 融資融券完成：{total} 筆")
    except Exception as e:
        logger.error(f"融資融券更新失敗：{e}")
        raise


def main() -> None:
    today = date.today().strftime("%Y-%m-%d")
    logger.info(f"=== 每日更新 {today} ===")

    # 檢查是否已更新
    if is_today_in_db():
        logger.info("[Skip] 今天已更新過，跳过")
        return

    start = time.time()
    try:
        update_institutional(today)
        update_margin(today)
        elapsed = time.time() - start
        logger.info(f"=== 完成，耗時 {elapsed:.1f} 秒 ===")
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[Error] 更新失敗（耗時 {elapsed:.1f}秒）：{e}")
        raise


if __name__ == "__main__":
    main()
