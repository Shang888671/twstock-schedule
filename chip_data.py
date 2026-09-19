"""三大法人籌碼(個股)與個股權證籌碼資料抓取（優化版）。

改善項目：
1. 加入 requests.Session() 復用連線，減少 TCP 握手開銷
2. 加入指數退避重試機制（最多 3 次，間隔 2/4/8 秒）
3. 加入 TPEx 上櫃三大法人官方端點（tpex_3insti_daily_trading）
4. 自動分離上市/上櫃股票，分別用最適端點抓取
5. 改進錯誤處理與日誌

資料來源：
- 上市三大法人買賣超：TWSE T86（半官方端點）
- 上櫃三大法人買賣超：TPEx 官方 OpenAPI /tpex_3insti_daily_trading
- 個股權證籌碼：TWSE MI_INDEX（type=0999 認購 / 0999P 認售）
"""

import time
import logging
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# TWSE 端點
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

# TPEx 官方 OpenAPI 端點
TPEX_3INSTI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TPEX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "If-Modified-Since": "Mon, 26 Jul 1997 05:00:00 GMT",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# 全域 session（復用連線 + 重試）
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """取得已配置重試機制的 requests Session（單例）。"""
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _session.mount("https://", adapter)
        _session.headers.update(HEADERS)
    return _session


def _to_int(s) -> int:
    s = str(s).strip().replace(",", "")
    if not s or s in ("--", "-"):
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _parse_price_direction(change_html) -> bool | None:
    """從 MI_INDEX『漲跌(+/-)』欄位判斷權證當天收盤價漲跌。"""
    text = str(change_html)
    if "color:red" in text:
        return True
    if "color:green" in text:
        return False
    return None


def _is_listed(code: str) -> bool:
    """判斷股票代號是否為上市（4 碼數字）。"""
    code = code.strip()
    return len(code) == 4 and code.isdigit()


def get_institutional_flow(code: str, start_date: str, end_date: str, sleep: float = 0.3) -> pd.DataFrame:
    """取得個股「三大法人買賣超」逐日資料（股數）。

    自動判斷上市/上櫃，選擇對應端點。
    """
    cache_path = CACHE_DIR / f"institutional_{code}.csv"
    cached = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing = [d for d in dates if cached.empty or d not in cached.index]

    if not missing:
        return cached.loc[(cached.index >= pd.Timestamp(start_date)) & (cached.index <= pd.Timestamp(end_date))].sort_index()

    rows = {}
    session = _get_session()
    is_listed_stock = _is_listed(code)

    for d in missing:
        date_str = d.strftime("%Y%m%d")
        try:
            if is_listed_stock:
                resp = session.get(T86_URL, params={"date": date_str, "selectType": "ALL", "response": "json"}, timeout=15)
                payload = resp.json()
                if payload.get("stat") != "OK" or not payload.get("data"):
                    continue
                for row in payload["data"]:
                    if row[0].strip() == code:
                        rows[d] = [
                            _to_int(row[4]) + _to_int(row[7]),  # foreign
                            _to_int(row[10]),                    # trust
                            _to_int(row[11]),                    # dealer
                            _to_int(row[18]),                    # total
                        ]
                        break
            else:
                resp = session.get(TPEX_3INSTI_URL, params={"date": date_str}, headers=TPEX_HEADERS, timeout=15)
                data = resp.json()
                if isinstance(data, list):
                    for row in data:
                        if str(row.get("SecuritiesCompanyCode", "")).strip() == code:
                            rows[d] = [
                                _to_int(row.get("ForeignInvestment", 0)),
                                _to_int(row.get("InvestmentTrust", 0)),
                                _to_int(row.get("Dealer", 0)),
                                _to_int(row.get("ForeignInvestment", 0)) + _to_int(row.get("InvestmentTrust", 0)) + _to_int(row.get("Dealer", 0)),
                            ]
                            break
        except Exception as e:
            logger.warning(f"抓取 {code} {date_str} 失敗：{e}")
            continue
        time.sleep(sleep)

    if rows:
        new_df = pd.DataFrame.from_dict(rows, orient="index", columns=["foreign_net", "trust_net", "dealer_net", "total_net"])
        combined = pd.concat([cached, new_df]) if not cached.empty else new_df
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_csv(cache_path)
    else:
        combined = cached

    return combined.loc[(combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))].sort_index()


