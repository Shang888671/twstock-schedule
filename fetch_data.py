"""抓取台股即時(延遲)報價與歷史資料。

上市股票代號用 .TW 後綴(例如台積電 2330 -> "2330.TW")
上櫃股票代號用 .TWO 後綴(例如 "6488.TWO")
"""

import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
import pandas as pd

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# yfinance 的 info["longName"] 對台股只有英文全名,沒有中文——中文簡稱改用 TWSE 公開的
# ISIN 一覽表(上市/上櫃分開兩個頁面),這是慣用的台股代號->中文名稱對照公開來源,不用驗證碼。
ISIN_LIST_URLS = {
    False: "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",  # 上市
    True: "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",  # 上櫃
}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = 7 * 86400  # 公司中文簡稱幾乎不變,快取 7 天,沿用 stock_futures.py 的慣例
HEADERS = {"User-Agent": "Mozilla/5.0"}

# 櫃買指數(^TWOII)實測發現 yfinance 回傳的資料嚴重過時(fast_info/info 抓到的
# regularMarketTime 對應到 2024-10-11,不是即時資料,數字也因此是錯的),改用櫃買中心
# (TPEx)官方 OpenAPI 直接抓——這個端點只回傳「當月」每日行情,不是任意區間的歷史資料,
# 沒辦法拿來做像加權指數那樣的完整K線圖,只能拿最新一筆當即時報價卡用。
TPEX_INDEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_index"

# 櫃買指數本身沒有「成交量」(指數不是股票,沒有股數),volume_profile.py要的Volume欄位
# 借用「上櫃市場當日成交量值指數」這個端點的TradeVolume(全市場合計量)當代理指標——
# 跟tpex_index一樣只回傳「這個月」的資料,日期格式卻是民國年(例如"1150917"),跟
# tpex_index本身已經是西元年("20260917")不一樣,合併時要注意換算。
TPEX_DAILY_TRADING_INDEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index"
OTC_INDEX_HISTORY_CACHE = CACHE_DIR / "otc_index_history.csv"


def to_yf_symbol(code: str, otc: bool = False) -> str:
    """把純數字股票代號轉成 yfinance 用的代號。

    「^」開頭的是指數代號(例如加權指數 "^TWII"、櫃買指數 "^TWOII"),原樣直接回傳,
    不套用 .TW/.TWO 個股後綴規則——指數不是個股,沒有上市/上櫃之分。
    """
    if code.startswith("^") or code.endswith(".TW") or code.endswith(".TWO"):
        return code
    return f"{code}.TWO" if otc else f"{code}.TW"


