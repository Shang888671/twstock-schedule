"""相對強弱(RS)排行。

概念取自 IBD(Investor's Business Daily)公開的 RS Rating 方法論——台股圈子裡老墨等
技術分析老師教的「相對強弱」選股邏輯也是同一套公開概念的台股應用版本。這裡只實作
公開已知的計算方式(加權近期漲幅、全市場排名轉百分位),不涉及、也沒有參考任何加密檔案
或他人未公開的程式碼,純粹用 yfinance 的公開股價資料自己算。

RS Rating 算法:
1. 對每檔股票算一個「加權報酬分數」——近一季漲幅權重是其他三季的兩倍(IBD 原始精神是
   「越近期的表現權重越高」),同時看 3/6/9/12 個月的報酬:
     raw_score = 2 * r3m + r6m + r9m + r12m   (r_nm = 現價 / n個月前收盤價)
2. 把股票池裡所有股票的 raw_score 由大到小排序,轉成 1~99 的百分位名次
   (99 = 全池最強,1 = 全池最弱),這個名次就是一般說的「RS Rating / RS 值」。

資料不足(例如新上市不到 12 個月)的股票會被跳過,不會出現在排行結果裡。
"""

from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from fetch_data import get_history

CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
_SCORE_CACHE_PATH = CACHE_DIR / "rs_raw_scores.csv"

_MONTH_TRADING_DAYS = 21

RS_BENCHMARK_SYMBOL = "^TWII"
RS_BENCHMARK_WINDOW_DAYS = 20


def _return_over_months(close: pd.Series, months: int):
    lookback_days = months * _MONTH_TRADING_DAYS
    if len(close) <= lookback_days:
        return None
    now = close.iloc[-1]
    past = close.iloc[-1 - lookback_days]
    if past is None or past <= 0:
        return None
    return float(now / past)


def _load_score_cache() -> pd.DataFrame:
    if _SCORE_CACHE_PATH.exists():
        return pd.read_csv(_SCORE_CACHE_PATH, dtype={"code": str})
    return pd.DataFrame(columns=["code", "asof_date", "raw_score", "r3m", "r6m", "r9m", "r12m"])


def _save_score_cache(df: pd.DataFrame):
    df.to_csv(_SCORE_CACHE_PATH, index=False)


def compute_rs_raw_score(code: str, otc: bool = False):
    """計算單一股票的 RS 加權原始分數。資料不足時回傳 None。"""
    hist = get_history(code, period="14mo", interval="1d", otc=otc)
    if hist.empty:
        return None
    close = hist["Close"].dropna()

    r3, r6, r9, r12 = (
        _return_over_months(close, 3),
        _return_over_months(close, 6),
        _return_over_months(close, 9),
        _return_over_months(close, 12),
    )
    if None in (r3, r6, r9, r12):
        return None

    raw_score = 2 * r3 + r6 + r9 + r12
    return {
        "code": code,
        "raw_score": raw_score,
        "r3m": r3 - 1,
        "r6m": r6 - 1,
        "r9m": r9 - 1,
        "r12m": r12 - 1,
    }


def _ratings_from_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("raw_score", ascending=False).reset_index(drop=True)
    n = len(df)
    df["rs_rating"] = [
        max(1, round(99 - (i / (n - 1 if n > 1 else 1)) * 98)) for i in range(n)
    ]
    return df


def compute_rs_ranking(codes, otc_map: dict | None = None, use_cache: bool = True, progress_callback=None) -> pd.DataFrame:
    """對一批股票代號算 RS 排行,回傳依 rs_rating 由高到低排序的 DataFrame。

    當天已經算過的股票會用快取(`data_cache/rs_raw_scores.csv`),不用每次都重新打
    yfinance——RS 排行本來就是看月線等級的趨勢,同一天內重算沒有意義。
    progress_callback(done, total) 可選,用來在 UI 顯示掃描進度。
    """
    otc_map = otc_map or {}
    codes = list(dict.fromkeys(codes))  # 去重複但保留順序
    today = date.today().isoformat()

    cache = _load_score_cache() if use_cache else pd.DataFrame(columns=["code", "asof_date"])
    cache_today = cache[cache["asof_date"] == today] if not cache.empty else cache

    rows = []
    to_fetch = []
    for code in codes:
        hit = cache_today[cache_today["code"] == code] if not cache_today.empty else pd.DataFrame()
        if not hit.empty:
            rows.append(hit.iloc[0].to_dict())
        else:
            to_fetch.append(code)

    new_rows = []
    for i, code in enumerate(to_fetch):
        result = compute_rs_raw_score(code, otc=otc_map.get(code, False))
        if result is not None:
            result["asof_date"] = today
            new_rows.append(result)
            rows.append(result)
        if progress_callback:
            progress_callback(i + 1, len(to_fetch))

    if new_rows and use_cache:
        cache_other_days = cache[cache["asof_date"] != today] if not cache.empty else cache
        parts = [df for df in (cache_other_days, cache_today, pd.DataFrame(new_rows)) if not df.empty]
        updated = pd.concat(parts, ignore_index=True)
        updated = updated.drop_duplicates(subset=["code", "asof_date"], keep="last")
        _save_score_cache(updated)

    if not rows:
        return pd.DataFrame(columns=["code", "raw_score", "rs_rating", "r3m", "r6m", "r9m", "r12m", "asof_date"])

    return _ratings_from_scores(pd.DataFrame(rows))


