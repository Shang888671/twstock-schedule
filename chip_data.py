"""三大法人籌碼(個股)與個股權證籌碼資料抓取。

資料來源:
- 三大法人買賣超:TWSE「三大法人買賣超日報」(T86),每次查詢回傳「單一交易日、全市場」的資料,
  沒有單一股票的歷史區間 API,所以要逐日查詢再篩出目標股票、並在本地快取避免重複抓取。
- 個股權證籌碼:TWSE「每日收盤行情」(MI_INDEX,type=0999 認購權證 / type=0999P 認售權證),
  同樣是「單一交易日、全市場」的快照(每天約 3 萬多檔權證掛牌),沒有單一標的的歷史區間 API,
  所以只逐日抓最近幾天、依「標的代號」篩出目標股票對應的所有權證再加總成交量。

這兩個資料源都沒有官方 API 文件,是直接解析網站既有的查詢頁面,行為可能隨網站改版而失效。

(先前版本原本還有台指選擇權(TXO)與個股選擇權籌碼,使用者已要求全部移除,
改用本檔案的個股權證籌碼取代——只看近 2 天當參考快照,不納入模型訓練特徵。)
"""

import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"


def _to_int(s: str) -> int:
    s = s.strip().replace(",", "")
    if not s or s in ("--", "-"):
        return 0
    return int(s)


def get_institutional_flow(code: str, start_date: str, end_date: str, sleep: float = 0.3) -> pd.DataFrame:
    """取得個股「三大法人買賣超」逐日資料(股數)。

    start_date/end_date: "YYYY-MM-DD"
    只支援上市股票(TWSE T86)。回傳欄位:foreign_net, trust_net, dealer_net, total_net
    """
    cache_path = CACHE_DIR / f"institutional_{code}.csv"
    cached = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing = [d for d in dates if cached.empty or d not in cached.index]

    rows = {}
    for d in missing:
        date_str = d.strftime("%Y%m%d")
        try:
            resp = requests.get(T86_URL, params={"date": date_str, "selectType": "ALL", "response": "json"}, headers=HEADERS, timeout=10)
            payload = resp.json()
        except Exception:
            continue
        if payload.get("stat") != "OK" or not payload.get("data"):
            continue
        for row in payload["data"]:
            if row[0].strip() == code:
                foreign_net = _to_int(row[4]) + _to_int(row[7])
                trust_net = _to_int(row[10])
                dealer_net = _to_int(row[11])
                total_net = _to_int(row[18])
                rows[d] = [foreign_net, trust_net, dealer_net, total_net]
                break
        time.sleep(sleep)

    if rows:
        new_df = pd.DataFrame.from_dict(
            rows, orient="index", columns=["foreign_net", "trust_net", "dealer_net", "total_net"]
        )
        combined = pd.concat([cached, new_df]) if not cached.empty else new_df
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_csv(cache_path)
    else:
        combined = cached

    result = combined.loc[(combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))]
    return result.sort_index()


