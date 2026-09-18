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

每個指標另外附「強弱」標記——使用者問「這個漲跌算強還是弱」,確認要跟自己近期比,不是
用固定的絕對門檻(例如±1%)。做法:算出「今天以前」`STRENGTH_WINDOW_DAYS` 個交易日的
平均單日漲跌幅度(絕對值)當基準,今天的漲跌幅度對這個基準的倍數 >= 1.5 倍算「強」、
<= 0.5 倍算「弱」,其餘算「普通」。基準只算「今天以前」的資料,不把今天自己算進去,
不然今天一根大漲大跌會把自己的比較基準也一起墊高,變得比不出強弱。這個做法的好處是
門檻會自動跟著各指標自己的波動習慣調整(例如那斯達克期貨本來就比小道瓊期貨愛大起大落,
基準也會跟著比較高,不會用同一把絕對尺去評斷不同指標)。

綜合方向改用「強弱加權分數」,不是單純「幾個漲、幾個跌」多數決——舊版多數決有個明顯
盲點:如果2個指標「普通」上漲、2個指標「強」下跌,單純數人頭會打成平手「不一致(2:2)」,
但直覺上這種組合其實偏空氣氛更重,只是被多數決抹平了。改法:每個指標依強弱給權重
(弱=1、普通=2、強=3,`STRENGTH_WEIGHT`),乘上方向正負號後加總得出淨分數(`score`),
再依 `SCORE_STRONG_THRESHOLD` 分成「強多/偏多/中性/偏空/強空」五級。強弱資料不足
(近期資料不到 `STRENGTH_WINDOW_DAYS` 天)的指標,算分時退回跟「普通」一樣的權重(2),
不會整個被排除在外。

**只有小道瓊期貨/那斯達克期貨兩個(`SCORE_SYMBOLS`)計入淨分**,費半/台積電ADR只顯示
各自的漲跌幅跟強弱標記,不參與加總——使用者明確要求這兩個純粹當參考,不要影響「淨分/
強多偏多中性偏空強空」這個綜合判斷,可能是因為費半/ADR是前一個交易日的現貨收盤價
(跟小道瓊/那斯達克期貨的近24小時夜盤走勢時間點不同,混在一起加總會模糊掉「最新夜盤
方向」這個淨分本來想表達的意思)。

