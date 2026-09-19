"""抓取台股即時報價與歷史資料（優化版）。

改善項目：
1. get_quote() 改用 TWSE MIS 官方即時報價（原用 Yahoo Finance 延遲 15-20 分鐘）
2. 加入 requests.Session() 復用連線 + 指數退避重試
3. 上櫃即時報價改打 TWSE MIS（而非 yfinance 的延遲資料）
4. get_history() 仍用 Yahoo Finance（TWSE MIS 無歷史查詢能力）
5. 自動判斷上市/上櫃，選擇對應端點
6. get_history() 使用 SQLite 快取（每日刷新），避免重複呼叫 yfinance
"""

import time
import logging
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")

ISIN_LIST_URLS: dict[bool, str] = {
    False: "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
    True: "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",
}
CACHE_DIR: Path = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS: int = 7 * 86400

HEADERS: dict[str, str] = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

_TWSE_MIS_QUOTE_URL: str = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_TWSE_MIS_INDEX_URL: str = "https://mis.twse.com.tw/stock/index.jsp"

_TPEX_INDEX_URL: str = "https://www.tpex.org.tw/openapi/v1/tpex_index"
_TPEX_DAILY_TRADING_INDEX_URL: str = "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index"
_TPEX_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "If-Modified-Since": "Mon, 26 Jul 1997 05:00:00 GMT",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

_OTC_INDEX_HISTORY_CACHE: Path = CACHE_DIR / "otc_index_history.csv"

_session: requests.Session | None = None
_mis_session: requests.Session | None = None
_mis_session_primed: bool = False


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _session.mount("https://", adapter)
        _session.headers.update(HEADERS)
    return _session