def get_institutional_flow_multi(codes, start_date: str, end_date: str, sleep: float = 0.3, progress_callback=None) -> dict:
    """跟 `get_institutional_flow` 抓一樣的資料,但一次抓「一批股票」而不是一檔。

    T86 每次查詢本來就回傳「單一交易日、全市場」的完整資料——`get_institutional_flow`
    只留自己要的那一檔、把其他列全部丟掉,如果要對一批股票各自查一次會浪費大量重複請求
    (同一天的資料被重複抓 N 次)。這個函式改成「逐日打一次 T86,一次解析出 codes 裡
    每一檔股票當天的數字」,不管 codes 有幾檔,總請求數只跟天數成正比。

    會直接寫回跟 `get_institutional_flow` 相同格式的每股快取檔(`data_cache/institutional_<code>.csv`),
    所以跑過這個函式之後,單股查詢一樣會吃到快取,兩邊互惠。

    progress_callback(done, total) 可選,逐日回報抓取進度(total 是「還沒有快取、需要打 API」
    的天數,不是整個區間的天數)——UI 用這個而不是「股票數」當進度基準,因為不管 codes 有幾檔,
    真正花時間的瓶頸都是「天數」。

    回傳:{code: DataFrame}(欄位跟 `get_institutional_flow` 相同)
    """
    codes = set(codes)
    caches = {}
    for code in codes:
        cache_path = CACHE_DIR / f"institutional_{code}.csv"
        caches[code] = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing_dates = [
        d for d in dates
        if any(caches[c].empty or d not in caches[c].index for c in codes)
    ]

    new_rows = {code: {} for code in codes}
    for i, d in enumerate(missing_dates):
        date_str = d.strftime("%Y%m%d")
        try:
            resp = requests.get(T86_URL, params={"date": date_str, "selectType": "ALL", "response": "json"}, headers=HEADERS, timeout=10)
            payload = resp.json()
        except Exception:
            payload = {}
        if payload.get("stat") == "OK" and payload.get("data"):
            for row in payload["data"]:
                row_code = row[0].strip()
                if row_code in codes:
                    foreign_net = _to_int(row[4]) + _to_int(row[7])
                    trust_net = _to_int(row[10])
                    dealer_net = _to_int(row[11])
                    total_net = _to_int(row[18])
                    new_rows[row_code][d] = [foreign_net, trust_net, dealer_net, total_net]
        if progress_callback:
            progress_callback(i + 1, len(missing_dates))
        time.sleep(sleep)

    result = {}
    for code in codes:
        cached = caches[code]
        if new_rows[code]:
            new_df = pd.DataFrame.from_dict(
                new_rows[code], orient="index", columns=["foreign_net", "trust_net", "dealer_net", "total_net"]
            )
            combined = pd.concat([cached, new_df]) if not cached.empty else new_df
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            combined.to_csv(CACHE_DIR / f"institutional_{code}.csv")
        else:
            combined = cached
        if combined.empty:
            result[code] = combined
        else:
            result[code] = combined.loc[
                (combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))
            ].sort_index()
    return result


def _fetch_warrant_table(date_str: str, put: bool) -> pd.DataFrame:
    """抓 TWSE 單日「全市場」權證收盤行情(認購或認售),回傳原始欄位的 DataFrame。

    date_str: "YYYYMMDD"。這是全市場快照(當天約 3 萬多檔權證),
    要抓哪一檔股票的權證得自己在回傳結果裡用「標的代號」篩選。
    """
    params = {"date": date_str, "type": "0999P" if put else "0999", "response": "json"}
    try:
        resp = requests.get(MI_INDEX_URL, params=params, headers=HEADERS, timeout=15)
        payload = resp.json()
    except Exception:
        return pd.DataFrame()
    if payload.get("stat") != "OK":
        return pd.DataFrame()
    table = next((t for t in payload.get("tables", []) if t.get("data")), None)
    if table is None:
        return pd.DataFrame()
    return pd.DataFrame(table["data"], columns=table["fields"])


