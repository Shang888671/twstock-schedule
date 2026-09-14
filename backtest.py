"""回測 `signals.py` 的「三大法人 + 個股權證」做多訊號,驗證實際隔日勝率/平均報酬。

只回測 `signals.py` 的前兩個條件(法人、權證)——第三個條件(已知大戶分點)沒有歷史資料
可回測,那份資料是使用者手動用 XQ 匯出的「當下快照」,TWSE 官方分點系統有 CAPTCHA 擋自動
查詢,無法回溯歷史。

門檻常數直接從 `signals.py` import,確保回測用的規則跟網頁上「檢查做多訊號」按鈕用的是
同一套定義,不會兩邊各存一份、之後改一邊忘了改另一邊。

兩種「隔日報酬」定義:
- forward_return_close:收盤買、隔日收盤賣(標準隔日報酬率)。
- forward_return_open_close:隔日開盤買、隔日收盤賣,對應使用者原始理論
  (「大戶隔日早盤拉高出貨」,這個時間點更貼近實際操作)。

**沒有計入手續費/證交稅/滑價**,回傳的是毛報酬,實際淨報酬會更低。
"""

import pandas as pd

from chip_data import get_institutional_flow_multi, get_warrant_large_trade_counts_multi
from fetch_data import get_history
from signals import (
    INSTITUTIONAL_WINDOW_DAYS,
    INSTITUTIONAL_POSITIVE_RATIO_THRESHOLD,
    WARRANT_SINGLE_TRADE_THRESHOLD,
    WARRANT_LARGE_TRADE_MIN_COUNT,
)


def evaluate_signal_series(code: str, price_df: pd.DataFrame, institutional_df: pd.DataFrame, warrant_count_df: pd.DataFrame) -> pd.DataFrame:
    """給定已經抓好的價格/法人/權證資料,逐日算出訊號與隔日報酬。純函式,不做任何網路請求。"""
    if price_df.empty:
        return pd.DataFrame()

    price = price_df.copy()
    idx = price.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    price.index = idx.normalize()
    price = price[~price.index.duplicated(keep="last")].sort_index()

    total_net = institutional_df["total_net"] if not institutional_df.empty else pd.Series(dtype=float)
    warrant_count = warrant_count_df["count"] if not warrant_count_df.empty else pd.Series(dtype=float)

    dates = price.index
    rows = []
    for i in range(len(dates) - 1):  # 最後一天沒有「隔一天」可以算 forward return,跳過
        d = dates[i]
        next_d = dates[i + 1]

        window = total_net[total_net.index <= d].tail(INSTITUTIONAL_WINDOW_DAYS)
        inst_days = len(window)
        inst_positive = int((window > 0).sum()) if inst_days else 0
        inst_ratio = (inst_positive / inst_days) if inst_days else 0.0
        inst_signal = inst_ratio >= INSTITUTIONAL_POSITIVE_RATIO_THRESHOLD

        wc = int(warrant_count.get(d, 0)) if not warrant_count.empty else 0
        warrant_signal = wc >= WARRANT_LARGE_TRADE_MIN_COUNT

        close_t = price.loc[d, "Close"]
        close_t1 = price.loc[next_d, "Close"]
        open_t1 = price.loc[next_d, "Open"]

        rows.append(
            {
                "code": code,
                "date": d,
                "institutional_days": inst_days,
                "institutional_ratio": inst_ratio,
                "institutional_signal": inst_signal,
                "warrant_count": wc,
                "warrant_signal": warrant_signal,
                "signal": inst_signal and warrant_signal,
                "forward_return_close": (close_t1 / close_t - 1) if close_t else None,
                "forward_return_open_close": (close_t1 / open_t1 - 1) if open_t1 else None,
            }
        )

    return pd.DataFrame(rows)