def get_institutional_flow_multi(codes, start_date: str, end_date: str, sleep: float = 0.3, progress_callback=None) -> dict:
    """一批股票的三大法人買賣超。分離上市/上櫃，各用最適端點。"""
    codes = set(codes)
    caches = {}
    for code in codes:
        cache_path = CACHE_DIR / f"institutional_{code}.csv"
        caches[code] = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing_dates = [d for d in dates if any(caches[c].empty or d not in caches[c].index for c in codes)]

    listed_codes = {c for c in codes if _is_listed(c)}
    otc_codes = codes - listed_codes

    new_rows = {code: {} for code in codes}
    session = _get_session()

    for i, d in enumerate(missing_dates):
        date_str = d.strftime("%Y%m%d")

        if listed_codes:
            try:
                resp = session.get(T86_URL, params={"date": date_str, "selectType": "ALL", "response": "json"}, timeout=15)
                payload = resp.json()
                if payload.get("stat") == "OK" and payload.get("data"):
                    for row in payload["data"]:
                        rc = row[0].strip()
                        if rc in listed_codes:
                            new_rows[rc][d] = [_to_int(row[4]) + _to_int(row[7]), _to_int(row[10]), _to_int(row[11]), _to_int(row[18])]
            except Exception as e:
                logger.warning(f"T86 {date_str} 失敗：{e}")

        if otc_codes:
            try:
                resp = session.get(TPEX_3INSTI_URL, params={"date": date_str}, headers=TPEX_HEADERS, timeout=15)
                data = resp.json()
                if isinstance(data, list):
                    for row in data:
                        rc = str(row.get("SecuritiesCompanyCode", "")).strip()
                        if rc in otc_codes:
                            new_rows[rc][d] = [
                                _to_int(row.get("ForeignInvestment", 0)),
                                _to_int(row.get("InvestmentTrust", 0)),
                                _to_int(row.get("Dealer", 0)),
                                _to_int(row.get("ForeignInvestment", 0)) + _to_int(row.get("InvestmentTrust", 0)) + _to_int(row.get("Dealer", 0)),
                            ]
            except Exception as e:
                logger.warning(f"TPEx 3insti {date_str} 失敗：{e}")

        if progress_callback:
            progress_callback(i + 1, len(missing_dates))
        time.sleep(sleep)

    result = {}
    for code in codes:
        cached = caches[code]
        if new_rows[code]:
            new_df = pd.DataFrame.from_dict(new_rows[code], orient="index", columns=["foreign_net", "trust_net", "dealer_net", "total_net"])
            combined = pd.concat([cached, new_df]) if not cached.empty else new_df
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            combined.to_csv(CACHE_DIR / f"institutional_{code}.csv")
        else:
            combined = cached
        result[code] = combined.loc[(combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))].sort_index() if not combined.empty else combined
    return result


def _fetch_warrant_table(date_str: str, put: bool) -> pd.DataFrame:
    """抓 TWSE 單日「全市場」權證收盤行情。"""
    params = {"date": date_str, "type": "0999P" if put else "0999", "response": "json"}
    try:
        resp = _get_session().get(MI_INDEX_URL, params=params, timeout=20)
        payload = resp.json()
    except Exception:
        return pd.DataFrame()
    if payload.get("stat") != "OK":
        return pd.DataFrame()
    table = next((t for t in payload.get("tables", []) if t.get("data")), None)
    return pd.DataFrame(table["data"], columns=table["fields"]) if table is not None else pd.DataFrame()


def get_warrant_flow(code: str, start_date: str, end_date: str, sleep: float = 0.3) -> pd.DataFrame:
    """取得個股對應「權證」逐日成交籌碼。"""
    cache_path = CACHE_DIR / f"warrant_flow_{code}.csv"
    cached = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing = [d for d in dates if cached.empty or d not in cached.index]

    rows = {}
    for d in missing:
        date_str = d.strftime("%Y%m%d")
        call_df = _fetch_warrant_table(date_str, put=False)
        put_df = _fetch_warrant_table(date_str, put=True)
        time.sleep(sleep)

        if call_df.empty and put_df.empty:
            continue

        call_vol = call_val = put_vol = put_val = 0.0
        if not call_df.empty:
            match = call_df[call_df["標的代號"].astype(str).str.strip() == code]
            call_vol = pd.to_numeric(match["成交股數"].astype(str).str.replace(",", ""), errors="coerce").fillna(0).sum()
            call_val = pd.to_numeric(match["成交金額"].astype(str).str.replace(",", ""), errors="coerce").fillna(0).sum()
        if not put_df.empty:
            match = put_df[put_df["標的代號"].astype(str).str.strip() == code]
            put_vol = pd.to_numeric(match["成交股數"].astype(str).str.replace(",", ""), errors="coerce").fillna(0).sum()
            put_val = pd.to_numeric(match["成交金額"].astype(str).str.replace(",", ""), errors="coerce").fillna(0).sum()

        vol_pc_ratio = (put_vol / call_vol * 100) if call_vol > 0 else float("nan")
        rows[d] = [call_vol, put_vol, call_val, put_val, vol_pc_ratio]

    columns = ["warrant_call_volume", "warrant_put_volume", "warrant_call_value", "warrant_put_value", "warrant_vol_pc_ratio"]
    if rows:
        new_df = pd.DataFrame.from_dict(rows, orient="index", columns=columns)
        combined = pd.concat([cached, new_df]) if not cached.empty else new_df
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_csv(cache_path)
    else:
        combined = cached

    return combined.loc[(combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))].sort_index() if not combined.empty else combined


