"""美股夜盤連動指標——小道瓊期貨、費城半導體指數(費半)。

概念:使用者觀察到美股夜盤走勢跟隔天台股高度連動(尤其電子/半導體股常跟著費半走),
這裡直接用 yfinance 抓兩個指標最近一根 K 棒的漲跌幅,當成「隔天台股開盤前」的方向
參考——單純是「美股昨夜漲/跌」的快速摘要,不是精確的下單訊號,也不做任何統計驗證
(跟 `signals.py`/`relative_strength.py` 那些有明確公開方法論的指標不同,純粹是使用者
自訂的觀察型參考指標)。

用小道瓊期貨(YM=F)不用道瓊現貨指數(^DJI):期貨幾乎 24 小時交易,可以涵蓋到台股
開盤前最新的夜盤走勢;現貨指數收盤時間比台股開盤早了將近 12 小時,參考價值較低。
費半沒有穩定的期貨代號,直接用現貨指數(^SOX)。
"""

import yfinance as yf

US_MARKET_SYMBOLS = {
    "dow_futures": ("YM=F", "小道瓊期貨"),
    "sox": ("^SOX", "費城半導體指數"),
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
    """回傳小道瓊期貨、費半指數各自最新漲跌幅,以及一個簡單的綜合方向摘要。

    綜合方向:兩者都漲 -> "偏多"、兩者都跌 -> "偏空"、方向不一致 -> "不一致";
    任一資料抓不到時該項回傳 None,綜合方向也回傳 None(資料不足不硬猜方向)。
    """
    results = {key: _latest_change(symbol) for key, (symbol, _) in US_MARKET_SYMBOLS.items()}

    direction = None
    if all(r is not None for r in results.values()):
        changes = [r["change_pct"] for r in results.values()]
        if all(c > 0 for c in changes):
            direction = "偏多"
        elif all(c < 0 for c in changes):
            direction = "偏空"
        else:
            direction = "不一致"

    return {**results, "direction": direction}


if __name__ == "__main__":
    result = get_us_overnight_signal()
    for key, (_, label) in US_MARKET_SYMBOLS.items():
        r = result[key]
        if r:
            print(f"{label}:{r['close']:,.2f} ({r['change_pct']:+.2f}%) @ {r['asof']}")
        else:
            print(f"{label}:資料不足")
    print(f"綜合方向:{result['direction'] or '無法判斷(資料不足或方向不一致)'}")
