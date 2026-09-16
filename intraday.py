"""台股盤中即時漲跌趨勢強弱判斷(單股)。

修 ^TWOII(櫃買指數)的問題時才確認 `yfinance` 對台股的「即時」報價其實是delayed、甚至
嚴重過期的資料(見 fetch_data.py 的說明),不適合拿來做盤中即時判斷。這裡改抓 TWSE 官方
「基本市況報導網站」的即時報價 API(mis.twse.com.tw,就是 TWSE 官網「個股行情查詢」畫面
背後在打的那個端點)——已實測上市(tse_2330.tw)、上櫃(otc_6488.tw)代號格式都能正常
拿到當下成交價/開高低/昨收/累積量/五檔委買委賣,不需要驗證碼。

強弱分數是 5 個訊號的加權平均(各自正規化到 -1~+1,加權平均後乘 100),跟 us_market.py
的「強弱加權淨分數」是同一套「五級標籤(強多/偏多/中性/偏空/強空)」設計理念,但訊號本身
是連續值不是離散的強/普通/弱分類,所以改用加權平均而不是加權計數。**這組權重跟正規化用的
「典型幅度」都是主觀訂的,不是統計驗證過的數字**——跟 us_market.py 的權重一樣,之後想調整
直接改這幾個常數即可。

5 個訊號:
1. 當日區間位置:現價在「當日最高-最低」區間的相對位置,收在高點附近算強、低點附近算弱。
2. 開盤動能:現價對開盤價的漲跌幅。
3. 量能比:今日累積成交量 ÷ (近5日均量 × 已過時間比例)——>1代表當下放量。
4. 委買委賣力道:五檔委買量 vs 五檔委賣量的差,是「掛單力道」的代理指標,不是真正的逐筆
   內外盤比(TWSE MIS 沒有逐筆成交方向資料)。
5. 短線動能斜率:同一個瀏覽器 session 內累積輪詢到的價格,算最近幾筆的簡單漲跌方向——
   只有從使用者開啟這檔股票開始才有資料,不是從開盤累積,樣本不足時直接跳過不硬湊。

任何一項訊號缺資料(例如盤前還沒成交、剛開盤量能比雜訊太大、session 內樣本不足)都直接
跳過,權重在其餘可用訊號間重新正規化,不會因為少一項就報錯或分數失真。
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import requests

TWSE_MIS_QUOTE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TWSE_MIS_INDEX_URL = "https://mis.twse.com.tw/stock/index.jsp"  # 只用來拿 session cookie
HEADERS = {"User-Agent": "Mozilla/5.0"}

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
MARKET_OPEN_TIME = dt_time(9, 0)
MARKET_CLOSE_TIME = dt_time(13, 30)
MARKET_SESSION_MINUTES = 270  # 09:00-13:30

# 正規化用的「典型幅度」,除以這個值再 clip 到 -1~1——例如開盤動能 2% 就已經算是滿格的強訊號。
OPEN_MOMENTUM_TYPICAL_PCT = 2.0
PRICE_SLOPE_TYPICAL_PCT = 0.3
MIN_ELAPSED_FRACTION_FOR_VOLUME = 0.03  # 開盤不到約8分鐘時,量能比雜訊太大先跳過
MIN_PRICE_HISTORY_SAMPLES = 5  # 短線動能斜率至少要有幾筆 session 內樣本才計算

SIGNAL_WEIGHTS = {
    "range_position": 0.25,
    "open_momentum": 0.20,
    "volume_ratio": 0.20,
    "bid_ask_pressure": 0.20,
    "price_slope": 0.15,
}
SIGNAL_LABELS = {
    "range_position": "當日區間位置",
    "open_momentum": "開盤動能",
    "volume_ratio": "量能比",
    "bid_ask_pressure": "委買委賣力道",
    "price_slope": "短線動能",
}
SCORE_STRONG_THRESHOLD = 50  # 分數範圍 -100~100,對齊顯示用的門檻
SCORE_NEUTRAL_DEADZONE = 5  # |分數| 小於這個值算「中性」——分數是連續值,剛好等於0幾乎不會發生

# TWSE MIS 是已知的公開端點(TWSE 官網自己也是打這支 API),持續輪詢時先打一次 index.jsp
# 拿 cookie 比較穩定——用 module-level session 讓同一個 process 內的輪詢重複使用。
_session = requests.Session()
_session_primed = False


def _get_primed_session() -> requests.Session:
    global _session_primed
    if not _session_primed:
        try:
            _session.get(TWSE_MIS_INDEX_URL, headers=HEADERS, timeout=10)
        except Exception:
            pass
        _session_primed = True
    return _session


def is_market_open_now(now: datetime | None = None) -> bool:
    """平日 + 09:00-13:30(Asia/Taipei)。不含國定假日行事曆——颱風假/國定假日恰好是平日時
    會誤判成開盤中,TWSE MIS 在非交易日仍會回傳最後一個交易日的靜態資料,不會報錯,只是
    多打幾次沒意義的請求,可接受。
    """
    now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME


def _elapsed_session_fraction(now: datetime | None = None) -> float:
    """今天從09:00開盤到現在,佔整個270分鐘盤中時段的比例,收在0~1之間。"""
    now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    open_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_minutes = (now - open_dt).total_seconds() / 60
    return max(0.0, min(1.0, elapsed_minutes / MARKET_SESSION_MINUTES))


def _parse_float(raw: dict, key: str) -> float | None:
    v = raw.get(key)
    if v is None or v in ("-", ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_depth(raw: dict, key: str) -> list[float]:
    values = []
    for part in (raw.get(key) or "").split("_"):
        if part in ("", "-"):
            continue
        try:
            values.append(float(part))
        except ValueError:
            continue
    return values


def get_intraday_quote(code: str, otc: bool = False) -> dict | None:
    """打 TWSE MIS 即時報價 API,回傳目前成交價/開高低/昨收/累積量/五檔委買委賣。

    `volume` 已經從 TWSE 回傳的「張」換算成「股」(乘1000),跟 fetch_data.get_history()
    回傳的 yfinance Volume 欄位單位一致,方便直接拿來算量能比。抓取失敗、代號查無資料、
    或還在非交易時段查詢一個從未有過即時資料的冷門代號時回傳 None,呼叫端應該優雅降級。
    """
    ex_ch = f"{'otc' if otc else 'tse'}_{code}.tw"
    session = _get_primed_session()
    try:
        resp = session.get(
            TWSE_MIS_QUOTE_URL,
            params={"ex_ch": ex_ch, "json": 1, "delay": 0},
            headers=HEADERS,
            timeout=10,
        )
        data = resp.json()
    except Exception:
        return None

    msg_array = data.get("msgArray") or []
    if not msg_array:
        return None
    raw = msg_array[0]

    volume_lots = _parse_float(raw, "v")
    bid_volumes = _parse_depth(raw, "g")
    ask_volumes = _parse_depth(raw, "f")

    return {
        "symbol": raw.get("ch", ex_ch),
        "name": raw.get("n"),
        "last_price": _parse_float(raw, "z"),
        "open": _parse_float(raw, "o"),
        "day_high": _parse_float(raw, "h"),
        "day_low": _parse_float(raw, "l"),
        "previous_close": _parse_float(raw, "y"),
        "volume": volume_lots * 1000 if volume_lots is not None else None,
        "bid_volumes": bid_volumes,
        "ask_volumes": ask_volumes,
        "time": raw.get("t") or raw.get("ot"),
        "date": raw.get("d"),
    }


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _signal_range_position(quote: dict) -> dict:
    high, low, price = quote.get("day_high"), quote.get("day_low"), quote.get("last_price")
    if high is None or low is None or price is None or high <= low:
        return {"available": False, "value": None, "detail": "當日高低區間資料不足"}
    value = _clip(2 * (price - low) / (high - low) - 1)
    return {"available": True, "value": value, "detail": f"現價 {price:,.2f}(高{high:,.2f}/低{low:,.2f})"}


def _signal_open_momentum(quote: dict) -> dict:
    open_price, price = quote.get("open"), quote.get("last_price")
    if not open_price or price is None:
        return {"available": False, "value": None, "detail": "尚無開盤價或成交價"}
    pct = (price - open_price) / open_price * 100
    value = _clip(pct / OPEN_MOMENTUM_TYPICAL_PCT)
    return {"available": True, "value": value, "detail": f"{pct:+.2f}%(對開盤 {open_price:,.2f})"}


def _signal_volume_ratio(quote: dict, avg_daily_volume: float | None) -> dict:
    volume = quote.get("volume")
    elapsed_fraction = _elapsed_session_fraction()
    if volume is None or not avg_daily_volume or elapsed_fraction < MIN_ELAPSED_FRACTION_FOR_VOLUME:
        return {"available": False, "value": None, "detail": "開盤時間太短或成交量資料不足"}
    expected = avg_daily_volume * elapsed_fraction
    if expected <= 0:
        return {"available": False, "value": None, "detail": "近5日均量資料不足"}
    ratio = volume / expected
    value = _clip(ratio - 1)
    return {"available": True, "value": value, "detail": f"量比 {ratio:.2f}倍(近5日同時段預期)"}


def _signal_bid_ask_pressure(quote: dict) -> dict:
    bid_total = sum(quote.get("bid_volumes") or [])
    ask_total = sum(quote.get("ask_volumes") or [])
    if bid_total + ask_total <= 0:
        return {"available": False, "value": None, "detail": "五檔掛單資料不足"}
    value = _clip((bid_total - ask_total) / (bid_total + ask_total))
    return {"available": True, "value": value, "detail": f"委買{bid_total:,.0f} / 委賣{ask_total:,.0f}"}


def _signal_price_slope(price_history: list[tuple[float, float]]) -> dict:
    if len(price_history) < MIN_PRICE_HISTORY_SAMPLES:
        return {"available": False, "value": None, "detail": f"樣本蒐集中({len(price_history)}/{MIN_PRICE_HISTORY_SAMPLES})"}
    first_price = price_history[0][1]
    last_price = price_history[-1][1]
    if not first_price:
        return {"available": False, "value": None, "detail": "樣本價格異常"}
    pct = (last_price - first_price) / first_price * 100
    value = _clip(pct / PRICE_SLOPE_TYPICAL_PCT)
    span_seconds = price_history[-1][0] - price_history[0][0]
    return {"available": True, "value": value, "detail": f"近{span_seconds:.0f}秒 {pct:+.2f}%"}


def _score_label(score: float) -> str:
    if score >= SCORE_STRONG_THRESHOLD:
        return "強多"
    if score > SCORE_NEUTRAL_DEADZONE:
        return "偏多"
    if score >= -SCORE_NEUTRAL_DEADZONE:
        return "中性"
    if score > -SCORE_STRONG_THRESHOLD:
        return "偏空"
    return "強空"


def compute_intraday_strength(
    quote: dict,
    avg_daily_volume: float | None,
    price_history: list[tuple[float, float]],
) -> dict:
    """5 個訊號 → 加權分數(-100~100)+ 五級標籤。任何訊號缺資料時跳過,權重在其餘可用
    訊號間重新正規化。回傳 `signals`(每項訊號的可用性/正規化值/人類可讀說明,順序固定
    比照 SIGNAL_WEIGHTS,方便呼叫端直接照順序渲染)、`score`、`score_label`
    (兩者資料完全不足時都是 None)。
    """
    signals = {
        "range_position": _signal_range_position(quote),
        "open_momentum": _signal_open_momentum(quote),
        "volume_ratio": _signal_volume_ratio(quote, avg_daily_volume),
        "bid_ask_pressure": _signal_bid_ask_pressure(quote),
        "price_slope": _signal_price_slope(price_history),
    }

    available = {k: v for k, v in signals.items() if v["available"]}
    score = None
    score_label = None
    if available:
        weight_sum = sum(SIGNAL_WEIGHTS[k] for k in available)
        score = sum(SIGNAL_WEIGHTS[k] * v["value"] for k, v in available.items()) / weight_sum * 100
        score_label = _score_label(score)

    return {"score": score, "score_label": score_label, "signals": signals}


if __name__ == "__main__":
    code = "2330"
    print(f"=== {code} 盤中即時報價 ===")
    print(f"目前是否為交易時間:{is_market_open_now()}")
    quote = get_intraday_quote(code)
    if quote is None:
        print("抓取失敗")
    else:
        for k, v in quote.items():
            print(f"{k}: {v}")

        print(f"\n=== {code} 盤中強弱分數(price_history 用當前這一筆模擬) ===")
        import time as time_module

        fake_history = [(time_module.time(), quote["last_price"])] * MIN_PRICE_HISTORY_SAMPLES
        result = compute_intraday_strength(quote, avg_daily_volume=20_000_000, price_history=fake_history)
        for key, label in SIGNAL_LABELS.items():
            s = result["signals"][key]
            print(f"{label}:{s['detail']}" + (f"(正規化值 {s['value']:+.2f})" if s["available"] else ""))
        if result["score"] is not None:
            print(f"\n強弱分數:{result['score']:+.1f}({result['score_label']})")
        else:
            print("\n強弱分數:無法判斷(資料不足)")