_WARRANT_COUNT_CACHE_TAG = "priceup_v2"


def get_warrant_large_trade_counts_multi(codes, start_date: str, end_date: str, threshold: float, sleep: float = 0.3, progress_callback=None) -> dict:
    """一批股票、一段區間，逐日算出「當天單一認購權證成交金額 >= threshold 且當天收紅的檔數」。"""
    codes = set(codes)
    threshold_tag = int(threshold)
    caches = {}
    for code in codes:
        cache_path = CACHE_DIR / f"warrant_large_trade_count_{_WARRANT_COUNT_CACHE_TAG}_{code}_{threshold_tag}.csv"
        caches[code] = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing_dates = [d for d in dates if any(caches[c].empty or d not in caches[c].index for c in codes)]

    new_rows = {code: {} for code in codes}
    for i, d in enumerate(missing_dates):
        date_str = d.strftime("%Y%m%d")
        call_df = _fetch_warrant_table(date_str, put=False)
        if not call_df.empty:
            call_df = call_df.copy()
            call_df["_value"] = pd.to_numeric(call_df["成交金額"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
            call_df["_underlying"] = call_df["標的代號"].astype(str).str.strip()
            call_df["_price_up"] = call_df["漲跌(+/-)"].apply(_parse_price_direction)
            for code in codes:
                match = call_df[call_df["_underlying"] == code]
                new_rows[code][d] = int(((match["_value"] >= threshold) & (match["_price_up"] == True)).sum())
        if progress_callback:
            progress_callback(i + 1, len(missing_dates))
        time.sleep(sleep)

    result = {}
    for code in codes:
        cached = caches[code]
        if new_rows[code]:
            new_df = pd.DataFrame.from_dict(new_rows[code], orient="index", columns=["count"])
            combined = pd.concat([cached, new_df]) if not cached.empty else new_df
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            combined.to_csv(CACHE_DIR / f"warrant_large_trade_count_{_WARRANT_COUNT_CACHE_TAG}_{code}_{threshold_tag}.csv")
        else:
            combined = cached
        result[code] = combined.loc[(combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))].sort_index() if not combined.empty else combined
    return result


def get_stock_call_warrant_detail(code: str, date_str: str):
    """取得某一天『認購權證』對應到指定股票代號的「個別權證」明細。"""
    table = _fetch_warrant_table(date_str, put=False)
    if table.empty:
        return None
    match = table[table["標的代號"].astype(str).str.strip() == code].copy()
    if match.empty:
        return pd.DataFrame(columns=["warrant_code", "warrant_name", "volume", "value", "price_up"])
    match["volume"] = pd.to_numeric(match["成交股數"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    match["value"] = pd.to_numeric(match["成交金額"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    match["price_up"] = match["漲跌(+/-)"].apply(_parse_price_direction)
    return match.rename(columns={"證券代號": "warrant_code", "證券名稱": "warrant_name"})[
        ["warrant_code", "warrant_name", "volume", "value", "price_up"]
    ]


if __name__ == "__main__":
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=30)

    print("=== 上市三大法人買賣超（2330, 近 30 天）===")
    flow = get_institutional_flow("2330", start.isoformat(), end.isoformat())
    print(flow.tail())
    print(f"共 {len(flow)} 筆")

    print("\n=== 上櫃三大法人買賣超（6488, 近 30 天）===")
    flow_otc = get_institutional_flow("6488", start.isoformat(), end.isoformat())
    print(flow_otc.tail())
    print(f"共 {len(flow_otc)} 筆")