def backtest_universe(codes, start_date: str, end_date: str, sleep: float = 0.3, progress_callback=None, otc_map: dict | None = None):
    """對一批股票、一段區間回測訊號。

    codes 裡的股票統一當作上市股票處理(otc_map 可選擇性覆寫個別代號),跟 RS 排行分頁
    的既有簡化一致——上櫃股票的法人資料本來就抓不到(T86 只支援上市),訊號會自然不成立,
    不會拋錯,只是那檔股票不會出現在「訊號觸發」的結果裡。

    progress_callback(stage, done, total) 可選——分三個階段回報進度("法人資料"/"權證資料"
    是逐日打 API 的兩個慢階段,天數才是真正的瓶頸,跟股票池大小基本無關;"組裝股價" 是最後
    逐股票讀價格+算訊號的階段,比較快)。

    回傳 (signal_days_df, all_trading_days_df):
    - signal_days_df:所有股票池、所有「訊號成立」的交易日,含隔日報酬。
    - all_trading_days_df:同樣股票池/區間「所有」交易日(不篩訊號),當作基準線比較用。
    """
    codes = list(dict.fromkeys(codes))
    otc_map = otc_map or {}

    # 法人資料往前多抓一段,讓區間開頭當天的「近10天」滾動窗口也有足夠資料可看
    padded_start = (pd.Timestamp(start_date) - pd.Timedelta(days=INSTITUTIONAL_WINDOW_DAYS * 3)).strftime("%Y-%m-%d")

    institutional_multi = get_institutional_flow_multi(
        codes, padded_start, end_date, sleep=sleep,
        progress_callback=(lambda done, total: progress_callback("法人資料", done, total)) if progress_callback else None,
    )
    warrant_multi = get_warrant_large_trade_counts_multi(
        codes, start_date, end_date, WARRANT_SINGLE_TRADE_THRESHOLD, sleep=sleep,
        progress_callback=(lambda done, total: progress_callback("權證資料", done, total)) if progress_callback else None,
    )

    all_rows = []
    for i, code in enumerate(codes):
        hist = get_history(code, otc=otc_map.get(code, False), start=padded_start, end=end_date)
        if not hist.empty:
            series = evaluate_signal_series(
                code, hist, institutional_multi.get(code, pd.DataFrame()), warrant_multi.get(code, pd.DataFrame())
            )
            if not series.empty:
                series = series[series["date"] >= pd.Timestamp(start_date)]
                all_rows.append(series)
        if progress_callback:
            progress_callback("組裝股價", i + 1, len(codes))

    if not all_rows:
        empty = pd.DataFrame()
        return empty, empty

    all_days = pd.concat(all_rows, ignore_index=True).dropna(subset=["forward_return_close"])
    signal_days = all_days[all_days["signal"]].reset_index(drop=True)
    return signal_days, all_days.reset_index(drop=True)


def _stats(df: pd.DataFrame, col: str) -> dict:
    if df.empty:
        return {"count": 0, "win_rate": None, "avg_return": None}
    wins = int((df[col] > 0).sum())
    return {"count": len(df), "win_rate": wins / len(df), "avg_return": float(df[col].mean())}


def summarize_backtest(signal_days: pd.DataFrame, all_days: pd.DataFrame) -> dict:
    """算出訊號的勝率/平均報酬,並對照「不篩訊號、股票池全部交易日」的基準線。"""
    return {
        "signal_close": _stats(signal_days, "forward_return_close"),
        "signal_open_close": _stats(signal_days, "forward_return_open_close"),
        "baseline_close": _stats(all_days, "forward_return_close"),
        "baseline_open_close": _stats(all_days, "forward_return_open_close"),
    }


if __name__ == "__main__":
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=60)
    codes = ["2330", "2317", "2454"]

    print(f"回測 {codes},區間 {start} ~ {end}...")

    def _progress(stage, done, total):
        print(f"  [{stage}] {done}/{total}")

    signal_days, all_days = backtest_universe(codes, start.isoformat(), end.isoformat(), progress_callback=_progress)
    print(f"\n全部交易日樣本數:{len(all_days)},訊號觸發樣本數:{len(signal_days)}")
    if not signal_days.empty:
        print(signal_days[["code", "date", "institutional_ratio", "warrant_count", "forward_return_close", "forward_return_open_close"]])

    summary = summarize_backtest(signal_days, all_days)
    for k, v in summary.items():
        print(k, ":", v)
