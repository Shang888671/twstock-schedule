"""抓 TAIFEX「股票期貨/股票選擇權 交易標的」清單,取得所有「個股期貨」標的的股票代號。

這是公開的靜態頁面(https://www.taifex.com.tw/cht/2/stockLists),不用驗證碼、可以直接用
requests 抓,跟分點資料(bsr.twse.com.tw)不一樣。這份名單變動很慢(只有新增/下市股票期貨
才會變),本地快取 7 天。
"""

import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

STOCK_FUTURES_LIST_URL = "https://www.taifex.com.tw/cht/2/stockLists"
HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_PATH = CACHE_DIR / "stock_futures_list.csv"
CACHE_TTL_SECONDS = 7 * 86400


def _load_stock_futures_table(force_refresh: bool = False) -> pd.DataFrame:
    if not force_refresh and CACHE_PATH.exists() and (time.time() - CACHE_PATH.stat().st_mtime) < CACHE_TTL_SECONDS:
        return pd.read_csv(CACHE_PATH, dtype=str)

    resp = requests.get(STOCK_FUTURES_LIST_URL, headers=HEADERS, timeout=15)
    tables = pd.read_html(StringIO(resp.text))
    table = max(tables, key=lambda t: len(t))

    futures_col = next(c for c in table.columns if "股票期貨" in str(c) and "選擇權" not in str(c))
    listed_col = next(c for c in table.columns if "上市普通股" in str(c))
    otc_col = next(c for c in table.columns if "上櫃普通股" in str(c))
    code_col = next(c for c in table.columns if "證券代號" in str(c))
    name_col = next(c for c in table.columns if "標的證券 簡稱" in str(c) or "標的證券簡稱" in str(c))

    futures_mask = table[futures_col].astype(str).str.contains("股票期貨標的", na=False)
    common_stock_mask = table[listed_col].astype(str).str.contains("普通股標的證券", na=False) | table[
        otc_col
    ].astype(str).str.contains("普通股標的證券", na=False)

    result = table.loc[futures_mask & common_stock_mask, [code_col, name_col]].copy()
    result.columns = ["code", "name"]
    result["code"] = result["code"].astype(str).str.strip()
    result = result.drop_duplicates(subset="code")

    result.to_csv(CACHE_PATH, index=False)
    return result


def get_stock_futures_codes(force_refresh: bool = False) -> set:
    """回傳所有「個股期貨」標的的股票代號集合(字串)。

    注意:TAIFEX 這個頁面的「是否為股票期貨標的」欄位其實同時涵蓋 ETF 期貨的標的
    (例如 0050 也會被標記為「是股票期貨標的」),不能只看這一欄——還要另外用
    「上市普通股標的證券」/「上櫃普通股標的證券」欄位排除掉 ETF,只留下真正的個股。
    """
    df = _load_stock_futures_table(force_refresh=force_refresh)
    return set(df["code"].astype(str).str.strip())


def get_stock_futures_name(code: str) -> str | None:
    """回傳個股期貨標的的股票簡稱(查不到回傳 None)。"""
    df = _load_stock_futures_table()
    match = df.loc[df["code"].astype(str).str.strip() == code, "name"]
    return match.iloc[0] if not match.empty else None


if __name__ == "__main__":
    codes = get_stock_futures_codes()
    print(f"共 {len(codes)} 檔個股期貨標的")
    print(sorted(codes)[:20])
