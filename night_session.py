"""台股期貨(TX)夜盤(盤後交易時段)成交量——反映台灣資金自己的參與度/信心強度。

跟 `us_market.py`(美股夜盤連動指標)是不同角度:那個看的是「美股期貨/現貨隔天對台股的
連動方向」,這個看的是「台灣資金自己在夜盤的參與熱度」,純粹是量能強弱,**不判斷多空方向**
——量大不代表偏多或偏空,只代表當晚交易熱絡、資金關注度高;量小代表觀望氣氛濃厚。跟
`signals.py` 的「成交量增3日均量」條件也是不同層次:那個是「日盤」個股成交量,這個是「夜盤」
台指期貨(TX)全市場層級的成交量。

資料來源:TAIFEX(臺灣期貨交易所)「期貨每日交易行情查詢」的 Excel 匯出連結
(`futDailyMarketExcel`,`marketCode=1` 是盤後/夜盤時段),已實測 `queryDate` 參數真的
能查任意過去交易日(不是像融資融券那樣只能拿到最新一天,見 margin_data.py 的教訓——這次
先用不同日期各查一次、比對合約月份/數字有沒有跟著變,確認參數真的有效才開始寫模組)。
非交易日(假日)查詢會查無資料,回傳 None,呼叫端優雅跳過。

**編碼問題**:這個頁面宣稱 UTF-8 編碼,但實際回傳的中文欄位標題不管用 UTF-8 還是 Big5
解碼都是亂碼(判斷是 TAIFEX 網站本身的編碼設定問題,不是我們這邊的問題)。合約代碼
("TX")跟所有數字欄位都是 ASCII,不受影響,所以底下改用「欄位位置」而不是欄位名稱字串
取值,不依賴中文標題能不能正確解碼。
"""

import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_PATH = CACHE_DIR / "tx_night_session.csv"

NIGHT_SESSION_URL = "https://www.taifex.com.tw/cht/3/futDailyMarketExcel"

# 欄位位置(0-indexed):契約代碼, 到期月份, 開盤, 最高, 最低, 最後成交價, 漲跌價, 漲跌%,
# 成交量, 結算價, 未沖銷契約量, 最後最佳買價, 最後最佳賣價, 歷史最高, 歷史最低
COL_CONTRACT = 0
COL_YEARMONTH = 1
COL_CHANGE_PCT = 7
COL_VOLUME = 8

LOOKBACK_DAYS_DEFAULT = 20
STRENGTH_STRONG_RATIO = 1.5  # 比照 us_market.py 的「跟自己近期比」設計,同一組門檻倍數
STRENGTH_WEAK_RATIO = 0.5


def _parse_pct(s) -> float | None:
    """從「▲-0.48%」這種帶方向符號的字串抓出數字部分——方向符號本身是亂碼,但正負號已經
    包含在數字裡(例如"-0.48"本身就是負的),不需要另外判斷方向符號。"""
    match = re.search(r"-?\d+\.?\d*", str(s))
    return float(match.group()) if match else None


def _fetch_night_session_day(date_str: str) -> dict | None:
    """抓 TX 單一天的夜盤(盤後交易時段)資料。

    date_str: "YYYY/MM/DD"。回傳 {"volume": int(全部合約月份加總成交量,口),
    "front_month_change_pct": float|None(最近到期月份合約當晚漲跌%)}。
    非交易日/抓取失敗回傳 None。
    """
    params = {"queryType": "2", "marketCode": "1", "commodity_id": "TX", "queryDate": date_str}
    try:
        resp = requests.get(NIGHT_SESSION_URL, params=params, headers=HEADERS, timeout=15)
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return None
    if not tables:
        return None
    df = tables[0]
    tx_rows = df[df.iloc[:, COL_CONTRACT].astype(str).str.strip() == "TX"]
    if tx_rows.empty:
        return None

    volume = pd.to_numeric(tx_rows.iloc[:, COL_VOLUME], errors="coerce").fillna(0).sum()
    front_month = tx_rows.sort_values(tx_rows.columns[COL_YEARMONTH]).iloc[0]
    return {"volume": int(volume), "front_month_change_pct": _parse_pct(front_month.iloc[COL_CHANGE_PCT])}


