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
from datetime import datetime, time as dt_time, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_PATH = CACHE_DIR / "tx_night_session.csv"

NIGHT_SESSION_URL = "https://www.taifex.com.tw/cht/3/futDailyMarketExcel"

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")
NIGHT_SESSION_START = dt_time(15, 0)
NIGHT_SESSION_END = dt_time(5, 0)
NIGHT_SESSION_TOTAL_MINUTES = 14 * 60  # 15:00~次日05:00,共14小時

# mis.taifex.com.tw 是期交所自己的「行情資訊網」,跟上面 futDailyMarketExcel(每日行情下載,
# 只有結算後的完整一天數字)是完全不同的系統——這個是網頁本身在用的內部API,會隨盤中成交
# 即時變動,才能做到「跟櫃買指數一樣即時監控」。用瀏覽器開發者工具實測抓到:網頁本身用的是
# WebSocket(rtCore)做逐筆推播,但底層 getQuoteList 這支REST API單獨打也能拿到當下快照,
# 不需要另外實作WebSocket,用一般輪詢(每次頁面重新整理打一次)就夠用。回應內容中文欄位
# (DispCName等)編碼是亂碼(跟 futDailyMarketExcel 同樣的期交所網站編碼問題),不受影響的
# 是 SymbolID/數字欄位,所以只取這些欄位,不解析中文名稱。
QUOTE_LIST_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"
QUOTE_LIST_PAYLOAD = {"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "", "ExpireMonth": ""}

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

    # 用台北時區的「今天」,不能用 pd.Timestamp.today()(沒指定時區,會用執行主機的系統時間)——
    # 本機是台灣時間沒差,但 Streamlit Cloud 的雲端主機系統時間是 UTC(比台北慢8小時),
    # 台北時間清晨0~8點這段 UTC 那邊的日曆還停在前一天,pd.bdate_range(end=today,...) 算出來
    # 的候選日期就會漏掉「台北的今天」,導致最新一夜的資料永遠抓不到、卡在前一天不動
    # (使用者實測雲端版「資料日期」卡在前一天,本機不會,已確認是這個時區問題)。
    today = pd.Timestamp(datetime.now(_TAIPEI_TZ).date())
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


SHORT_WINDOW_DAYS_DEFAULT = 3  # 使用者要求「量能比再做個評分跟前三天比較」


def _ratio_to_strength_label(ratio: float | None) -> str | None:
    """量能比換算成熱絡/普通/清淡文字標籤,長期(近20夜)、短期(近3夜)兩個時間窗共用同一套
    門檻(STRENGTH_STRONG_RATIO/STRENGTH_WEAK_RATIO),標籤才有一致的意義。"""
    if ratio is None:
        return None
    if ratio >= STRENGTH_STRONG_RATIO:
        return "熱絡"
    if ratio <= STRENGTH_WEAK_RATIO:
        return "清淡"
    return "普通"


def compute_night_session_strength(
    lookback_days: int = LOOKBACK_DAYS_DEFAULT, short_window_days: int = SHORT_WINDOW_DAYS_DEFAULT
) -> dict | None:
    """最近一個交易日的夜盤成交量,對比「今天以前」近 lookback_days 天的均量,算出參與度
    強弱標記——**這是「參與度/信心」強弱,不是多空方向**,量大只代表交易熱絡,不代表偏多
    或偏空。強弱門檻(1.5倍/0.5倍)比照 `us_market.py` 的既有設計,同樣是主觀訂的,不是
    統計驗證過的數字。

    **近3夜短期比較**:近20夜均量是長期基準,反應比較慢,同一份 hist 資料再另外算一個
    「跟近 short_window_days 夜比」的短期版本(兩者共用同一次 get_night_session_history()
    呼叫,不用多打API)——20夜version看的是「跟長期正常水準比」,3夜version看的是「跟這幾天
    的氣氛比,是不是剛開始轉熱/轉冷」,兩個時間窗一起看才夠全面,單看20夜可能會被過去某幾週
    的特殊事件拉高/拉低基準、蓋掉最近幾天真正的轉折訊號。`short_ratio`/`short_score100` 資料
    不足(少於1天基準)時是 None,呼叫端優雅處理,不是錯誤。

    分數(`long_score100`/`short_score100`)用 `ratio_to_score100()` 統一換算,兩個時間窗
    的分數才能放在同一個0~100尺度上直接比較。

    回傳 {"date", "volume", "avg_volume", "ratio", "strength"("熱絡"/"普通"/"清淡"),
    "front_month_change_pct", "long_score100", "short_window_days", "short_avg_volume",
    "short_ratio", "short_score100"}。資料不足(少於2天)時回傳 None。
    """
    hist = get_night_session_history(lookback_days=lookback_days + 1)
    if len(hist) < 2:
        return None

    latest = hist.iloc[-1]
    baseline = hist["volume"].iloc[:-1]  # 不含最新一天,理由跟 us_market.py 的強弱基準一致
    avg_volume = baseline.mean()
    ratio = latest["volume"] / avg_volume if avg_volume > 0 else None

    strength = _ratio_to_strength_label(ratio)

    short_baseline = baseline.tail(short_window_days)
    short_avg_volume = short_baseline.mean() if len(short_baseline) > 0 else None
    short_ratio = (
        latest["volume"] / short_avg_volume if short_avg_volume is not None and short_avg_volume > 0 else None
    )

    return {
        "date": hist.index[-1].strftime("%Y-%m-%d"),
        "volume": int(latest["volume"]),
        "avg_volume": avg_volume,
        "ratio": ratio,
        "strength": strength,
        "front_month_change_pct": latest["front_month_change_pct"],
        "long_score100": ratio_to_score100(ratio),
        "short_window_days": short_window_days,
        "short_avg_volume": short_avg_volume,
        "short_ratio": short_ratio,
        "short_strength": _ratio_to_strength_label(short_ratio),
        "short_score100": ratio_to_score100(short_ratio),
    }


def is_night_session_open_now(now: datetime | None = None) -> bool:
    """周一到周四晚上15:00~次日05:00(Asia/Taipei)。**週五晚上沒有夜盤**——TAIFEX的規則
    是夜盤要銜接「下一個交易日」才會開,週五晚上後面接的是週六(不是交易日),所以週五
    15:00之後到週六05:00這段實際上不開盤,跟平日晚上不一樣。這是這次抓到的真實bug:
    舊版直接用`weekday() < 5`(週一~週五都算),沒排除週五晚上,結果週五晚上會誤判成
    夜盤中,呼叫`get_tx_live_quote()`拿到的其實是當天日盤收盤時定住不動的最後一筆報價
    (TAIFEX的即時報價API不會因為沒有夜盤就回傳空值,它就是回傳「目前這個合約最後已知的
    狀態」,不會主動說「現在沒有交易」),`compute_live_participation_score()`會把這個
    靜止的舊報價當成活的即時資料下去算分,畫面看起來像是「有夜盤即時評分」但數字永遠不動
    ——使用者回報「這一塊是死的,根本沒有即時評分」正是這個原因。

    跟 intraday.is_market_open_now() 一樣不含國定假日行事曆——颱風假/國定假日恰好是
    平日晚上時仍會誤判成夜盤中,這個影響範圍小(一年就幾天)所以先不修,跟週五這種
    每週固定發生、影響大的情況不一樣。

    凌晨0點~05:00這段屬於「前一個平日晚上15:00」開始的那個夜盤session,所以要往前一天
    判斷「前一天」是不是「有開夜盤的平日」(週一~週四),不能只看「現在」是星期幾——例如
    週五凌晨0~5點算在週四晚上開始的夜盤裡(週四晚上有開盤),週六凌晨0~5點則不算(週五
    晚上沒開盤),週一凌晨0~5點也不算(週日沒有15:00開盤)。
    """
    now = (now or datetime.now(_TAIPEI_TZ)).astimezone(_TAIPEI_TZ)
    t = now.time()
    if t >= NIGHT_SESSION_START:
        return now.weekday() < 4  # 週一(0)~週四(3)晚上才開夜盤,週五(4)晚上不開
    if t <= NIGHT_SESSION_END:
        return (now.weekday() - 1) % 7 < 4  # 前一天要是「有開夜盤的平日」(週一~週四)
    return False


def has_night_session_tonight(now: datetime | None = None) -> bool:
    """今天晚上15:00會不會真的開夜盤(週一~週四才會,週五~週日不會)——給app.py判斷
    「昨晚最終評分」卡片下面那句提示文字該講「今晚15:00開盤後換成即時評分」還是「今晚沒有
    夜盤」時用,跟`is_night_session_open_now()`共用同一個週五排除規則,不要在app.py那邊
    又刻意寫一份`weekday() < 4`的判斷,兩邊各自維護容易漏改。"""
    now = (now or datetime.now(_TAIPEI_TZ)).astimezone(_TAIPEI_TZ)
    return now.weekday() < 4


def _elapsed_night_session_minutes(now: datetime) -> float:
    """從最近一次「平日15:00」算起經過幾分鐘——換算「這個時間點正常應該累積多少量能」
    步調基準用,分鐘級精度就夠用,不需要到秒。"""
    if now.time() >= NIGHT_SESSION_START:
        start = now.replace(hour=15, minute=0, second=0, microsecond=0)
    else:
        start = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    return (now - start).total_seconds() / 60


def get_tx_live_quote() -> dict | None:
    """台指期(TX)近月合約目前最後已知報價——跟 get_night_session_history() 是不同資料源
    (見上面 QUOTE_LIST_URL 的說明)。不管現在是不是真的夜盤中都會回傳(只要API本身正常、
    這個合約有任何已知報價),用於畫面上「現在指數多少」的顯示——不管夜盤有沒有開,使用者
    都應該看得到某個TX指數數字,不是只有夜盤即時評分算得出來才顯示。

    回傳清單裡第一個 SymbolID 以「-F」結尾的項目當近月合約("-S"開頭的是現貨參考指數,
    不是期貨合約;清單本身已經照到期月份由近到遠排序)。API本身異常、或欄位是空字串
    (該API在沒有任何成交紀錄時就是回傳空字串,不是0)時,回傳 None,呼叫端優雅跳過。

    **重要陷阱**:這個API不會因為「還沒開盤/今晚沒有夜盤」就回傳空值——它就是回傳「這個
    合約目前最後已知的狀態」,沒有新成交就一直回傳同一筆舊資料,不會主動說「現在沒有交易」。
    如果晚上15:00剛過還沒真正開始交易(或整個晚上沒有夜盤,例如假日),抓到的會是白天日盤
    收盤時定住的舊報價,`CTime`(最後成交時間)會停在日盤收盤附近(13:4x前後),明顯早於
    夜盤開盤時間15:00。這裡額外算一個`is_live`旗標:如果現在已經過了今天15:00(晚上這段,
    不是跨夜到隔天清晨那段),但報價的CTime還停在15:00之前,代表這其實是舊的日盤收盤報價、
    不是真的夜盤成交,`is_live=False`——呼叫端如果只想要「真的活的夜盤成交」(例如即時評分
    算分)才自己判斷這個旗標決定要不要用;如果只是想顯示「目前指數是多少」(不管新舊都想看
    到一個數字),可以不管這個旗標直接顯示,但要搭配`ctime`告知使用者這筆報價的實際時間,
    不要讓人誤以為那是剛剛發生的成交。跨夜到隔天清晨(00:00~05:00)這段不算`is_live`
    (交由呼叫端自行決定,原因跟之前一樣:合理的CTime範圍橫跨兩個日期,單純比較時間字串
    會誤判,先簡化成一律標True不擋)。這是實際發生過的bug帶出的設計:週五晚上因為
    is_night_session_open_now()誤判成夜盤中(已修正),曾經讓「夜盤即時參與度評分」整晚
    顯示同一個數字看起來像是死的;修完那個bug後,原本的「昨晚最終評分」fallback卡片又完全
    不顯示任何指數數字(因為compute_night_session_strength()的歷史資料源本來就沒有價格
    欄位,只有%漲跌),所以再抽出這支函式讓app.py不管夜盤開不開都能拿到一個指數數字顯示。
    """
    try:
        resp = requests.post(QUOTE_LIST_URL, json=QUOTE_LIST_PAYLOAD, headers=HEADERS, timeout=10)
        quotes = resp.json().get("RtData", {}).get("QuoteList", [])
    except Exception:
        return None

    front_month = next((q for q in quotes if str(q.get("SymbolID", "")).endswith("-F")), None)
    if front_month is None:
        return None

    volume_str = str(front_month.get("CTotalVolume") or "").strip()
    if not volume_str:
        return None
    try:
        volume = int(float(volume_str))
        price_str = str(front_month.get("CLastPrice") or "").strip()
        last_price = float(price_str) if price_str else None
        diff_pct_str = str(front_month.get("CDiffRate") or "").strip()
        change_pct = float(diff_pct_str) if diff_pct_str else None
    except (TypeError, ValueError):
        return None

    ctime_str = str(front_month.get("CTime") or "").strip()
    now = datetime.now(_TAIPEI_TZ)
    is_live = not (now.time() >= NIGHT_SESSION_START and ctime_str and ctime_str < "150000")

    return {
        "symbol_id": front_month["SymbolID"],
        "volume": volume,
        "last_price": last_price,
        "change_pct": change_pct,
        "ctime": ctime_str or None,
        "is_live": is_live,
    }


PACE_MIN_ELAPSED_MINUTES = 15  # 剛開盤沒多久,步調比較(pace_ratio)波動太大不具參考性,先不給分數
PACE_SCORE_SCALE = 50  # ratio=1.0(正常步調)對應50分,分數映射見 ratio_to_score100()


def ratio_to_score100(ratio: float | None) -> int | None:
    """把「量能比 vs 正常」的比值換算成 0~100 單向強度分數——共用同一套映射,讓「盤中
    即時評分」(compute_live_participation_score 的 pace_ratio)跟「昨晚結算後的最終
    評分」(compute_night_session_strength 的 ratio,是完整一夜量能對比近20夜均量,
    不是步調比較)可以放在同一個0~100分數尺度上直接比較——雖然兩者比較基準不同(一個是
    步調基準、一個是完整一夜均量),但「1.0=正常、2.0以上=極熱絡、0=無量」這個相對感覺
    是一致的,分數才有意義互相對照。

    ratio=1.0->50分,ratio>=2.0->100分封頂,ratio=0->0分,None(資料不足)->None。
    """
    if ratio is None:
        return None
    return round(max(0.0, min(100.0, ratio * PACE_SCORE_SCALE)))


def compute_live_participation_score(lookback_days: int = LOOKBACK_DAYS_DEFAULT) -> dict:
    """夜盤盤中即時參與度評分——使用者要求「跟櫃買市場一樣有即時監控的評分分數」,
    比照 app.py 櫃買指數位階(intraday.signal_range_position)的呈現風格,但這裡監控的是
    「參與度」不是「價格位置」,兩者本質不同,分數設計也不一樣:

    **不是方向性分數**:跟 compute_night_session_strength() 的既有立場一致,量能只代表
    參與熱度,不代表多空方向,所以這裡的 score100 是 0~100 的單向強度分數(0=極度清淡,
    100=極度熱絡),不是像櫃買位階那樣 -100~+100 的雙向分數。

    **步調(pace)比較,不是單純的「目前量 vs 歷史全天均量」**:夜盤才進行到一半,直接拿
    目前累積量能比歷史「一整夜」的均量一定會偏低,不能反映「現在算不算熱絡」。這裡改成
    「目前經過的時間佔全部夜盤時段(15:00~次日05:00,共840分鐘)的比例」,乘上歷史均量
    當作「這個時間點正常應該累積多少」的基準(pace baseline),目前累積量能除以這個基準
    才是有意義的即時參考。**這是簡化假設**——假設量能均勻分佈在整個夜盤時段裡,實際上
    開盤前段/尾盤通常比半夜清淡時段熱絡,用這個基準在時段頭尾算出來的比值會有系統性偏差,
    當作粗略參考就好,不是精確的統計模型。

    分數映射:score100 = min(100, max(0, pace_ratio * 50))——pace_ratio=1.0(正常步調)
    →50分,pace_ratio>=2.0→100分封頂,pace_ratio=0(還沒成交)→0分,中間線性內插。
    熱絡/清淡文字標籤沿用跟 compute_night_session_strength() 同一組門檻
    (STRENGTH_STRONG_RATIO/STRENGTH_WEAK_RATIO),數字判斷跟文字標籤才會互相一致。

    回傳 dict 一定有 "available" 這個key:
    - 不在夜盤時段內:{"available": False, "reason": "closed"}
    - 剛開盤未滿 PACE_MIN_ELAPSED_MINUTES 分鐘,或即時報價/歷史均量任一暫時抓不到:
      {"available": False, "reason": "insufficient"}
    - 正常情況:{"available": True, "score100": int, "label": "熱絡"/"普通"/"清淡",
      "pace_ratio": float, "live_volume": int, "expected_volume": float,
      "elapsed_minutes": float, "last_price": float|None, "change_pct": float|None,
      "symbol_id": str}
    """
    now = datetime.now(_TAIPEI_TZ)
    if not is_night_session_open_now(now):
        return {"available": False, "reason": "closed"}

    elapsed_minutes = _elapsed_night_session_minutes(now)
    if elapsed_minutes < PACE_MIN_ELAPSED_MINUTES:
        return {"available": False, "reason": "insufficient"}

    live = get_tx_live_quote()
    if live is None or not live["is_live"]:
        return {"available": False, "reason": "insufficient"}

    hist = get_night_session_history(lookback_days=lookback_days)
    if len(hist) < 2 or hist["volume"].mean() <= 0:
        return {"available": False, "reason": "insufficient"}
    avg_full_session_volume = hist["volume"].mean()

    elapsed_fraction = min(1.0, elapsed_minutes / NIGHT_SESSION_TOTAL_MINUTES)
    expected_volume = avg_full_session_volume * elapsed_fraction
    if expected_volume <= 0:
        return {"available": False, "reason": "insufficient"}
    pace_ratio = live["volume"] / expected_volume

    score100 = ratio_to_score100(pace_ratio)
    if pace_ratio >= STRENGTH_STRONG_RATIO:
        label = "熱絡"
    elif pace_ratio <= STRENGTH_WEAK_RATIO:
        label = "清淡"
    else:
        label = "普通"

    return {
        "available": True,
        "score100": score100,
        "label": label,
        "pace_ratio": pace_ratio,
        "live_volume": live["volume"],
        "expected_volume": expected_volume,
        "elapsed_minutes": elapsed_minutes,
        "last_price": live["last_price"],
        "change_pct": live["change_pct"],
        "symbol_id": live["symbol_id"],
    }


if __name__ == "__main__":
    print("=== 台指期(TX)夜盤參與度 ===")
    result = compute_night_session_strength()
    if result:
        print(f"日期:{result['date']}")
        print(f"夜盤成交量:{result['volume']:,} 口")
        print(f"近期均量:{result['avg_volume']:,.0f} 口")
        print(f"量能比:{result['ratio']:.2f}倍 → {result['strength']}(評分{result['long_score100']})")
        if result["short_ratio"] is not None:
            print(f"近{result['short_window_days']}夜量能比:{result['short_ratio']:.2f}倍(評分{result['short_score100']})")
        print(f"近月合約當晚漲跌:{result['front_month_change_pct']}%")
    else:
        print("資料不足")

    print()
    print("=== 台指期(TX)夜盤即時參與度評分 ===")
    live_result = compute_live_participation_score()
    if live_result["available"]:
        print(f"合約:{live_result['symbol_id']}")
        print(f"分數:{live_result['score100']} → {live_result['label']}")
        print(f"步調比(pace_ratio):{live_result['pace_ratio']:.2f}")
        print(f"目前累積量能:{live_result['live_volume']:,} 口(已經過 {live_result['elapsed_minutes']:.0f} 分鐘,"
              f"正常步調基準約 {live_result['expected_volume']:,.0f} 口)")
        print(f"最新成交價:{live_result['last_price']}  漲跌%:{live_result['change_pct']}")
    elif live_result["reason"] == "closed":
        print("目前非夜盤交易時段(平日15:00~次日05:00)")
    else:
        print("剛開盤或資料暫時不足,還無法評分")
