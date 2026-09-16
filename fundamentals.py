"""個股月營收基本面資料抓取。

月營收年增率是中長期股價的根本驅動力之一,現有系統(indicators.py/signals.py 等)完全是
技術/籌碼面,這裡補上第一個基本面指標。

資料來源:MOPS(公開資訊觀測站)每月營業收入彙總表,上市/上櫃分開兩個端點,欄位命名完全
一致(都是「公司代號」「營業收入-去年同月增減(%)」等),這是台灣公開資料常見的慣例——
上市走 TWSE OpenAPI,上櫃走 TPEx OpenAPI 底下同樣掛 MOPS 前綴(`mopsfin_`)的對應端點。
這是「全市場單月快照」(某月營收公告後,所有公司都在同一份表裡),不是任意公司的歷史區間
查詢,MoM/YoY成長率是 MOPS 官方直接算好的欄位,不用自己算。本地用 TTL 快取整份表(比照
fetch_data.py 的 `_load_isin_name_table()` 模式),避免每次查詢都重打一次全市場的表。
"""

import time
from pathlib import Path

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = 6 * 3600  # 月營收一個月才更新一次,6小時只是避免同一天內反覆重打

REVENUE_URL = {
    False: "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",  # 上市
    True: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",  # 上櫃
}


def _to_float(s) -> float | None:
    s = str(s).strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _load_revenue_table(otc: bool, force_refresh: bool = False) -> pd.DataFrame:
    cache_path = CACHE_DIR / f"revenue_{'otc' if otc else 'listed'}.csv"
    if not force_refresh and cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL_SECONDS:
        return pd.read_csv(cache_path, dtype=str)

    try:
        resp = requests.get(REVENUE_URL[otc], headers=HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return pd.read_csv(cache_path, dtype=str) if cache_path.exists() else pd.DataFrame()

    df = pd.DataFrame(data, dtype=str)
    df.to_csv(cache_path, index=False)
    return df


def get_monthly_revenue(code: str, otc: bool = False) -> dict | None:
    """取得這檔股票最新一個月的營收年增率/月增率。

    回傳 {"period": "資料年月"(例如"11508"), "revenue": int(當月營收,千元),
    "mom_pct": float|None(上月比較增減%), "yoy_pct": float|None(去年同月增減%)}。
    查無資料(例如剛上市、代號輸入錯誤)回傳 None。mom_pct/yoy_pct 在公司沒有對應期間
    可比較時(例如剛上市不滿一年)MOPS 原始資料就是空字串,這裡保留 None 不硬湊。
    """
    df = _load_revenue_table(otc)
    if df.empty:
        return None
    match = df[df["公司代號"].astype(str) == code]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "period": row["資料年月"],
        "revenue": int(float(row["營業收入-當月營收"])) if row["營業收入-當月營收"] else None,
        "mom_pct": _to_float(row["營業收入-上月比較增減(%)"]),
        "yoy_pct": _to_float(row["營業收入-去年同月增減(%)"]),
    }


if __name__ == "__main__":
    print("=== 月營收(2330)===")
    print(get_monthly_revenue("2330"))

    print("\n=== 月營收(6488,上櫃)===")
    print(get_monthly_revenue("6488", otc=True))
