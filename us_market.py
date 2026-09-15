"""美股夜盤連動指標——小道瓊期貨、那斯達克期貨、費城半導體指數(費半)、台積電ADR。

概念:使用者觀察到美股夜盤走勢跟隔天台股高度連動,這裡直接用 yfinance 抓幾個指標最近
一根 K 棒的漲跌幅,當成「隔天台股開盤前」的方向參考——單純是「美股昨夜漲/跌」的快速
摘要,不是精確的下單訊號,也不做任何統計驗證(跟 `signals.py`/`relative_strength.py`
那些有明確公開方法論的指標不同,純粹是使用者自訂的觀察型參考指標)。

四個指標各自的角色:
- 小道瓊期貨(YM=F)、那斯達克期貨(NQ=F):用期貨不用道瓊/那斯達克現貨指數,因為期貨
  幾乎 24 小時交易,可以涵蓋到台股開盤前最新的夜盤走勢;現貨指數收盤時間比台股開盤
  早了將近 12 小時,參考價值較低。兩個一起看是因為道瓊(傳產/藍籌權重高)跟那斯達克
  (科技股權重高)反映的產業面不完全一樣。
- 費城半導體指數(^SOX):半導體類股的產業指數,沒有穩定的期貨代號,直接用現貨。
- 台積電ADR(TSM,紐約證交所掛牌):業界公認最直接的隔天台股領先指標之一——台積電是
  台股權值最大的一檔,ADR 漲跌常常直接預示台積電/大盤隔天怎麼走,跟 ^SOX 一樣用現貨
  (沒有期貨)。

綜合方向用「幾個漲、幾個跌」的多數決,不是要求全部一致才算數:TSM ADR 是單一個股,
偶爾會因為台積電自己的消息(法說會、除息、ADR溢價/折價)脫離大盤走勢,如果要求 4 個
指標全部同向才判得出方向,反而會讓「不一致」出現的頻率大增,參考價值不增反減,違背
當初加更多指標想提高參考價值的本意。
"""

import yfinance as yf

US_MARKET_SYMBOLS = {
    "dow_futures": ("YM=F", "小道瓊期貨"),
    "nasdaq_futures": ("NQ=F", "那斯達克期貨"),
    "sox": ("^SOX", "費城半導體指數"),
    "tsm_adr": ("TSM", "台積電ADR"),
}


def _latest_change(symbol: str) -> dict | None:
    """抓 symbol 最近幾個交易日的收盤,算出最新一筆相對前一筆的漲跌幅。資料不足回傳 None。"""
    hist = yf.Ticker(symbol).history(period="5d")
    close = hist["Close"].dropna()
    if len(close) < 2:
        return None
    latest, prev = float(close.iloc[-1]), float(close.iloc[-2])
    return {
        "close": latest,
        "change_pct": (latest / prev - 1) * 100,
        "asof": close.index[-1].strftime("%Y-%m-%d %H:%M"),
    }


def get_us_overnight_signal() -> dict:
    """回傳每個指標各自最新漲跌幅,以及依「幾個漲、幾個跌」統計出的綜合方向摘要。

    綜合方向用抓得到資料的指標裡漲跌數量的多數決:漲的多 -> "偏多"、跌的多 -> "偏空"、
    平手 -> "不一致";一個資料都抓不到時回傳 None。`direction_ratio` 是對應的比例文字
    (例如 "3/4"、平手時是 "2:2"),給呼叫端顯示用。
    """
    results = {key: _latest_change(symbol) for key, (symbol, _) in US_MARKET_SYMBOLS.items()}

    available = [r for r in results.values() if r is not None]
    direction, direction_ratio = None, None
    if available:
        up = sum(1 for r in available if r["change_pct"] > 0)
        down = sum(1 for r in available if r["change_pct"] < 0)
        total = len(available)
        if up > down:
            direction, direction_ratio = "偏多", f"{up}/{total}"
        elif down > up:
            direction, direction_ratio = "偏空", f"{down}/{total}"
        else:
            direction, direction_ratio = "不一致", f"{up}:{down}"

    return {**results, "direction": direction, "direction_ratio": direction_ratio}


if __name__ == "__main__":
    result = get_us_overnight_signal()
    for key, (_, label) in US_MARKET_SYMBOLS.items():
        r = result[key]
        if r:
            print(f"{label}:{r['close']:,.2f} ({r['change_pct']:+.2f}%) @ {r['asof']}")
        else:
            print(f"{label}:資料不足")
    if result["direction"]:
        print(f"綜合方向:{result['direction']}({result['direction_ratio']})")
    else:
        print("綜合方向:無法判斷(資料不足)")