def get_night_session_history(lookback_days: int = LOOKBACK_DAYS_DEFAULT, sleep: float = 0.3) -> pd.DataFrame:
    """取得近 lookback_days 個交易日的台指期夜盤資料,本地逐日快取(`data_cache/tx_night_session.csv`),
    重複呼叫不會重打已經快取過的日期——這個資料源(跟 margin_data.py 不同)真的支援任意過去
    日期查詢,所以第一次使用就能立刻回填完整的 lookback_days 天,不用像融資融券那樣慢慢累積。

    回傳欄位:volume(口)、front_month_change_pct(%)。
    """
    cached = pd.read_csv(CACHE_PATH, index_col=0, parse_dates=True) if CACHE_PATH.exists() else pd.DataFrame()

    today = pd.Timestamp.today().normalize()
    # 抓寬一點(lookback_days 的 1.5 倍)再取尾巴 lookback_days 筆,扣掉週末/國定假日後才夠數。
    candidate_dates = pd.bdate_range(end=today, periods=int(lookback_days * 1.5) + 5)
    missing = [d for d in candidate_dates if cached.empty or d not in cached.index]

    rows = {}
    for d in missing:
        result = _fetch_night_session_day(d.strftime("%Y/%m/%d"))
        if result is not None:
            rows[d] = [result["volume"], result["front_month_change_pct"]]
        time.sleep(sleep)

    if rows:
        new_df = pd.DataFrame.from_dict(rows, orient="index", columns=["volume", "front_month_change_pct"])
        combined = pd.concat([cached, new_df]) if not cached.empty else new_df
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined.to_csv(CACHE_PATH)
    else:
        combined = cached

    return combined.tail(lookback_days) if not combined.empty else combined


def compute_night_session_strength(lookback_days: int = LOOKBACK_DAYS_DEFAULT) -> dict | None:
    """最近一個交易日的夜盤成交量,對比「今天以前」近 lookback_days 天的均量,算出參與度
    強弱標記——**這是「參與度/信心」強弱,不是多空方向**,量大只代表交易熱絡,不代表偏多
    或偏空。強弱門檻(1.5倍/0.5倍)比照 `us_market.py` 的既有設計,同樣是主觀訂的,不是
    統計驗證過的數字。

    回傳 {"date", "volume", "avg_volume", "ratio", "strength"("熱絡"/"普通"/"清淡"),
    "front_month_change_pct"}。資料不足(少於2天)時回傳 None。
    """
    hist = get_night_session_history(lookback_days=lookback_days + 1)
    if len(hist) < 2:
        return None

    latest = hist.iloc[-1]
    baseline = hist["volume"].iloc[:-1]  # 不含最新一天,理由跟 us_market.py 的強弱基準一致
    avg_volume = baseline.mean()
    ratio = latest["volume"] / avg_volume if avg_volume > 0 else None

    if ratio is None:
        strength = None
    elif ratio >= STRENGTH_STRONG_RATIO:
        strength = "熱絡"
    elif ratio <= STRENGTH_WEAK_RATIO:
        strength = "清淡"
    else:
        strength = "普通"

    return {
        "date": hist.index[-1].strftime("%Y-%m-%d"),
        "volume": int(latest["volume"]),
        "avg_volume": avg_volume,
        "ratio": ratio,
        "strength": strength,
        "front_month_change_pct": latest["front_month_change_pct"],
    }


if __name__ == "__main__":
    print("=== 台指期(TX)夜盤參與度 ===")
    result = compute_night_session_strength()
    if result:
        print(f"日期:{result['date']}")
        print(f"夜盤成交量:{result['volume']:,} 口")
        print(f"近期均量:{result['avg_volume']:,.0f} 口")
        print(f"量能比:{result['ratio']:.2f}倍 → {result['strength']}")
        print(f"近月合約當晚漲跌:{result['front_month_change_pct']}%")
    else:
        print("資料不足")