def _get_mis_session() -> requests.Session:
    global _mis_session, _mis_session_primed
    if _mis_session is None:
        _mis_session = requests.Session()
        retry = Retry(total=2, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _mis_session.mount("https://", adapter)
        _mis_session.headers.update(HEADERS)
    if not _mis_session_primed:
        try:
            _mis_session.get(_TWSE_MIS_INDEX_URL, timeout=10)
        except Exception:
            pass
        _mis_session_primed = True
    return _mis_session


def _is_listed(code: str) -> bool:
    code = code.strip()
    return len(code) == 4 and code.isdigit()


def to_yf_symbol(code: str, otc: bool = False) -> str:
    if code.startswith("^") or code.endswith(".TW") or code.endswith(".TWO"):
        return code
    return f"{code}.TWO" if otc else f"{code}.TW"


def _parse_float(raw: dict, key: str) -> float | None:
    v = raw.get(key)
    if v is None or v in ("-", ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def get_quote(code: str, otc: bool = False) -> dict:
    """取得即時報價（TWSE MIS 官方即時，非延遲）。

    改良：打 TWSE MIS 即時 API，失敗才 fallback 到 Yahoo Finance。
    """
    symbol = to_yf_symbol(code, otc)

    if otc is False and _is_listed(code):
        ex_ch = f"tse_{code}.tw"
        try:
            session = _get_mis_session()
            resp = session.get(
                _TWSE_MIS_QUOTE_URL,
                params={"ex_ch": ex_ch, "json": 1, "delay": 0},
                timeout=10,
            )
            data = resp.json()
            msg_array = data.get("msgArray") or []
            if msg_array:
                raw = msg_array[0]
                last_price = _parse_float(raw, "z")
                if last_price is None:
                    last_price = _parse_float(raw.get("trade") or {}, "z")
                if last_price is not None:
                    volume_lots = _parse_float(raw, "v")
                    return {
                        "symbol": symbol,
                        "last_price": last_price,
                        "previous_close": _parse_float(raw, "y"),
                        "day_high": _parse_float(raw, "h"),
                        "day_low": _parse_float(raw, "l"),
                        "volume": int(volume_lots * 1000) if volume_lots else None,
                        "source": "twse_mis",
                    }
        except Exception as e:
            logger.warning(f"TWSE MIS 即時報價失敗（{code}）：{e}，改用 Yahoo Finance")
    elif otc:
        ex_ch = f"otc_{code}.tw"
        try:
            session = _get_mis_session()
            resp = session.get(
                _TWSE_MIS_QUOTE_URL,
                params={"ex_ch": ex_ch, "json": 1, "delay": 0},
                timeout=10,
            )
            data = resp.json()
            msg_array = data.get("msgArray") or []
            if msg_array:
                raw = msg_array[0]
                last_price = _parse_float(raw, "z")
                if last_price is None:
                    last_price = _parse_float(raw.get("trade") or {}, "z")
                if last_price is not None:
                    volume_lots = _parse_float(raw, "v")
                    return {
                        "symbol": symbol,
                        "last_price": last_price,
                        "previous_close": _parse_float(raw, "y"),
                        "day_high": _parse_float(raw, "h"),
                        "day_low": _parse_float(raw, "l"),
                        "volume": int(volume_lots * 1000) if volume_lots else None,
                        "source": "tpex_mis",
                    }
        except Exception as e:
            logger.warning(f"TWSE MIS 上櫃報價失敗（{code}）：{e}，改用 Yahoo Finance")

    # Fallback: Yahoo Finance
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        return {
            "symbol": symbol,
            "last_price": info.get("lastPrice"),
            "previous_close": info.get("previousClose"),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "volume": info.get("lastVolume"),
            "source": "yahoo_delayed",
        }
    except Exception as e:
        logger.error(f"Yahoo Finance 也失敗（{code}）：{e}")
        return {"symbol": symbol, "source": "failed"}


def get_tpex_otc_index_quote() -> dict | None:
    try:
        session = _get_session()
        resp = session.get(_TPEX_INDEX_URL, headers=_TPEX_HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return None
    if not data:
        return None
    latest = data[-1]
    try:
        close = float(latest["Close"])
        change = float(latest["Change"])
        return {
            "symbol": "^TWOII",
            "last_price": close,
            "previous_close": close - change,
            "day_high": float(latest["High"]),
            "day_low": float(latest["Low"]),
            "volume": None,
            "asof": latest["Date"],
        }
    except (KeyError, ValueError):
        return None


def get_tpex_otc_index_previous_day() -> dict | None:
    try:
        session = _get_session()
        resp = session.get(_TPEX_INDEX_URL, headers=_TPEX_HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return None
    if not data:
        return None
    today_str = datetime.now(_TAIPEI_TZ).strftime("%Y%m%d")
    dates = [d.get("Date") for d in data]
    if today_str in dates:
        idx = dates.index(today_str)
        if idx == 0:
            return None
        prev = data[idx - 1]
    else:
        prev = data[-1]
    try:
        return {
            "date": prev["Date"],
            "open": float(prev["Open"]),
            "high": float(prev["High"]),
            "low": float(prev["Low"]),
            "close": float(prev["Close"]),
        }
    except (KeyError, ValueError):
        return None


def _roc_date_to_western(roc_date: str) -> str:
    year = int(roc_date[:3]) + 1911
    return f"{year}{roc_date[3:]}"


def update_otc_index_history_cache() -> pd.DataFrame:
    if _OTC_INDEX_HISTORY_CACHE.exists():
        cache_df = pd.read_csv(_OTC_INDEX_HISTORY_CACHE, dtype={"Date": str})
    else:
        cache_df = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    try:
        session = _get_session()
        idx_data = session.get(_TPEX_INDEX_URL, headers=_TPEX_HEADERS, timeout=15).json()
        vol_data = session.get(_TPEX_DAILY_TRADING_INDEX_URL, headers=_TPEX_HEADERS, timeout=15).json()
    except Exception:
        idx_data, vol_data = None, None

    if idx_data and vol_data:
        volume_by_date = {}
        for row in vol_data:
            try:
                volume_by_date[_roc_date_to_western(row["Date"])] = float(row["TradeVolume"])
            except (KeyError, ValueError):
                continue
        new_rows = []
        for row in idx_data:
            date_str = row.get("Date")
            try:
                new_rows.append({
                    "Date": date_str,
                    "Open": float(row["Open"]),
                    "High": float(row["High"]),
                    "Low": float(row["Low"]),
                    "Close": float(row["Close"]),
                    "Volume": volume_by_date.get(date_str),
                })
            except (KeyError, ValueError):
                continue
        if new_rows:
            new_df = pd.DataFrame(new_rows)
            cache_df = new_df if cache_df.empty else pd.concat([cache_df, new_df], ignore_index=True)
            cache_df = cache_df.drop_duplicates(subset="Date", keep="last")

    cache_df = cache_df.sort_values("Date").reset_index(drop=True)
    cache_df.to_csv(_OTC_INDEX_HISTORY_CACHE, index=False)
    result_df = cache_df.copy()
    result_df.index = pd.to_datetime(result_df.pop("Date"), format="%Y%m%d")
    result_df.index.name = "date"
    return result_df


def get_history(
    code: str,
    period: str = "1y",
    interval: str = "1d",
    otc: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """取得歷史 OHLCV 資料，使用 SQLite 快取（每日刷新）。

    先查 SQLite，快取未過期則直接回傳；否則打 yfinance 並寫入快取。
    """
    from database import init_db, get_history as db_get_history, upsert_history

    init_db()

    # 只有使用 period（未指定 start/end）時才用快取
    if start is None and end is None:
        cached = db_get_history(code, period=period)
        if cached is not None and not cached.empty:
            logger.info(f"[{code}] 使用 SQLite 歷史快取（{len(cached)} 筆）")
            return cached

    # 無快取或指定日期範圍：打 yfinance
    symbol = to_yf_symbol(code, otc)
    ticker = yf.Ticker(symbol)
    if start or end:
        df = ticker.history(start=start, end=end, interval=interval)
    else:
        df = ticker.history(period=period, interval=interval)
    df.index.name = "date"

    # 只有使用 period 時才寫入快取
    if start is None and end is None and not df.empty:
        upsert_history(df, code)
        logger.info(f"[{code}] 已寫入 SQLite 歷史快取（{len(df)} 筆）")

    return df


def _load_isin_name_table(otc: bool, force_refresh: bool = False) -> pd.DataFrame:
    cache_path = CACHE_DIR / f"isin_names_{'otc' if otc else 'listed'}.csv"
    if not force_refresh and cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL_SECONDS:
        return pd.read_csv(cache_path, dtype=str)
    session = _get_session()
    resp = session.get(ISIN_LIST_URLS[otc], timeout=20)
    resp.encoding = "cp950"
    tables = pd.read_html(StringIO(resp.text))
    table = max(tables, key=len)
    split = table.iloc[1:][0].astype(str).str.split("　", n=1, expand=True)
    valid = split[1].notna()
    result = pd.DataFrame({"code": split[0][valid].str.strip(), "name": split[1][valid].str.strip()})
    result = result.drop_duplicates(subset="code")
    result.to_csv(cache_path, index=False)
    return result


def get_chinese_name(code: str, otc: bool = False) -> str | None:
    df = _load_isin_name_table(otc)
    match = df.loc[df["code"] == code, "name"]
    return match.iloc[0] if not match.empty else None


if __name__ == "__main__":
    print("=== 即時報價（TWSE MIS）===")
    quote = get_quote("2330")
    for k, v in quote.items():
        print(f"  {k}: {v}")
    print()
    quote_otc = get_quote("6488", otc=True)
    for k, v in quote_otc.items():
        print(f"  {k}: {v}")