def get_rs_rating_for_code(code: str, universe_codes, otc: bool = False, otc_map: dict | None = None):
    """算某一檔股票在指定股票池(universe_codes)裡的 RS Rating(1~99)。

    回傳 (該股的 rating 資訊 dict, 完整排行 DataFrame);資料不足算不出來時回傳 (None, 排行 DataFrame)。
    """
    codes = list(dict.fromkeys([*universe_codes, code]))
    otc_map = dict(otc_map or {})
    otc_map.setdefault(code, otc)
    ranking = compute_rs_ranking(codes, otc_map=otc_map)
    row = ranking[ranking["code"] == code]
    if row.empty:
        return None, ranking
    return row.iloc[0].to_dict(), ranking


def fetch_benchmark_history(period: str = "2mo") -> pd.DataFrame:
    """抓大盤加權指數(^TWII)歷史資料,獨立成一個函式是因為掃描整個股票池時,大盤資料
    只需要抓一次、給所有股票共用,不用每一檔股票各自抓一次(浪費請求)。"""
    return yf.Ticker(RS_BENCHMARK_SYMBOL).history(period=period)


def compute_rs_vs_benchmark(code: str, otc: bool = False, window_days: int = RS_BENCHMARK_WINDOW_DAYS, benchmark_hist: pd.DataFrame | None = None):
    """個股近 window_days 個交易日累計報酬率,減去加權指數(^TWII)同期累計報酬率。

    **這跟 `compute_rs_ranking()`/`get_rs_rating_for_code()` 是不同概念**——那組函式算的是
    「在一個股票池裡排百分位名次」(1~99),這個函式算的是「跟大盤比的絕對強弱值」,正值代表
    這段期間漲幅贏大盤、負值代表輸大盤,是會在 0 附近震盪的數字,不是排名。

    `^TWII` 是 yfinance 的加權指數代號,不是一般股票代號,不能走 `fetch_data.to_yf_symbol()`
    的 .TW/.TWO 後綴邏輯,所以用 `fetch_benchmark_history()` 直接抓,不透過 `fetch_data.get_history()`。

    benchmark_hist 可選——批次掃描一批股票時,呼叫端應該自己呼叫一次 `fetch_benchmark_history()`
    再傳進來給每一檔股票共用,不要讓這個函式每次都重抓大盤(單股查詢不傳的話,會自動抓一次)。

    資料不足(例如新股剛上市不到 window_days 個交易日)回傳 None。
    """
    stock_hist = get_history(code, otc=otc, period="2mo")
    bench_hist = benchmark_hist if benchmark_hist is not None else fetch_benchmark_history()

    if len(stock_hist) <= window_days or len(bench_hist) <= window_days:
        return None

    stock_return = float(stock_hist["Close"].iloc[-1] / stock_hist["Close"].iloc[-1 - window_days] - 1)
    bench_return = float(bench_hist["Close"].iloc[-1] / bench_hist["Close"].iloc[-1 - window_days] - 1)

    return {
        "rs_value": stock_return - bench_return,
        "stock_return": stock_return,
        "bench_return": bench_return,
        "window_days": window_days,
    }


if __name__ == "__main__":
    from xq_watchlist import get_stock_futures_codes_from_watchlists

    universe = sorted(get_stock_futures_codes_from_watchlists())
    print(f"股票池共 {len(universe)} 檔,開始計算 RS 排行(第一次跑會比較久,要抓每檔股票 14 個月資料)...")

    def _progress(done, total):
        if done % 20 == 0 or done == total:
            print(f"  已計算 {done}/{total} 檔")

    ranking = compute_rs_ranking(universe, progress_callback=_progress)
    print(f"\n算出 RS 排行的股票數:{len(ranking)} / {len(universe)}")
    print(ranking[["code", "rs_rating", "raw_score", "r3m", "r6m", "r9m", "r12m"]].head(20))