def get_quote(code: str, otc: bool = False) -> dict:
    """取得延遲報價(最新一筆價格與基本資訊)。"""
    symbol = to_yf_symbol(code, otc)
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info
    return {
        "symbol": symbol,
        "last_price": info.get("lastPrice"),
        "previous_close": info.get("previousClose"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "volume": info.get("lastVolume"),
    }


def get_tpex_otc_index_quote() -> dict | None:
    """抓櫃買指數(OTC)最新一筆日行情,直接打 TPEx 官方 OpenAPI(不透過 yfinance)。

    回傳格式跟 `get_quote()` 相容(last_price/previous_close/day_high/day_low/volume),
    多一個 `asof`(資料日期,"YYYYMMDD")方便呼叫端顯示「這是哪一天的收盤」而不是誤以為
    是當下即時報價——這個端點是每日更新的收盤行情,不是逐筆即時報價。抓取失敗或當月
    完全沒有資料時回傳 None,呼叫端應該優雅降級。
    """
    try:
        resp = requests.get(TPEX_INDEX_URL, headers=HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return None
    if not data:
        return None
    latest = data[-1]
    try:
        close = float(latest["Close"])
        change = float(latest["Change"])
        return {
            "symbol": "^TWOII",
            "last_price": close,
            "previous_close": close - change,
            "day_high": float(latest["High"]),
            "day_low": float(latest["Low"]),
            "volume": None,
            "asof": latest["Date"],
        }
    except (KeyError, ValueError):
        return None


def get_tpex_otc_index_previous_day() -> dict | None:
    """回傳櫃買指數「上一個完整交易日」的OHLC(不是今天),用來跟今天的即時位階比較——
    昨天收盤時自己是貼近高點還是低點,才看得出「連續好幾天高檔急殺」這種型態,不是只看
    今天一天。

    跟 get_tpex_otc_index_quote() 共用同一個TPEx端點,但這個端點的docstring說是「每日
    更新的收盤行情」——盤中今天還沒收盤時,資料最後一筆可能還是昨天(還沒把今天加進去);
    收盤後資料更新了,最後一筆才會變成今天。所以不能總是假設「最後一筆是今天、倒數第二筆
    是昨天」,要先用日期字串比對「今天在不在清單裡」,今天不在清單裡代表最後一筆本身就是
    「上一個完整交易日」=昨天,今天在清單裡才要往前一筆。
    """
    try:
        resp = requests.get(TPEX_INDEX_URL, headers=HEADERS, timeout=15)
        data = resp.json()
    except Exception:
        return None
    if not data:
        return None

    today_str = datetime.now(_TAIPEI_TZ).strftime("%Y%m%d")
    dates = [d.get("Date") for d in data]
    if today_str in dates:
        idx = dates.index(today_str)
        if idx == 0:
            return None  # 這個月第一筆交易日,沒有再往前的資料可以當「昨天」
        prev = data[idx - 1]
    else:
        prev = data[-1]  # 今天還沒收盤入帳,最後一筆本身就是最近一個完整交易日

    try:
        return {
            "date": prev["Date"],
            "open": float(prev["Open"]),
            "high": float(prev["High"]),
            "low": float(prev["Low"]),
            "close": float(prev["Close"]),
        }
    except (KeyError, ValueError):
        return None


def _roc_date_to_western(roc_date: str) -> str:
    """民國年日期字串("1150917")轉西元"YYYYMMDD"("20260917")。"""
    year = int(roc_date[:3]) + 1911
    return f"{year}{roc_date[3:]}"


def update_otc_index_history_cache() -> pd.DataFrame:
    """把TPEx「這個月」的櫃買指數OHLC(tpex_index)跟「這個月」的全市場成交量
    (tpex_daily_trading_index)依日期合併,累積存進本地快取(data_cache/otc_index_history.csv),
    湊出 volume_profile.py 支撐/阻力要用的多天OHLCV資料。

    這兩個TPEx端點都只回傳當月資料、沒有任意區間查詢能力,跟 margin_data.py(融資融券)
    完全一樣的限制——這裡用同一套解法,本地逐次累積,已經存過的日期不會被覆蓋(用Date
    去重,保留新抓到的版本,因為當月最後一筆的Change/Volume在盤中可能還會變動)。差別是
    這裡一次可以拿到「整個月」不是「一天」,所以不用每天都真的打開app才累積得到資料,
    累積速度比逐日快取快很多——只要這個月裡開過一次app,那個月份就整個進快取了。

    抓取失敗時只回傳現有快取(不因為單次抓取失敗就把本地已經累積的資料清空、也不會
    寫入壞資料)。回傳欄位跟 get_history() 相容(Date/Open/High/Low/Close/Volume),
    方便直接餵給 volume_profile.find_nearest_supports()/find_nearest_resistances()。
    """
    if OTC_INDEX_HISTORY_CACHE.exists():
        cache_df = pd.read_csv(OTC_INDEX_HISTORY_CACHE, dtype={"Date": str})
    else:
        cache_df = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    try:
        idx_data = requests.get(TPEX_INDEX_URL, headers=HEADERS, timeout=15).json()
        vol_data = requests.get(TPEX_DAILY_TRADING_INDEX_URL, headers=HEADERS, timeout=15).json()
    except Exception:
        idx_data, vol_data = None, None

    if idx_data and vol_data:
        volume_by_date = {}
        for row in vol_data:
            try:
                volume_by_date[_roc_date_to_western(row["Date"])] = float(row["TradeVolume"])
            except (KeyError, ValueError):
                continue

        new_rows = []
        for row in idx_data:
            date_str = row.get("Date")
            try:
                new_rows.append({
                    "Date": date_str,
                    "Open": float(row["Open"]),
                    "High": float(row["High"]),
                    "Low": float(row["Low"]),
                    "Close": float(row["Close"]),
                    "Volume": volume_by_date.get(date_str),
                })
            except (KeyError, ValueError):
                continue

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            cache_df = new_df if cache_df.empty else pd.concat([cache_df, new_df], ignore_index=True)
            cache_df = cache_df.drop_duplicates(subset="Date", keep="last")

    cache_df = cache_df.sort_values("Date").reset_index(drop=True)
    cache_df.to_csv(OTC_INDEX_HISTORY_CACHE, index=False)
    return cache_df


def get_history(
    code: str,
    period: str = "1y",
    interval: str = "1d",
    otc: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """取得歷史 OHLCV 資料。

    period: 例如 "1mo", "6mo", "1y", "5y", "max"(沒有指定 start/end 時使用)
    interval: 例如 "1d", "1wk", "1mo"
    start/end: "YYYY-MM-DD",指定明確日期區間時用這兩個(例如回測),會忽略 period。
    """
    symbol = to_yf_symbol(code, otc)
    ticker = yf.Ticker(symbol)
    if start or end:
        df = ticker.history(start=start, end=end, interval=interval)
    else:
        df = ticker.history(period=period, interval=interval)
    df.index.name = "date"
    return df


def _load_isin_name_table(otc: bool, force_refresh: bool = False) -> pd.DataFrame:
    """抓 TWSE ISIN 一覽表(上市或上櫃),解析出「代號 -> 中文簡稱」對照表,本地快取 7 天。

    頁面裡「有價證券代號及名稱」欄位是 "代號　名稱"(用全形空白分隔)一起塞在同一欄,
    區隔股票/ETF/受益證券等分類的標題列該欄沒有全形空白可切,切完 split 後第二欄會是
    NaN,用這個特性直接濾掉分類標題列,不用另外判斷分類文字。
    """
    cache_path = CACHE_DIR / f"isin_names_{'otc' if otc else 'listed'}.csv"
    if not force_refresh and cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL_SECONDS:
        return pd.read_csv(cache_path, dtype=str)

    resp = requests.get(ISIN_LIST_URLS[otc], headers=HEADERS, timeout=20)
    resp.encoding = "big5"
    tables = pd.read_html(StringIO(resp.text))
    table = max(tables, key=len)

    split = table.iloc[1:][0].astype(str).str.split("　", n=1, expand=True)
    valid = split[1].notna()
    result = pd.DataFrame({"code": split[0][valid].str.strip(), "name": split[1][valid].str.strip()})
    result = result.drop_duplicates(subset="code")

    result.to_csv(cache_path, index=False)
    return result


def get_chinese_name(code: str, otc: bool = False) -> str | None:
    """回傳股票的中文簡稱(查不到回傳 None,呼叫端應該優雅降級,不要當成錯誤)。"""
    df = _load_isin_name_table(otc)
    match = df.loc[df["code"] == code, "name"]
    return match.iloc[0] if not match.empty else None


if __name__ == "__main__":
    code = "2330"  # 台積電
    print(f"=== {code} 即時(延遲)報價 ===")
    quote = get_quote(code)
    for k, v in quote.items():
        print(f"{k}: {v}")

    print(f"\n=== {code} 近一年歷史資料(前 5 筆) ===")
    hist = get_history(code, period="1y")
    print(hist.head())
    print(f"\n共 {len(hist)} 筆資料")
