"""隔夜/盤前連動強弱指標——小道瓊期貨、那斯達克期貨、韓國KOSPI、美元/新台幣、VIX恐慌指數
(計分)+ 費城半導體指數(費半)、台積電ADR(純參考)。

概念:使用者觀察到美股夜盤走勢跟隔天台股高度連動,這裡直接用 yfinance 抓幾個指標最近
一根 K 棒的漲跌幅,當成「隔天台股開盤前」的方向參考——單純是快速摘要,不是精確的下單
訊號,也不做任何統計驗證(跟 `signals.py`/`relative_strength.py` 那些有明確公開方法論的
指標不同,純粹是使用者自訂的觀察型參考指標)。使用者明確要求「多方向判斷」,所以除了
原本的美股期貨,再加兩個不同性質的角度:亞洲同步市場(韓國KOSPI)、資金流向(美元/
新台幣匯率)、風險情緒(VIX)——不是全部堆同一種「股價指數」,而是股市/匯市/波動率
三個不同維度一起看。

七個指標各自的角色:
- 小道瓊期貨(YM=F)、那斯達克期貨(NQ=F):用期貨不用道瓊/那斯達克現貨指數,因為期貨
  幾乎 24 小時交易,可以涵蓋到台股開盤前最新的夜盤走勢;現貨指數收盤時間比台股開盤
  早了將近 12 小時,參考價值較低。兩個一起看是因為道瓊(傳產/藍籌權重高)跟那斯達克
  (科技股權重高)反映的產業面不完全一樣。**計入淨分。**
- 韓國KOSPI(^KS11):韓股開盤時間比台股早(同一個交易日,不是隔夜),產業結構跟台灣
  高度重疊(三星/SK海力士對標台積電/聯電),是少數「當天真的已經開盤」的領先指標,
  跟小道瓊/那斯達克這種「隔夜」訊號的時間點不同。**計入淨分。**
- 美元/新台幣(TWD=X,USD/TWD匯率):反映外資資金流向——外資賣超台股換匯匯出會推升
  USD/TWD(新台幣貶值),外資買超匯入則會壓低USD/TWD(新台幣升值),所以這個指標對
  台股的「方向」跟自己「漲跌」的方向是**反的**:USD/TWD上漲(新台幣貶)對台股是偏空
  訊號,下跌(新台幣升)是偏多訊號。**計入淨分**,已在`INVERTED_SYMBOLS`裡標記反向。
- VIX恐慌指數(^VIX):不是判斷台股方向,是判斷「現在市場敢不敢冒險」——VIX飆高代表
  避險情緒濃厚,資金傾向從風險資產(包括台股這種新興市場股票)撤出,所以VIX上漲對
  台股也是偏空訊號,跟USD/TWD一樣是**反向**指標。**計入淨分**。
- 費城半導體指數(^SOX):半導體類股的產業指數,沒有穩定的期貨代號,直接用現貨。
  **只顯示漲跌,不計分**(見下方說明)。
- 台積電ADR(TSM,紐約證交所掛牌):業界公認最直接的隔天台股領先指標之一——台積電是
  台股權值最大的一檔,ADR 漲跌常常直接預示台積電/大盤隔天怎麼走,跟 ^SOX 一樣用現貨
  (沒有期貨)。**只顯示漲跌,不計分**。

每個指標另外附「強弱」標記——使用者問「這個漲跌算強還是弱」,確認要跟自己近期比,不是
用固定的絕對門檻(例如±1%)。做法:算出「今天以前」`STRENGTH_WINDOW_DAYS` 個交易日的
平均單日漲跌幅度(絕對值)當基準,今天的漲跌幅度對這個基準的倍數 >= 1.5 倍算「強」、
<= 0.5 倍算「弱」,其餘算「普通」。基準只算「今天以前」的資料,不把今天自己算進去,
不然今天一根大漲大跌會把自己的比較基準也一起墊高,變得比不出強弱。這個做法的好處是
門檻會自動跟著各指標自己的波動習慣調整(例如VIX、USD/TWD本來就跟股價指數的波動幅度
完全不是一個量級,基準會自動跟著各自的習慣調整,不會用同一把絕對尺去評斷不同性質的
指標)。

綜合方向用「強弱加權分數」,不是單純「幾個漲、幾個跌」多數決——舊版多數決有個明顯
盲點:如果2個指標「普通」上漲、2個指標「強」下跌,單純數人頭會打成平手「不一致(2:2)」,
但直覺上這種組合其實偏空氣氛更重,只是被多數決抹平了。改法:每個指標依強弱給權重
(弱=1、普通=2、強=3,`STRENGTH_WEIGHT`),乘上「對台股的方向」(`effective_sign()`,
一般指標是自己漲跌的正負號,`INVERTED_SYMBOLS`裡的指標要反過來)後加總得出淨分數
(`score`),再依 `SCORE_STRONG_THRESHOLD` 分成「強多/偏多/中性/偏空/強空」五級。強弱
資料不足(近期資料不到 `STRENGTH_WINDOW_DAYS` 天)的指標,算分時退回跟「普通」一樣的
權重(2),不會整個被排除在外。

**只有 `SCORE_SYMBOLS`(小道瓊期貨/那斯達克期貨/KOSPI/美元新台幣/VIX,共5個)計入
淨分**,費半/台積電ADR只顯示各自的漲跌幅跟強弱標記,不參與加總——使用者明確要求這兩個
純粹當參考,可能是因為費半/ADR是前一個交易日的現貨收盤價(跟其他計分指標的最新時間點
不同,混在一起加總會模糊掉淨分本來想表達的「最新盤前氣氛」)。

**這組權重(1:2:3)、強的門檻都是主觀訂的,不是統計驗證過的數字**——跟強弱標記本身的
1.5/0.5 倍門檻一樣,單純是「拿現有的強弱標記做加權」這個直覺想法的實作,之後如果使用者
想調整權重、門檻,或新增/移除計分指標,直接改這幾個常數即可,不用動其他邏輯。
"""