def get_stock_call_warrant_detail(code: str, date_str: str):
    """取得某一天『認購權證』對應到指定股票代號的「個別權證」明細,不加總。

    跟 get_warrant_flow() 的差異:get_warrant_flow 把同一檔股票對應的所有認購權證加總成一個數字,
    這個函式保留「每一檔權證各自的成交金額」,用來判斷「單一檔權證當天是否被大買」
    (而不是看全部權證加總後的總額)。

    date_str: "YYYYMMDD"
    回傳:
    - None:代表那天不是交易日或抓取失敗(呼叫端應該往前一天重試)
    - 空 DataFrame:那天有交易,但這檔股票當天沒有任何認購權證成交(視為 0 筆,不用重試)
    - 非空 DataFrame,欄位:warrant_code, warrant_name, volume(成交股數), value(成交金額)
    """
    table = _fetch_warrant_table(date_str, put=False)
    if table.empty:
        return None
    match = table[table["標的代號"].astype(str).str.strip() == code].copy()
    if match.empty:
        return pd.DataFrame(columns=["warrant_code", "warrant_name", "volume", "value"])
    match["volume"] = pd.to_numeric(match["成交股數"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    match["value"] = pd.to_numeric(match["成交金額"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    return match.rename(columns={"證券代號": "warrant_code", "證券名稱": "warrant_name"})[
        ["warrant_code", "warrant_name", "volume", "value"]
    ]


def get_warrant_large_trade_counts_multi(codes, start_date: str, end_date: str, threshold: float, sleep: float = 0.3, progress_callback=None) -> dict:
    """一批股票、一段區間,逐日算出「當天單一認購權證成交金額 >= threshold 的檔數」。

    用途是幫 `signals.py` 的「認購權證單日大額筆數」條件做歷史回測——跟
    `get_institutional_flow_multi` 同樣的道理:MI_INDEX 每次查詢回傳的是「單一交易日、
    全市場」約 3 萬多檔權證的完整行情,不能對每一檔股票各自查一次(會重複抓同一天的資料
    N 次),改成逐日打一次、一次算出 codes 裡每一檔股票當天的檔數。

    threshold 由呼叫端傳入(例如 `signals.WARRANT_SINGLE_TRADE_THRESHOLD`),不在這裡
    重複定義門檻常數——如果呼叫端用不同的 threshold 重跑,舊快取會被視為過期重新計算
    (快取檔名內含 threshold,不同門檻不會共用到錯誤的快取)。

    progress_callback(done, total) 可選,逐日回報抓取進度(total 是「還沒有快取、需要打 API」
    的天數),理由同 `get_institutional_flow_multi`。

    回傳:{code: DataFrame}(index 是日期,欄位是 count)
    """
    codes = set(codes)
    threshold_tag = int(threshold)
    caches = {}
    for code in codes:
        cache_path = CACHE_DIR / f"warrant_large_trade_count_{code}_{threshold_tag}.csv"
        caches[code] = pd.read_csv(cache_path, index_col=0, parse_dates=True) if cache_path.exists() else pd.DataFrame()

    dates = pd.bdate_range(start=start_date, end=end_date)
    missing_dates = [
        d for d in dates
        if any(caches[c].empty or d not in caches[c].index for c in codes)
    ]

    new_rows = {code: {} for code in codes}
    for i, d in enumerate(missing_dates):
        date_str = d.strftime("%Y%m%d")
        call_df = _fetch_warrant_table(date_str, put=False)
        if not call_df.empty:
            call_df = call_df.copy()
            call_df["_value"] = pd.to_numeric(call_df["成交金額"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
            call_df["_underlying"] = call_df["標的代號"].astype(str).str.strip()
            for code in codes:
                match = call_df[call_df["_underlying"] == code]
                new_rows[code][d] = int((match["_value"] >= threshold).sum())
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
            combined.to_csv(CACHE_DIR / f"warrant_large_trade_count_{code}_{threshold_tag}.csv")
        else:
            combined = cached
        if combined.empty:
            result[code] = combined
        else:
            result[code] = combined.loc[
                (combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))
            ].sort_index()
    return result


def get_warrant_flow(code: str, start_date: str, end_date: str, sleep: float = 0.3) -> pd.DataFrame:
    """取得個股對應「權證」(認購+認售)逐日成交籌碼(全部履約價/到期月份加總)。

    只建議查近幾天(例如近 2 天)當參考快照——這個資料源沒有單一標的的歷史區間 API,
    每天都要抓「全市場」約 3 萬多檔權證的收盤行情再篩選,逐日查詢成本很高。

    start_date/end_date: "YYYY-MM-DD"
    回傳欄位:warrant_call_volume, warrant_put_volume(成交股數)、
              warrant_call_value, warrant_put_value(成交金額)、
              warrant_vol_pc_ratio(認售/認購成交量比,百分比;認購量為 0 時為 NaN)
    """
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
            # 這天可能休市,或資料尚未更新
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

    if combined.empty:
        return combined
    result = combined.loc[(combined.index >= pd.Timestamp(start_date)) & (combined.index <= pd.Timestamp(end_date))]
    return result.sort_index()


if __name__ == "__main__":
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=90)

    print("=== 三大法人買賣超(2330,近 90 天)===")
    flow = get_institutional_flow("2330", start.isoformat(), end.isoformat())
    print(flow.tail())
    print(f"共 {len(flow)} 筆")

    print("\n=== 個股權證籌碼(2330,近 2 天)===")
    warrant = get_warrant_flow("2330", (end - timedelta(days=2)).isoformat(), end.isoformat())
    print(warrant.tail())
    print(f"共 {len(warrant)} 筆")
