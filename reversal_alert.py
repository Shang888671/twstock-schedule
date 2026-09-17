"""盤中急殺/反轉警示——純粹的「畫面提示」邏輯(還沒有背景通知,見下方說明),用來補足
「使用者剛好沒在盯盤」時容易錯過的急殺情況。

開發起因:使用者親身經歷櫃買指數(^TWOII)當天先漲高、後來忽然急跌下來,因為沒有即時
發現,等回頭看盤時已經造成虧損。討論後決定分兩步做:
1. (這個模組)先把「什麼叫急殺」的判斷邏輯做出來、驗證抓得準不準——這一步只在網頁上
   顯示警示,使用者還是要自己開著頁面看。
2. 邏輯驗證沒問題後,再包成一支獨立的背景輪詢腳本,透過LINE Notify等管道主動推播——
   因為Streamlit網頁只有使用者開著分頁時才會執行,沒辦法在背景幫忙盯盤,這是後面才要
   解決的問題,這個模組本身不處理。

跟 intraday.py 的差異:intraday.py 是「多空強弱綜合評分」(5訊號加權,目的是判斷現在
偏多還是偏空),這裡只單獨處理「有沒有正在快速從高點反轉往下」這一件事,適用範圍也更廣——
不只個股,加權指數/櫃買指數這種沒有5訊號可用的標的(沒有昨日均量可以算量能比、沒有session
內委買委賣力道以外的訊號)也適用,因為這裡只需要 day_high + last_price + 一段時間內的
價格歷史,intraday.get_intraday_quote() 回傳的欄位就都有。

兩個獨立條件,任一個成立就構成警示,分開判斷是因為兩者代表不同情境:
- **拉回幅度**(距今日高點回落%):就算是慢慢跌回來的,離高點夠遠一樣有風險,這個條件
  不需要價格歷史也能算(day_high本身就是TWSE MIS官方追蹤的當日最高,不依賴我方輪詢
  有沒有剛好抓到那個高點)。
- **近N分鐘變動幅度**:才是真正對應「忽然」「急」這兩個字的訊號,需要呼叫端自己在
  session_state 裡累積這個symbol的價格歷史(比照 intraday.py 短線動能訊號的做法)。
  剛打開頁面、樣本還不夠時這個條件就先跳過,不會硬湊。

**這幾個門檻數字(1%/2%/5分鐘/0.8%)都是主觀訂的,不是統計驗證過的數字**——跟專案裡其他
訊號(us_market.py的強弱門檻、intraday.py的正規化幅度)一樣的免責聲明,之後想調整直接改
這幾個常數即可。
"""

from __future__ import annotations

PULLBACK_WARN_PCT = 1.0  # 距今日高點拉回超過這個%,標「拉回」
PULLBACK_SEVERE_PCT = 2.0  # 超過這個%,直接標「急殺」(即使近期變動幅度那個條件沒觸發)
FAST_DROP_WINDOW_SECONDS = 300  # 「近N分鐘」的N,5分鐘
FAST_DROP_SEVERE_PCT = 0.8  # 近5分鐘跌幅超過這個%,標「急殺」——這是抓「速度」的條件

SEVERITY_ORDER = {"正常": 0, "拉回": 1, "急殺": 2}


def compute_reversal_signal(day_high: float | None, last_price: float | None, price_history: list) -> dict:
    """price_history:呼叫端自己在 st.session_state 累積的 [(unix_timestamp, price), ...],
    按時間排序、越新的在越後面(比照 intraday.py 短線動能訊號的既有慣例)。

    回傳 {"pullback_pct", "recent_change_pct", "severity"("正常"/"拉回"/"急殺"), "detail"}。
    兩個百分比欄位在資料不足時是 None,呼叫端顯示時要自己處理。
    """
    pullback_pct = None
    if day_high and last_price is not None and day_high > 0:
        pullback_pct = (day_high - last_price) / day_high * 100

    recent_change_pct = None
    if price_history:
        now_ts, now_price = price_history[-1]
        cutoff = now_ts - FAST_DROP_WINDOW_SECONDS
        baseline = next((p for t, p in price_history if t >= cutoff), None)
        if baseline:
            recent_change_pct = (now_price - baseline) / baseline * 100

    is_severe = (pullback_pct is not None and pullback_pct >= PULLBACK_SEVERE_PCT) or (
        recent_change_pct is not None and recent_change_pct <= -FAST_DROP_SEVERE_PCT
    )
    is_warn = pullback_pct is not None and pullback_pct >= PULLBACK_WARN_PCT

    if is_severe:
        severity = "急殺"
    elif is_warn:
        severity = "拉回"
    else:
        severity = "正常"

    detail_parts = []
    if pullback_pct is not None:
        detail_parts.append(f"距今日高點{day_high:,.2f}拉回{pullback_pct:.2f}%")
    if recent_change_pct is not None:
        detail_parts.append(f"近5分鐘{recent_change_pct:+.2f}%")
    detail = "、".join(detail_parts) if detail_parts else "樣本蒐集中"

    return {
        "pullback_pct": pullback_pct,
        "recent_change_pct": recent_change_pct,
        "severity": severity,
        "detail": detail,
    }


if __name__ == "__main__":
    print("=== 情境1:剛開盤,只有day_high/last_price,沒有價格歷史 ===")
    print(compute_reversal_signal(day_high=100.0, last_price=99.5, price_history=[]))

    print("\n=== 情境2:高點是更早之前創的,近5分鐘持平在低檔(拉回但不算急殺)===")
    hist = [(1000 + i * 10, 98.5) for i in range(20)]  # 近5分鐘持平,沒有快速變動
    print(compute_reversal_signal(day_high=100.0, last_price=hist[-1][1], price_history=hist))

    print("\n=== 情境3:急殺(近5分鐘內從高點快速跳水)===")
    hist = [(1000 + i * 10, 100.0) for i in range(20)] + [(1000 + 20 * 10 + i * 10, 100.0 - i * 0.3) for i in range(1, 6)]
    print(compute_reversal_signal(day_high=100.0, last_price=hist[-1][1], price_history=hist))