**這組權重(1:2:3)、強的門檻都是主觀訂的,不是統計驗證過的數字**——跟強弱標記本身的
1.5/0.5 倍門檻一樣,單純是「拿現有的強弱標記做加權」這個直覺想法的實作,之後如果使用者
想調整權重或改門檻,直接改這幾個常數即可,不用動其他邏輯。
"""

import yfinance as yf

US_MARKET_SYMBOLS = {
    "dow_futures": ("YM=F", "小道瓊期貨"),
    "nasdaq_futures": ("NQ=F", "那斯達克期貨"),
    "sox": ("^SOX", "費城半導體指數"),
    "tsm_adr": ("TSM", "台積電ADR"),
}

SCORE_SYMBOLS = ("dow_futures", "nasdaq_futures")  # 只有這兩個計入淨分,費半/ADR純參考

STRENGTH_WINDOW_DAYS = 20
STRENGTH_STRONG_RATIO = 1.5
STRENGTH_WEAK_RATIO = 0.5

STRENGTH_WEIGHT = {"強": 3, "普通": 2, "弱": 1}
DEFAULT_STRENGTH_WEIGHT = 2  # 強弱資料不足時,算分退回跟「普通」同等權重
# 滿分是 SCORE_SYMBOLS 2 個指標 * 3(強) = 6,門檻抓約4(需要至少「兩個都普通以上」或
# 「一強一弱」的agreement程度)才算「強多/強空」——改成只用2個指標計分後門檻也重新校準過,
# 不是沿用舊版4指標時代的±5(那個門檻對只剩2個指標來說會變得幾乎打不到)。
SCORE_STRONG_THRESHOLD = 4


def _latest_change(symbol: str) -> dict | None:
    """抓 symbol 近期收盤,算出最新一筆相對前一筆的漲跌幅,以及相對近期的強弱標記。

    強弱標記(`strength`,"強"/"普通"/"弱")跟對應倍數(`strength_ratio`)見模組 docstring
    的說明;近期資料不足 `STRENGTH_WINDOW_DAYS` 天時這兩個欄位回傳 None(不硬猜),但
    `change_pct` 本身只要有兩筆收盤就算得出來,兩者分開處理。資料完全不足(不到兩筆收盤)
    整個回傳 None。
    """
    hist = yf.Ticker(symbol).history(period="2mo")
    close = hist["Close"].dropna()
    if len(close) < 2:
        return None

    latest, prev = float(close.iloc[-1]), float(close.iloc[-2])
    change_pct = (latest / prev - 1) * 100

    strength, strength_ratio = None, None
    daily_returns = close.pct_change().dropna() * 100
    baseline_returns = daily_returns.iloc[:-1]  # 不含今天(daily_returns 最後一筆就是今天)
    if len(baseline_returns) >= STRENGTH_WINDOW_DAYS:
        baseline = baseline_returns.tail(STRENGTH_WINDOW_DAYS).abs().mean()
        if baseline > 0:
            strength_ratio = abs(change_pct) / baseline
            if strength_ratio >= STRENGTH_STRONG_RATIO:
                strength = "強"
            elif strength_ratio <= STRENGTH_WEAK_RATIO:
                strength = "弱"
            else:
                strength = "普通"

    return {
        "close": latest,
        "change_pct": change_pct,
        "asof": close.index[-1].strftime("%Y-%m-%d %H:%M"),
        "strength": strength,
        "strength_ratio": strength_ratio,
    }


def _score_label(score: float) -> str:
    if score >= SCORE_STRONG_THRESHOLD:
        return "強多"
    if score > 0:
        return "偏多"
    if score == 0:
        return "中性"
    if score > -SCORE_STRONG_THRESHOLD:
        return "偏空"
    return "強空"


def get_us_overnight_signal() -> dict:
    """回傳每個指標各自最新漲跌幅(含強弱標記),以及強弱加權後的綜合多空分數。

    `score`:只由 `SCORE_SYMBOLS`(小道瓊期貨+那斯達克期貨)兩個指標依強弱給權重
    (見模組 docstring 的 `STRENGTH_WEIGHT`)、乘上漲跌方向正負號後加總,範圍 -6~+6,
    正代表偏多、負代表偏空,數值越極端代表訊號越一致越強烈。費半/台積電ADR不計入,
    只在回傳的dict裡各自附上漲跌幅跟強弱標記供顯示,不影響這個分數。`score_label` 是
    對應的五級文字("強多"/"偏多"/"中性"/"偏空"/"強空")。SCORE_SYMBOLS都抓不到資料時
    兩者都回傳 None。
    """
    results = {key: _latest_change(symbol) for key, (symbol, _) in US_MARKET_SYMBOLS.items()}

    scored = [results[key] for key in SCORE_SYMBOLS if results.get(key) is not None]
    score, score_label = None, None
    if scored:
        score = sum(
            (1 if r["change_pct"] > 0 else (-1 if r["change_pct"] < 0 else 0))
            * STRENGTH_WEIGHT.get(r["strength"], DEFAULT_STRENGTH_WEIGHT)
            for r in scored
        )
        score_label = _score_label(score)

    return {**results, "score": score, "score_label": score_label}


if __name__ == "__main__":
    result = get_us_overnight_signal()
    for key, (_, label) in US_MARKET_SYMBOLS.items():
        r = result[key]
        if r:
            strength_str = f"，{r['strength']}(近{STRENGTH_WINDOW_DAYS}日平均的{r['strength_ratio']:.1f}倍)" if r["strength"] else "，強弱資料不足"
            print(f"{label}:{r['close']:,.2f} ({r['change_pct']:+.2f}%) @ {r['asof']}{strength_str}")
        else:
            print(f"{label}:資料不足")
    if result["score"] is not None:
        print(f"綜合多空分數:{result['score']:+d}（{result['score_label']}）")
    else:
        print("綜合多空分數:無法判斷(資料不足)")
