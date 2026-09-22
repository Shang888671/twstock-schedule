"""外資臺指選擇權合成多空部位——用TAIFEX官方「三大法人-選擇權買賣權分計」報表,
換算外資及陸資在臺指選擇權(TXO)的未平倉合成多単/合成空単/多空淨額。

概念來自使用者截圖看到的WantGoo玩股網「外資選擇權合成部位」圖表,但這裡不是去抓
WantGoo網站(第三方衍生資料,算法不透明、也不確定跟這裡的商品範圍是否一致),而是直接
用期交所官方原始資料(交易資訊-三大法人-選擇權買賣權分計-依日期,
https://www.taifex.com.tw/cht/3/callsAndPutsDate)自己算,算法是業界通用的
「選擇權轉換成期貨等值部位」公式,不是自己發明的門檻:

    合成多単(近似做多台指的曝險) = 買進CALL未平倉口數 + 賣出PUT未平倉口數
    合成空単(近似做空台指的曝險) = 賣出CALL未平倉口數 + 買進PUT未平倉口數
    多空淨額 = 合成多単 - 合成空単

買call ≈ 看漲、賣put ≈ 承擔下跌義務(也是偏多倉),兩者delta同方向,加總近似
「合成做多」;反之賣call+買put近似「合成做空」。金額(千元)欄位用同一套公式另外算一次。

**這份資料源史料公開下載端點本身就支援日期範圍查詢**(`callsAndPutsDateDown`),跟這個
repo其他法人/權證/融資資料源(institutional_flow只能每天累積、margin_balance/
warrant_*完全沒有歷史API)不同——backfill一次可以拿到完整歷史,不用等好幾個月慢慢
累積才有樣本可以回測。實測2024/01~2026/09(658個交易日)單次查詢約1.4MB,正常回傳,
這裡分年请求是為了穩妥(避免單次請求範圍太大被伺服器拒絕或逾時),不是因為端點本身
有限制。

用法：
    python foreign_option_position.py --backfill 2018-01-01   # 一次補完整段歷史
    python foreign_option_position.py --update                # 每日排程用,只補最新缺的幾天
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from database import init_db, upsert_foreign_option_position, get_foreign_option_position

DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/callsAndPutsDateDown"
PRODUCT_NAME = "臺指選擇權"
IDENTITY_NAME = "外資及陸資"

# 政府資料開放平臺說這份報表(區分買賣權)官方資料庫最早到2008/04/07,但實測發現這個
# 網頁查詢工具(callsAndPutsDateDown)本身另外限制只能查最近約3年——2026/09/21測試,
# 查詢區間起點早於2023/09/18左右就直接回傳錯誤頁(不是空結果,是`DateTime error`的
# HTML)。這3年限制看起來是跟著「今天」滾動的,不是寫死的日期。要拿到更久以前的歷史,
# 需要另外找期交所的整批下載/開放資料集,這個模組目前沒做,先用查詢工具能給的範圍。
# backfill_history()遇到這個錯誤只會跳過那年、繼續抓更近的年份,不會整個中斷。
EARLIEST_AVAILABLE_DATE = date(2008, 4, 7)

CSV_SNAPSHOT_PATH = Path(__file__).parent / "branch_history" / "foreign_option_position_history.csv"

_RAW_COLUMNS = [
    "date", "product", "option_type", "identity",
    "buy_lots", "buy_value", "sell_lots", "sell_value",
    "net_lots", "net_value",
    "oi_buy_lots", "oi_buy_value", "oi_sell_lots", "oi_sell_value",
    "oi_net_lots", "oi_net_value",
]


class QueryRangeUnavailable(Exception):
    """查詢區間超出期交所這個網頁查詢工具能給的範圍(見上面EARLIEST_AVAILABLE_DATE的
    說明)——伺服器不是回傳空結果,是回傳一個帶錯誤訊息的HTML頁,原本直接拿去切
    逗號會炸成一堆亂欄位(AssertionError: N columns passed, passed data had 1 column),
    這裡先檢查、丟出清楚的例外,呼叫端(backfill_history)接住後跳過該年繼續處理。"""


def _fetch_range_raw(start: date, end: date) -> pd.DataFrame:
    """打期交所「選擇權買賣權分計-依日期」下載端點,回傳這段區間全部商品/身份別的原始資料。"""
    resp = requests.get(
        DOWNLOAD_URL,
        params={
            "queryStartDate": start.strftime("%Y/%m/%d"),
            "queryEndDate": end.strftime("%Y/%m/%d"),
            "commodityId": "",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.content.decode("big5", errors="replace")
    if text.lstrip().startswith("<!DOCTYPE") or "<html" in text[:200].lower():
        raise QueryRangeUnavailable(f"{start} ~ {end} 查詢失敗(期交所回傳錯誤頁,通常代表超出查詢工具可查範圍)")
    lines = [l for l in text.split("\r\n") if l.strip()]
    if len(lines) <= 1:
        return pd.DataFrame(columns=_RAW_COLUMNS)
    rows = [l.split(",") for l in lines[1:]]
    return pd.DataFrame(rows, columns=_RAW_COLUMNS)


def _fetch_range_safe(start: date, end: date, max_backoff_days: int = 6) -> pd.DataFrame:
    """跟_fetch_range_raw一樣,但這份報表是每個交易日收盤後才發布,`end`如果落在還沒
    發布資料的區間(週末/今天盤中/連假)會被期交所回傳錯誤頁——實測2026-09-21(週一)
    查詢到2026-09-20/09-21都失敗,查到09-18(上週五)才成功。這裡不用自己維護一份台股
    交易日曆,直接把end往前一天一天重試,最多試max_backoff_days天(正常連假不會超過
    這個天數),找到「目前查得到資料的最後一天」。如果是start太舊導致的失敗(超出查詢
    工具可回溯的範圍),往前試到start還是會一路失敗,交給呼叫端(backfill_history)接住。"""
    attempt_end = end
    for _ in range(max_backoff_days + 1):
        if attempt_end < start:
            break
        try:
            return _fetch_range_raw(start, attempt_end)
        except QueryRangeUnavailable:
            attempt_end -= timedelta(days=1)
    raise QueryRangeUnavailable(f"{start} ~ {end} 往前重試{max_backoff_days}天都失敗")


def _compute_synthetic(raw: pd.DataFrame) -> pd.DataFrame:
    """篩選臺指選擇權+外資及陸資,把CALL/PUT兩列轉成每天一列的合成多空部位。"""
    if raw.empty:
        return pd.DataFrame(columns=[
            "date", "synthetic_long_lots", "synthetic_short_lots", "net_lots",
            "synthetic_long_value", "synthetic_short_value", "net_value",
        ])

    df = raw[(raw["product"] == PRODUCT_NAME) & (raw["identity"] == IDENTITY_NAME)].copy()
    for col in ["oi_buy_lots", "oi_buy_value", "oi_sell_lots", "oi_sell_value"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    calls = df[df["option_type"] == "CALL"].set_index("date")
    puts = df[df["option_type"] == "PUT"].set_index("date")
    common_dates = calls.index.intersection(puts.index)
    calls, puts = calls.loc[common_dates], puts.loc[common_dates]

    out = pd.DataFrame(index=common_dates)
    out["synthetic_long_lots"] = calls["oi_buy_lots"] + puts["oi_sell_lots"]
    out["synthetic_short_lots"] = calls["oi_sell_lots"] + puts["oi_buy_lots"]
    out["net_lots"] = out["synthetic_long_lots"] - out["synthetic_short_lots"]
    out["synthetic_long_value"] = calls["oi_buy_value"] + puts["oi_sell_value"]
    out["synthetic_short_value"] = calls["oi_sell_value"] + puts["oi_buy_value"]
    out["net_value"] = out["synthetic_long_value"] - out["synthetic_short_value"]
    out.index.name = "date"
    out = out.reset_index()
    out["date"] = pd.to_datetime(out["date"], format="%Y/%m/%d")
    return out.sort_values("date")


def backfill_history(start: date = EARLIEST_AVAILABLE_DATE, end: date | None = None) -> pd.DataFrame:
    """一次補完整段歷史,分年请求(見檔頭說明,穩妥考量不是端點限制)。"""
    end = end or date.today()
    init_db()
    frames = []
    year_start = start
    while year_start <= end:
        year_end = min(date(year_start.year, 12, 31), end)
        print(f"[foreign_option_position] 抓取 {year_start} ~ {year_end}...")
        try:
            raw = _fetch_range_safe(year_start, year_end)
        except QueryRangeUnavailable as e:
            print(f"[foreign_option_position]   -> 跳過({e})")
            year_start = date(year_start.year + 1, 1, 1)
            continue
        computed = _compute_synthetic(raw)
        print(f"[foreign_option_position]   -> {len(computed)} 個交易日")
        if not computed.empty:
            frames.append(computed)
            upsert_foreign_option_position(computed)
        year_start = date(year_start.year + 1, 1, 1)

    if not frames:
        return pd.DataFrame()
    history = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["date"]).sort_values("date")
    save_csv_snapshot(get_history())
    return history


def update_latest() -> int:
    """每日排程用:只補資料庫最後一筆之後到今天的新資料,不用重跑整段歷史。回傳新增天數。"""
    init_db()
    existing = get_foreign_option_position()
    last_date = existing["date"].max().date() if not existing.empty else EARLIEST_AVAILABLE_DATE - timedelta(days=1)
    start = last_date + timedelta(days=1)
    today = date.today()
    if start > today:
        return 0
    raw = _fetch_range_safe(start, today)
    computed = _compute_synthetic(raw)
    if computed.empty:
        return 0
    upsert_foreign_option_position(computed)
    save_csv_snapshot(get_history())
    return len(computed)


def get_history() -> pd.DataFrame:
    """優先讀SQLite(本機/fly.io有完整累積歷史時),查無資料退回git-tracked的CSV快照
    (Streamlit Cloud沒有SQLite本機檔案,只能讀部署下來的CSV)——跟branch_win_rate.py
    同一套備援邏輯。"""
    try:
        init_db()
        df = get_foreign_option_position()
        if not df.empty:
            return df
    except Exception:
        pass
    return load_csv_snapshot()


def load_csv_snapshot() -> pd.DataFrame:
    if not CSV_SNAPSHOT_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(CSV_SNAPSHOT_PATH, parse_dates=["date"])


def save_csv_snapshot(history: pd.DataFrame) -> None:
    CSV_SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
    history.to_csv(CSV_SNAPSHOT_PATH, index=False)
    print(f"[foreign_option_position] 快照已存到 {CSV_SNAPSHOT_PATH}({len(history)} 筆)")


def classify_current_reading(history: pd.DataFrame | None = None) -> dict | None:
    """把最新一天的net_ratio(合成多空淨額佔合成多空總量的比例)分類成多/空,給app.py
    的判斷分析文案用。

    **多空分界線用0軸,不是歷史中位數或百分位**——2026-09-21測過兩種二分法
    (`backtest_foreign_option_position.py`,658個交易日),0軸(net_ratio>=0算多)
    比歷史中位數平分(net_ratio>=中位數算多)統計上更顯著、也更直覺(正值本來就代表
    外資淨部位是真的偏多頭曝險,不用去記一個會隨資料累積慢慢飄動的歷史中位數):

    | 分法 | 20日窗格 | 10日窗格 | 5日窗格 |
    |---|---|---|---|
    | 0軸 | 多89.6% vs 空64.0%,p=7e-10 | 多77.6% vs 空61.2%,p=2.9e-4 | 多70.8% vs 空58.4%,p=6.6e-3 |
    | 歷史中位數 | 多84.5% vs 空62.9%,p=3.5e-6 | 多76.0% vs 空58.8%,p=4.7e-4 | 多69.0% vs 空57.2%,p=1.5e-2 |

    歷史百分位數字還是一併回傳,當輔助資訊用(告訴你目前是「偏多裡面算強的還是弱的」),
    但不是拿來決定多/空這個主標籤。

    **重要但書**(適用於上面所有數字):回測期間剛好落在一段整體偏多頭的期間(這3個
    窗格的母體基準勝率本身就有63~74%,遠高於「隨機」的50%),測的又是加權指數本身的
    原始報酬(不是像這個repo其他回測那樣拿「超額報酬 vs 大盤」當作對照組——這裡沒有
    更高階的大盤可以當基準,^TWII自己就是被測的對象),沒辦法排除「這個關係只是剛好
    跟這段多頭走勢同步、換成空頭或盤整期不一定成立」的可能。呼叫端顯示這個分類時
    **必須**一起帶上這個但書,不能只講勝率數字。"""
    if history is None:
        history = get_history()
    if history.empty:
        return None

    history = history.sort_values("date").copy()
    history["net_ratio"] = history["net_lots"] / (history["synthetic_long_lots"] + history["synthetic_short_lots"])
    latest = history.iloc[-1]
    ratio = latest["net_ratio"]
    percentile = float((history["net_ratio"] < ratio).mean() * 100)

    if ratio >= 0:
        label, tone = "偏多", "bull"
    else:
        label, tone = "偏空", "bear"

    return {
        "date": latest["date"],
        "net_ratio": float(ratio),
        "percentile": percentile,
        "label": label,
        "tone": tone,
        "synthetic_long_lots": float(latest["synthetic_long_lots"]),
        "synthetic_short_lots": float(latest["synthetic_short_lots"]),
        "net_lots": float(latest["net_lots"]),
    }


def main():
    parser = argparse.ArgumentParser(description="外資臺指選擇權合成多空部位")
    parser.add_argument("--backfill", type=str, default=None, help="從指定日期(YYYY-MM-DD)開始補完整段歷史")
    parser.add_argument("--update", action="store_true", help="只補最新缺的幾天(每日排程用)")
    args = parser.parse_args()

    if args.backfill:
        start = date.fromisoformat(args.backfill)
        history = backfill_history(start=start)
        print(f"\n共 {len(history)} 個交易日")
        print(history.tail(5).to_string(index=False))
    elif args.update:
        n = update_latest()
        print(f"新增 {n} 個交易日")
    else:
        history = get_history()
        print(f"目前已累積 {len(history)} 個交易日" if not history.empty else "尚未累積任何資料")


if __name__ == "__main__":
    main()