import yfinance as yf

US_MARKET_SYMBOLS = {
    "dow_futures": ("YM=F", "小道瓊期貨"),
    "nasdaq_futures": ("NQ=F", "那斯達克期貨"),
    "kospi": ("^KS11", "韓國KOSPI"),
    "usdtwd": ("TWD=X", "美元/新台幣"),
    "vix": ("^VIX", "VIX恐慌指數"),
    "sox": ("^SOX", "費城半導體指數"),
    "tsm_adr": ("TSM", "台積電ADR"),
}

# 計入淨分的5個指標——費半/ADR(sox/tsm_adr)不在這裡,純參考不計分
SCORE_SYMBOLS = ("dow_futures", "nasdaq_futures", "kospi", "usdtwd", "vix")

# 這兩個指標「自己漲」對台股反而是偏空訊號(見模組docstring的說明),算分/燈號顏色時
# 方向要反過來,跟其他「自己漲=對台股偏多」的指標不一樣
INVERTED_SYMBOLS = {"usdtwd", "vix"}

STRENGTH_WINDOW_DAYS = 20
STRENGTH_STRONG_RATIO = 1.5
STRENGTH_WEAK_RATIO = 0.5

STRENGTH_WEIGHT = {"強": 3, "普通": 2, "弱": 1}
DEFAULT_STRENGTH_WEIGHT = 2  # 強弱資料不足時,算分退回跟「普通」同等權重
# 滿分是 SCORE_SYMBOLS 5 個指標 * 3(強) = 15,門檻抓約10(2/3滿分,大致對應「5個裡面
# 有3~4個方向一致且偏強」的agreement程度)才算「強多/強空」——跟改成2指標時同樣比例
# (4/6 = 2/3)校準,不是隨便挑的數字。
SCORE_STRONG_THRESHOLD = 10


def effective_sign(key: str, change_pct: float) -> int:
    """回傳這個指標的漲跌對台股代表的方向:+1偏多、-1偏空、0持平。一般指標就是自己漲跌的
    正負號;`INVERTED_SYMBOLS`裡的指標(USD/TWD、VIX)方向要反過來(自己漲反而對台股
    偏空),見模組docstring的說明。"""
    if change_pct == 0:
        return 0
    raw_sign = 1 if change_pct > 0 else -1
    return -raw_sign if key in INVERTED_SYMBOLS else raw_sign


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

    `score`:只由 `SCORE_SYMBOLS`(小道瓊期貨/那斯達克期貨/KOSPI/美元新台幣/VIX,共5個)
    依強弱給權重(見模組 docstring 的 `STRENGTH_WEIGHT`)、乘上`effective_sign()`(對台股
    的方向,不是每個指標都等於自己漲跌的正負號)後加總,範圍 -15~+15,正代表偏多、負代表
    偏空,數值越極端代表訊號越一致越強烈。費半/台積電ADR不計入,只在回傳的dict裡各自附上
    漲跌幅跟強弱標記供顯示,不影響這個分數。`score_label` 是對應的五級文字("強多"/"偏多"/
    "中性"/"偏空"/"強空")。SCORE_SYMBOLS都抓不到資料時兩者都回傳 None。
    """
    results = {key: _latest_change(symbol) for key, (symbol, _) in US_MARKET_SYMBOLS.items()}

    scored = {key: results[key] for key in SCORE_SYMBOLS if results.get(key) is not None}
    score, score_label = None, None
    if scored:
        score = sum(
            effective_sign(key, r["change_pct"]) * STRENGTH_WEIGHT.get(r["strength"], DEFAULT_STRENGTH_WEIGHT)
            for key, r in scored.items()
        )
        score_label = _score_label(score)

    return {**results, "score": score, "score_label": score_label}


if __name__ == "__main__":
    result = get_us_overnight_signal()
    for key, (_, label) in US_MARKET_SYMBOLS.items():
        r = result[key]
        if r:
            strength_str = f"，{r['strength']}(近{STRENGTH_WINDOW_DAYS}日平均的{r['strength_ratio']:.1f}倍)" if r["strength"] else "，強弱資料不足"
            scored_note = "" if key in SCORE_SYMBOLS else "，不計分"
            inverted_note = "，反向指標" if key in INVERTED_SYMBOLS else ""
            print(f"{label}:{r['close']:,.2f} ({r['change_pct']:+.2f}%) @ {r['asof']}{strength_str}{scored_note}{inverted_note}")
        else:
            print(f"{label}:資料不足")
    if result["score"] is not None:
        print(f"綜合多空分數:{result['score']:+d}（{result['score_label']}）")
    else:
        print("綜合多空分數:無法判斷(資料不足)")
