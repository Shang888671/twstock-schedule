"""特殊時間點提醒:除權息預告、期貨/選擇權結算日、近期重大訊息公告。

這三個都不是「多空訊號」——除權息本身沒有好壞之分、結算日前後的技術性波動也沒有方向性、
重大訊息公告內容可能利多可能利空,不看內容無法判斷——所以不併入 signals.py 的燈號計分卡,
只在 app.py 顯示成「有的話才顯示」的提示訊息,平常沒有接近的事件就完全不顯示,不佔版面。

除權息預告表跟重大訊息表,上市(TWSE)/上櫃(TPEx)兩邊都是 MOPS 系列資料,但兩個交易所
各自的欄位命名不一致(甚至同一個交易所內,除權息端點用英文欄位、重大訊息端點卻混雜英文
代號欄位+中文內容欄位),所以用 EXRIGHT_FIELDS/MATERIAL_FIELDS 兩個對照表分別處理,不硬找
共通邏輯。西元/民國年轉換(`_roc_to_date`)跟量能單位換算一樣,是每個直接對接 TWSE/TPEx
原始資料的模組都要自己處理一次的小工具,不特別抽成共用模組(比照 chip_data.py/fetch_data.py
各自處理各自日期格式的慣例)。
"""

import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = 6 * 3600

EXRIGHT_URL = {
    False: "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL",
    True: "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost",
}
EXRIGHT_FIELDS = {
    False: {"date": "Date", "code": "Code", "cash_dividend": "CashDividend"},
    True: {"date": "ExRrightsExDividendDate", "code": "SecuritiesCompanyCode", "cash_dividend": "CashDividend"},
}

MATERIAL_URL = {
    False: "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
    True: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
}
MATERIAL_FIELDS = {
    False: {"date": "發言日期", "code": "公司代號", "subject": "主旨 "},  # TWSE「主旨」欄位名帶尾隨空白,是官方資料本身的問題
    True: {"date": "發言日期", "code": "SecuritiesCompanyCode", "subject": "主旨"},
}


def _roc_to_date(s: str) -> date | None:
    """民國年日期字串("1150914")轉西元 date。格式不對時回傳 None。"""
    s = str(s).strip()
    if len(s) != 7 or not s.isdigit():
        return None
    year = int(s[:3]) + 1911
    month, day = int(s[3:5]), int(s[5:7])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _load_table(url: str, cache_name: str, force_refresh: bool = False) -> pd.DataFrame:
    cache_path = CACHE_DIR / f"{cache_name}.csv"
    if not force_refresh and cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL_SECONDS:
        return pd.read_csv(cache_path, dtype=str)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return pd.read_csv(cache_path, dtype=str) if cache_path.exists() else pd.DataFrame()
    df = pd.DataFrame(data, dtype=str)
    df.to_csv(cache_path, index=False)
    return df


def get_upcoming_ex_dividend(code: str, otc: bool = False, within_trading_days: int = 10) -> dict | None:
    """未來 within_trading_days 個交易日內(含今天)最近一筆除權息預告。

    回傳 {"date": date, "cash_dividend": float, "trading_days_until": int}。沒有回傳 None。
    交易日用 `pd.bdate_range` 估算(不含國定假日行事曆,見 intraday.py 同樣的已知限制)。
    """
    fields = EXRIGHT_FIELDS[otc]
    df = _load_table(EXRIGHT_URL[otc], f"exright_{'otc' if otc else 'listed'}")
    if df.empty:
        return None
    match = df[df[fields["code"]].astype(str) == code].copy()
    if match.empty:
        return None

    today = date.today()
    match["_date"] = match[fields["date"]].apply(_roc_to_date)
    match = match.dropna(subset=["_date"])
    match = match[match["_date"] >= today]
    if match.empty:
        return None
    match = match.sort_values("_date")
    row = match.iloc[0]
    ex_date = row["_date"]
    trading_days_until = max(0, len(pd.bdate_range(today, ex_date)) - 1)
    if trading_days_until > within_trading_days:
        return None
    try:
        cash_dividend = float(row[fields["cash_dividend"]])
    except (ValueError, TypeError):
        cash_dividend = 0.0
    return {"date": ex_date, "cash_dividend": cash_dividend, "trading_days_until": trading_days_until}


def get_recent_material_announcements(code: str, otc: bool = False, lookback_days: int = 3) -> list:
    """近 lookback_days 天內(含今天)這檔股票的重大訊息公告,依日期由新到舊排序。

    回傳 [{"date": date, "subject": str}, ...],沒有回傳空 list。這個端點本身就是
    MOPS「近期」公告快照(不支援任意歷史區間查詢),TTL 快取只是避免每次 Streamlit
    rerun 都重打一次全市場的表,不是拿來累積歷史。
    """
    fields = MATERIAL_FIELDS[otc]
    df = _load_table(MATERIAL_URL[otc], f"material_{'otc' if otc else 'listed'}")
    if df.empty:
        return []
    match = df[df[fields["code"]].astype(str) == code].copy()
    if match.empty:
        return []

    cutoff = date.today() - timedelta(days=lookback_days - 1)
    match["_date"] = match[fields["date"]].apply(_roc_to_date)
    match = match.dropna(subset=["_date"])
    match = match[match["_date"] >= cutoff]
    if match.empty:
        return []
    match = match.sort_values("_date", ascending=False)
    return [
        {"date": row["_date"], "subject": str(row[fields["subject"]]).strip()}
        for _, row in match.iterrows()
    ]


def _third_wednesday(year: int, month: int) -> date:
    first_of_month = date(year, month, 1)
    days_to_wed = (2 - first_of_month.weekday()) % 7  # Monday=0 ... Wednesday=2
    first_wed = first_of_month + timedelta(days=days_to_wed)
    return first_wed + timedelta(days=14)


def get_futures_settlement_info(reference_date: date | None = None, within_days: int = 3) -> dict:
    """台指期/選擇權結算日是每月第三個星期三,純計算不用抓資料。

    回傳 {"settlement_date": date, "days_until": int, "is_near": bool}
    (is_near = 結算日前 within_days 天內,含當天)。
    """
    ref = reference_date or date.today()
    settlement = _third_wednesday(ref.year, ref.month)
    if settlement < ref:
        year, month = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
        settlement = _third_wednesday(year, month)
    days_until = (settlement - ref).days
    return {"settlement_date": settlement, "days_until": days_until, "is_near": 0 <= days_until <= within_days}


if __name__ == "__main__":
    print("=== 除權息預告(2330)===")
    print(get_upcoming_ex_dividend("2330"))

    print("\n=== 近期重大訊息(2330)===")
    for item in get_recent_material_announcements("2330"):
        print(item)

    print("\n=== 期貨結算日 ===")
    print(get_futures_settlement_info())
