"""個股融資融券餘額資料抓取。

融資餘額變化代表散戶籌碼輕重(融資增加=籌碼變重、變不穩;減少=籌碼轉健康),融券餘額變化
代表放空力道,是台股很常見的籌碼面指標,跟 chip_data.py 的三大法人是互補角度。

**資料源限制(比原本設計時預期的更嚴格,務必先讀這段)**:TWSE OpenAPI 的
`exchangeReport/MI_MARGN`(`MARGIN_ALL_URL`)雖然回傳「每一檔股票各自」的融資融券數字
(不是只有大盤合計),但這個端點**沒有任何查詢參數**(swagger 文件確認 `parameters` 是
空的)——不管有沒有帶 `date=`,永遠只回傳「最新一個交易日」的快照,無法查任意過去日期。
(原本規劃時以為 `www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?stockNo=&date=` 這個路徑
可以做到單股歷史查詢且 `date` 參數有效——後來發現 `stockNo` 其實完全被忽略,不管傳什麼值
永遠回傳「大盤合計」而不是個股數字,`date` 雖然真的有效但查到的也是大盤合計,不是個股,
所以這個路徑整個不能用。已經改用下面這個真正的個股資料源。)

所以融資融券**沒有官方的歷史區間查詢 API**,只能每天存一筆「今天」的快照慢慢累積本地
快取——剛開始查一檔新股票時歷史筆數會不夠(第一天查只有1筆,沒有「今天 vs 前日」可以比較),
要連續幾天使用這個功能才會累積出足夠的趨勢資料,這是資料源本身的限制,不是 bug。這點跟
`intraday.py` 的短線動能訊號(只從打開頁面那一刻累積 session 內樣本)是類似的「資料只能
隨使用時間累積」限制。

只支援上市股票——這個端點沒有上櫃股票的資料,也沒有找到 TPEx 對應的個股歷史查詢端點,
這次不處理上櫃(跟 chip_data.py 的三大法人資料同樣只支援上市是一致的限制)。
"""

from pathlib import Path

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

MARGIN_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"  # 全市場單日快照,無查詢參數


def _to_int(s) -> int:
    s = str(s).strip().replace(",", "")
    if not s or s in ("--", "-"):
        return 0
    return int(s)


def _fetch_and_cache_today(codes: set) -> dict:
    """打一次全市場快照,把 codes 裡有出現的股票各自的融資/融券今日餘額存進本地快取
    (`data_cache/margin_{code}.csv`,index 是日期,欄位 margin_balance/short_balance,
    單位張)。已經有今天這筆快取的股票不重打。回傳每一檔目前為止累積到的完整快取內容。
    """
    caches = {}
    for code in codes:
        cache_path = CACHE_DIR / f"margin_{code}.csv"
        caches[code] = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    today = pd.Timestamp.today().normalize()
    need_fetch = any(caches[c].empty or today not in caches[c].index for c in codes)
    if need_fetch:
        try:
            resp = requests.get(MARGIN_ALL_URL, headers=HEADERS, timeout=15)
            data = resp.json()
        except Exception:
            data = []
        by_code = {str(row.get("股票代號", "")).strip(): row for row in data}
        for code in codes:
            row = by_code.get(code)
            if row is None:
                continue
            new_df = pd.DataFrame(
                {
                    "margin_balance": [_to_int(row.get("融資今日餘額", ""))],
                    "short_balance": [_to_int(row.get("融券今日餘額", ""))],
                },
                index=[today],
            )
            cached = caches[code]
            combined = pd.concat([cached, new_df]) if not cached.empty else new_df
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            combined.to_csv(CACHE_DIR / f"margin_{code}.csv")
            caches[code] = combined

    return caches


def get_margin_trading(code: str, lookback_days: int = 10) -> pd.DataFrame:
    """取得這檔股票目前為止累積到的融資融券快照(最多回傳最近 `lookback_days` 筆)。

    只支援上市股票。回傳欄位:margin_balance(融資今日餘額,張)、short_balance(融券今日
    餘額,張)。實際筆數取決於這檔股票被查詢過幾個不同的交易日(見模組docstring的資料源
    限制),不是保證有 `lookback_days` 筆。
    """
    caches = _fetch_and_cache_today({code})
    df = caches[code]
    return df.tail(lookback_days) if not df.empty else df


def get_margin_trading_multi(codes, progress_callback=None) -> dict:
    """批次掃描用,一次快照涵蓋整批股票(比逐檔呼叫 `get_margin_trading` 省請求數,
    但反正這個資料源本來就只有「今天」一筆,批次與否差別只在請求次數,不影響資料完整度)。

    progress_callback(done, total) 可選,比照 chip_data.py 其餘 `_multi` 函式的介面,
    這裡固定回報 (1, 1)(只有一次「打一次全市場快照」的動作)。

    回傳:{code: DataFrame}(欄位跟 `get_margin_trading` 相同,回傳目前累積到的全部筆數)
    """
    caches = _fetch_and_cache_today(set(codes))
    if progress_callback:
        progress_callback(1, 1)
    return caches


if __name__ == "__main__":
    print("=== 融資融券餘額(2330,目前累積到的快照)===")
    df = get_margin_trading("2330")
    print(df)
    print(f"共 {len(df)} 筆(第一次查詢只會有1筆,連續幾天查詢會慢慢累積)")
