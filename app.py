"""股市查詢模型 — Streamlit 網頁介面。

執行方式: streamlit run app.py
"""

import base64
import json
import time as time_module
from pathlib import Path

import pandas as pd
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as components

import calendar_events
import intraday
import night_session
import reversal_alert
from fetch_data import (
    get_quote,
    get_history,
    to_yf_symbol,
    get_chinese_name,
    get_tpex_otc_index_quote,
    get_tpex_otc_index_previous_day,
    update_otc_index_history_cache,
)
from indicators import add_indicators
from volume_profile import find_nearest_supports, find_nearest_resistances
from us_market import (
    get_us_overnight_signal,
    US_MARKET_SYMBOLS,
    SCORE_SYMBOLS,
    INVERTED_SYMBOLS,
    effective_sign,
    STRENGTH_WEIGHT,
    SCORE_STRONG_THRESHOLD,
)
from xq_branch import XQ_BRANCH_DIR

LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"
st.set_page_config(page_title="台股查詢模型", page_icon=str(LOGO_PATH), layout="wide")


@st.cache_data
def _load_logo_img_tag() -> str:
    """把Logo讀成base64內嵌<img>標籤,取代原本標題文字前面的📈 emoji——用於直接嵌在
    markdown/HTML字串裡(st.markdown的f-string),跟st.image()那種需要獨立版面位置的用法
    不同,這裡要跟文字同一行。圖檔不會變,用@st.cache_data包住只讀一次檔案。"""
    b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
    return f'<img src="data:image/png;base64,{b64}" style="height:4.4rem; vertical-align:middle; margin-right:0.5rem; margin-top:-0.3rem; margin-bottom:-0.3rem;">'


LOGO_IMG_TAG = _load_logo_img_tag()

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+TC:wght@400;500;700&display=swap');

:root {
    /* 配色取自 ui-ux-pro-max 技能的 Banking/Traditional Finance 產業色票(信賴藏青 + 質感金),
       並將金色調亮以符合暗色模式下的 WCAG 對比需求 */
    --tw-up: #ef4444;      /* 台股慣例:漲為紅 */
    --tw-down: #22c55e;    /* 跌為綠 */
    --accent-gold: #eab308;
    --card-bg: #0e1223;
    --card-border: rgba(255,255,255,0.07);
}

html, body, [class*="css"] {
    font-family: "Inter", "Noto Sans TC", -apple-system, sans-serif;
}

.block-container { padding-top: 1.6rem; max-width: 1180px; }

/* 頂部標頭 */
.app-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.6rem;
    padding-bottom: 0.4rem;
    margin-bottom: 1.2rem;
    border-bottom: 1px solid var(--card-border);
}
.app-header h1 { font-size: 1.5rem; margin: 0; font-weight: 700; letter-spacing: 0.3px; }
.app-header .sub { color: #8b93a7; font-size: 0.85rem; }
.app-header .badge-gold {
    display: inline-block; color: var(--accent-gold); background: rgba(234,179,8,0.12);
    border: 1px solid rgba(234,179,8,0.25); border-radius: 8px; padding: 0.15rem 0.55rem;
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.4px; margin-left: 0.6rem; vertical-align: middle;
}

/* 報價卡片 */
.quote-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1.2rem;
}
.quote-symbol { color: #8b93a7; font-size: 0.9rem; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }
.quote-price-row { display: flex; align-items: baseline; gap: 0.9rem; margin: 0.2rem 0 1rem 0; flex-wrap: wrap; }
.quote-price { font-size: 3rem; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.quote-badge { font-size: 1.05rem; font-weight: 700; padding: 0.25rem 0.7rem; border-radius: 10px; }
.badge-up { color: var(--tw-up); background: rgba(239,68,68,0.12); }
.badge-down { color: var(--tw-down); background: rgba(34,197,94,0.12); }
.badge-flat { color: #9ca3af; background: rgba(156,163,175,0.12); }
/* 條件達成/未達成徽章——維持使用者習慣的紅綠燈直覺(達成=綠、未達成=紅),
   跟quote-badge的漲跌紅綠是不同語意脈絡,不會混在一起看 */
.badge-hit { color: #22c55e; background: rgba(34,197,94,0.14); }
.badge-miss { color: #ef4444; background: rgba(239,68,68,0.12); }

.stat-row { display: flex; gap: 2.2rem; flex-wrap: wrap; }
.stat-item .label { color: #8b93a7; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.4px; }
.stat-item .value { font-size: 1.15rem; font-weight: 600; font-variant-numeric: tabular-nums; }

/* 一般卡片區塊 */
.section-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 1.3rem 1.5rem;
    margin-bottom: 1rem;
}
.section-title {
    font-size: 1rem; font-weight: 700; margin-bottom: 0.8rem; color: #e5e7eb;
    padding-left: 0.7rem; border-left: 3px solid var(--accent-gold);
}
.disclaimer { color: #8b93a7; font-size: 0.8rem; margin-top: 0.6rem; }

[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


AUTO_REFRESH_SECONDS = 20

# 這次腳本執行「開始前」記錄的上一次執行時間——不管上次是使用者互動、計時器、還是任何其他
# 原因觸發的重跑都算。用意見下面 _auto_refresh_tick() 的說明(側邊欄「⚙️盤中急殺警示設定」
# 數值卡住的成因)。每次都無條件更新,不受任何條件影響。
_prev_script_run_time = st.session_state.get("_last_script_run_time", 0)
st.session_state["_last_script_run_time"] = time_module.time()


@st.fragment(run_every=AUTO_REFRESH_SECONDS)
def _auto_refresh_tick():
    """純計時器,不畫任何東西——每20秒觸發一次 st.rerun()(預設 scope="app",整頁重跑,
    不是只重跑這個 fragment 自己),讓各區塊的 st.cache_data 過期後能盡快自動撈到新資料,
    不用使用者手動重新整理瀏覽器。原本是60秒,使用者覺得太慢、參考fly.io背景監控
    (alert_monitor.py,15秒輪詢一次)改成20秒——大部分快取TTL還是300秒(5分鐘)、夜盤是
    1800秒沒有跟著變動,這些區塊會有多次重繪拿不到新資料的浪費,但換來的是真正即時的部分
    (個股/大盤/櫃買指數的TWSE MIS報價、K線快取已對齊縮到20秒、台指期夜盤即時參與度評分)
    更新感受明顯變快,使用者已權衡過這個取捨、接受多打幾次API換即時感。

    用 session_state 記錄上次真的觸發 rerun 的時間點,靠這個判斷「這次呼叫是不是計時器到期
    才觸發的」——如果不這樣擋,這個 function 每次被呼叫(包括使用者互動造成的全頁重跑,
    或這個 function 自己呼叫 st.rerun() 之後緊接著的那次重跑)都會無條件立刻再呼叫一次
    st.rerun(),變成無窮迴圈(第一版就是這樣寫,結果整頁卡死變空白,一直重跑出不去)。

    另外加一個「距離上一次不管什麼原因觸發的重跑」門檻(_prev_script_run_time)——原本只看
    「距離上次計時器自己觸發的重跑」,沒考慮到使用者互動(例如編輯側邊欄「盤中急殺警示設定」
    的門檻數字)本身也是一次全頁重跑。實測發現:如果計時器的60秒倒數剛好跟使用者才送出的
    編輯前後腳到,兩個重跑會搶著執行,可能導致使用者剛打好的新門檻值被計時器這次重跑蓋掉
    (用到的還是編輯生效前的舊值)——畫面上數字看起來有改,但警示文字/換算股價卻沒有跟著
    新數字重新判斷,就是使用者回報的「設定錯誤跳出警示後,數值會卡住」。多這道門檻確保計時器
    重跑跟其他任何重跑之間至少間隔5秒,不會搶在使用者編輯還沒穩定生效前硬插進來。

    第三道門檻(_long_task_running)防的是另一個坑:RS排行/批次燈號掃描這種一次要逐檔抓
    上百檔股票資料的按鈕,運算本身動輒超過60秒——這個計時器的 st.rerun() 預設是整頁重跑
    (scope="app"),不管使用者正在等哪個按鈕的運算跑到一半,只要60秒一到就會把還在執行中的
    腳本直接中斷重來,算好的結果整個消失,批次越大越跑不完(股票池夠大的話永遠卡在中途)。
    這兩個按鈕的程式碼會在開始運算前把 _long_task_running 設成True、結束後(含例外)用
    finally清回False,這裡看到是True就先跳過這次rerun,等運算結束後下一次60秒到期就會
    正常補上。"""
    now = time_module.time()
    last = st.session_state.get("_auto_refresh_last_tick", 0)
    if (
        now - last >= AUTO_REFRESH_SECONDS
        and now - _prev_script_run_time >= 5
        and not st.session_state.get("_long_task_running")
    ):
        st.session_state["_auto_refresh_last_tick"] = now
        st.rerun()


with st.sidebar:
    st.markdown(f"### {LOGO_IMG_TAG} 台股查詢模型", unsafe_allow_html=True)
    st.caption("即時報價・技術指標・做多訊號")
    st.divider()
    auto_refresh = st.checkbox(f"🔄 自動刷新(每{AUTO_REFRESH_SECONDS}秒)", value=True,
                                help=f"每{AUTO_REFRESH_SECONDS}秒自動重新整理頁面。各區塊資料實際更新頻率仍取決於"
                                     "各自的快取有效期(大部分5分鐘,夜盤30分鐘),這個開關只是"
                                     "確保快取過期後不用手動重整就能盡快看到新資料。")
    if auto_refresh:
        _auto_refresh_tick()
    keep_screen_awake = st.checkbox(
        "🔆 螢幕保持常亮", value=True,
        help="開著這個分頁時不讓螢幕自動變暗/鎖定,關掉這個開關或關掉分頁就恢復系統原本的省電設定。"
             "靠瀏覽器內建的Screen Wake Lock API,不用另外裝任何東西——桌面版Chrome/Edge/最新版"
             "Safari都支援,少數瀏覽器不支援的話就是沒作用、不會報錯。",
    )
    if keep_screen_awake:
        # Screen Wake Lock API 只在使用者主動要求時才申請(這個checkbox本身就是那個要求),
        # 拿到的鎖只在這個分頁還開著、還在前景時有效——分頁被切到背景鎖會自動釋放,所以額外
        # 監聽visibilitychange,切回前景時重新申請一次。這段HTML每次腳本重跑(例如auto_refresh
        # 每60秒重跑一次)都會重新插入、重新申請一次鎖,等於順便定期補鎖,不用擔心中途意外釋放
        # 後就再也不會恢復。不支援這個API的瀏覽器會被catch住,靜默不作用,不影響其他功能。
        components.html(
            """
            <script>
            (async () => {
                try {
                    if (!('wakeLock' in navigator)) return;
                    await navigator.wakeLock.request('screen');
                    document.addEventListener('visibilitychange', async () => {
                        if (document.visibilityState === 'visible') {
                            try { await navigator.wakeLock.request('screen'); } catch (e) {}
                        }
                    });
                } catch (err) {
                    console.warn('螢幕常亮(Wake Lock)申請失敗,瀏覽器可能不支援或不允許:', err);
                }
            })();
            </script>
            """,
            height=0,
        )
    st.divider()
    query_mode = st.radio("查詢標的", ["個股", "加權指數", "櫃買指數"], horizontal=True)
    if query_mode == "個股":
        code = st.text_input("股票代號", value="2330", help="輸入純數字代號,例如 2330")
        otc = st.checkbox("上櫃股票", value=False, help="預設為上市股票")
    else:
        # 指數代號直接寫死,不透過股票代號輸入框——加權/櫃買指數不是個股,沒有上市/上櫃之分,
        # yfinance 用「^」開頭的代號代表指數(fetch_data.to_yf_symbol 會原樣放行不加 .TW/.TWO)。
        code = "^TWII" if query_mode == "加權指數" else "^TWOII"
        otc = False
        st.caption(
            "櫃買指數(^TWOII)改抓TWSE MIS即時報價(真即時,非延遲)。K線圖/籌碼支撐阻力"
            "靠本地逐次累積(沒有任意區間可選,每次查詢自動累積新的一個月),做多訊號/"
            "分點掃描需要三大法人/權證資料,指數沒有,不支援。"
            if query_mode == "櫃買指數" else "加權指數(^TWII)功能跟查個股一樣完整。"
        )
    period = st.selectbox("歷史資料區間", ["3mo", "6mo", "1y", "2y", "5y"], index=2)
    st.divider()

    # 急殺警示,原本只能在 alert_config.py(背景推播腳本用的設定檔)裡改門檻,網頁上完全
    # 沒有對應的輸入欄位。第一版補了門檻輸入框,但沒勾選任何東西的狀況下警示卡片還是
    # 一直用系統預設值跑——使用者明確要求「要有勾選才能啟動警示設定,沒有預設」,改成
    # 這個checkbox現在是「整個急殺警示功能」的總開關,不勾就完全不顯示、也不會多打即時
    # 報價API去輪詢,不是「用預設值默默跑在背景」。
    #
    # 門檻數字改成「每檔股票/指數各自分開記」——使用者發現原本3個門檻是綁在側邊欄的全域
    # 設定,不管切換到哪一檔股票都是同一組數字,反應「好像會套用在全部股票」,明確要求
    # 改成分開記。
    #
    # **不能只靠「key依股票代號變化」這招**——實測過改成 key=f"...{target_key}" 之後,
    # 切到別的股票再切回來,原本設定的值竟然又變回預設值了。原因是 Streamlit 的規則是
    # 「一個 widget key 如果這次 rerun 沒有被實際建立(instantiate),它在 session_state
    # 裡的值就會被清掉」——換股票的當下,舊股票那組 key 的 number_input 沒有被畫出來,
    # 值就跟著沒了,不是像一般 session_state 那樣切走再切回來還留著。
    #
    # 改成自己手動維護一個 `_reversal_thresholds` dict(key是target_key,不受widget
    # 掛載/卸載影響,是一般session_state,不會被清),widget本身用固定的key,只在偵測到
    # 「目標換了」的時候,才在建立widget之前手動把session_state裡widget的值換成這個新
    # 目標存的設定(沒存過就用全域預設),widget渲染完之後把目前的值寫回dict存起來。
    target_key = f"{code}_{otc}"
    thresholds_store = st.session_state.setdefault("_reversal_thresholds", {})
    if st.session_state.get("_reversal_target_key") != target_key:
        saved = thresholds_store.get(target_key, {})
        st.session_state["pullback_warn_pct_widget"] = saved.get("warn", reversal_alert.PULLBACK_WARN_PCT)
        st.session_state["pullback_severe_pct_widget"] = saved.get("severe", reversal_alert.PULLBACK_SEVERE_PCT)
        st.session_state["fast_drop_severe_pct_widget"] = saved.get("fast", reversal_alert.FAST_DROP_SEVERE_PCT)
        st.session_state["_reversal_target_key"] = target_key

    with st.expander("⚙️ 盤中急殺警示設定"):
        st.caption("距今日高點拉回%、或近5分鐘變動%,任一超過門檻就升級警示。預設不啟用,"
                   "勾選下面的開關才會顯示警示卡片、開始輪詢即時報價。門檻數字每檔股票/"
                   "指數分開記憶,換一檔查詢不會互相影響。")
        enable_reversal_alert = st.checkbox(
            "啟用盤中急殺警示", value=False,
            help="不勾選就完全不顯示急殺警示卡片,也不會多打TWSE即時報價API;"
                 "勾選後才會用下面填的門檻開始監控。這個開關是全域的,不分股票。",
        )
        custom_pullback_warn_pct = st.number_input(
            "拉回警示門檻(%)", min_value=0.1, max_value=20.0,
            step=0.1, disabled=not enable_reversal_alert, key="pullback_warn_pct_widget",
        )
        custom_pullback_severe_pct = st.number_input(
            "急殺警示門檻(%,距高點拉回)", min_value=0.1, max_value=30.0,
            step=0.1, disabled=not enable_reversal_alert, key="pullback_severe_pct_widget",
        )
        custom_fast_drop_severe_pct = st.number_input(
            "急殺警示門檻(%,近5分鐘變動)", min_value=0.1, max_value=10.0,
            step=0.1, disabled=not enable_reversal_alert, key="fast_drop_severe_pct_widget",
        )
        # 每次重跑都把目前3個widget的值同步寫回目前目標的儲存格——使用者剛編輯完的新值
        # (不管是不是這次剛換目標)都要存起來,下次換回這檔才找得到。
        thresholds_store[target_key] = {
            "warn": custom_pullback_warn_pct,
            "severe": custom_pullback_severe_pct,
            "fast": custom_fast_drop_severe_pct,
        }
        # 用 st.empty() 佔位再每次重跑都明確寫入/清空,不要單純寫「if 條件: st.warning(...)」——
        # 實測發現這種寫法在使用者把門檻設定「錯誤→改對→改回錯誤」來回切換時,第二次進入
        # 錯誤狀態時警示文字不會重新跳出來(卡住),要用 st.empty() 佔位子每次都強制整個替換
        # 內容才會穩定重新顯示。
        threshold_warning_slot = st.empty()
        if enable_reversal_alert and custom_pullback_severe_pct < custom_pullback_warn_pct:
            threshold_warning_slot.warning(
                "急殺門檻比拉回門檻還小,「拉回」這個中間等級實際上不會出現,"
                "拉回超過拉回門檻就會直接跳成急殺——如果不是故意的,建議急殺門檻"
                "設得比拉回門檻大。"
            )
        else:
            threshold_warning_slot.empty()

        if enable_reversal_alert:
            # 上面兩個number_input只有調完按Enter/失焦才會觸發rerun,不會邊打邊即時換算——
            # 這裡改用「這檔標的上一次成功抓到的今日高點」(存在session_state,下面報價
            # 區塊每次抓到新資料就會更新)換算成價格,直接顯示在設定區塊裡,不用捲到下面
            # 的警示卡片才看得到對應股價。第一次查詢這檔標的、還沒有快取時顯示提示文字。
            _preview_code = code if query_mode == "個股" else ("^TWII" if query_mode == "加權指數" else "^TWOII")
            _preview_day_high = st.session_state.get(f"_last_day_high_{_preview_code}")
            if _preview_day_high:
                st.caption(
                    f"換算參考(依上次查到的今日高點 {_preview_day_high:,.2f}):"
                    f"拉回門檻價 **{_preview_day_high * (1 - custom_pullback_warn_pct / 100):,.2f}**、"
                    f"急殺門檻價 **{_preview_day_high * (1 - custom_pullback_severe_pct / 100):,.2f}**"
                )
            else:
                st.caption("這檔標的還沒有查過資料,查詢一次之後這裡會顯示換算股價參考。")

    st.divider()

    # 雲端部署版本沒有本機的 xq_branch_data/(.dsl/CSV 個人資料被 .gitignore 排除,不會上傳
    # 到 GitHub),導致「檢查整個.dsl股票池」「RS排行」「分點掃描」這幾個功能在雲端版本上
    # 會是空的。加這個上傳功能讓使用者可以直接在網頁上把本機匯出的檔案傳上來,存到雲端App
    # 當次執行的暫存空間——不透過 git,所以不會把個人資料留在版控歷史裡。
    with st.expander("📤 上傳個人資料(.dsl / CSV)"):
        st.caption(
            "雲端版本沒有你本機的 .dsl 自選清單/分點CSV(個人資料不會上傳到GitHub)。"
            "在這裡上傳後,「檢查整個.dsl股票池」「RS排行」「分點掃描」才會抓得到資料——"
            "但這裡存的是這次雲端App執行期間的暫存空間,App閒置一段時間重啟後就會消失,"
            "要用的時候可能得重新上傳一次,不像本機是永久保存。"
        )
        uploaded_files = st.file_uploader(
            "選擇 .dsl 或 .csv 檔案", type=["dsl", "csv"], accept_multiple_files=True
        )
        if uploaded_files:
            for f in uploaded_files:
                (XQ_BRANCH_DIR / f.name).write_bytes(f.getbuffer())
            st.success(f"已存入 {len(uploaded_files)} 個檔案")

        existing = sorted(p.name for p in XQ_BRANCH_DIR.glob("*") if p.suffix in (".dsl", ".csv"))
        if existing:
            st.caption("目前資料夾裡的檔案:" + "、".join(existing))
        else:
            st.caption("目前資料夾裡還沒有任何 .dsl/.csv 檔案。")

    st.divider()
    st.caption("學習用途,所有數字僅供參考,不構成投資建議。")


@st.cache_data(ttl=300)
def load_quote(code, otc):
    return get_quote(code, otc=otc)


@st.cache_data(ttl=300)
def load_tpex_otc_index_quote():
    return get_tpex_otc_index_quote()


@st.cache_data(ttl=3600)
def load_name(code, otc):
    symbol = to_yf_symbol(code, otc)
    try:
        english_name = yf.Ticker(symbol).info.get("longName")
    except Exception:
        english_name = None
    english_name = english_name or symbol

    try:
        chinese_name = get_chinese_name(code, otc=otc)
    except Exception:
        chinese_name = None

    return f"{english_name}({chinese_name})" if chinese_name else english_name


@st.cache_data(ttl=AUTO_REFRESH_SECONDS)  # 對齊AUTO_REFRESH_SECONDS——使用者要看漲跌判斷,K線/均線/
                        # 支撐阻力這些跟著自動刷新一起變新,不要卡在舊的5分鐘快取裡沒更新
def load_history_with_indicators(code, period, otc):
    hist = get_history(code, period=period, otc=otc)
    return add_indicators(hist)


@st.cache_data(ttl=300)
def load_us_overnight_signal():
    """包一層st.cache_data降低yfinance呼叫頻率——這個wrapper的快取只認自己(這幾行)的
    原始碼有沒有變,不會追蹤它呼叫的us_market.get_us_overnight_signal()內部邏輯改了沒。
    任何一次修改us_market.py的計分/資料邏輯(例如這次從2指標計分擴充成5指標:小道瓊/
    那斯達克期貨/KOSPI/美元新台幣/VIX),都要記得順手在這裡也留一點改動(哪怕只是更新
    這段說明的版本註記),強制讓快取key跟著變、逼新部署立刻重新計算一次,不要依賴TTL
    自然過期(這裡TTL=300秒,最壞情況會顯示錯誤/舊數字長達5分鐘)。"""
    return get_us_overnight_signal()


@st.cache_data(ttl=1800)
def load_night_session_strength():
    return night_session.compute_night_session_strength()


def _build_score_gauge_html(score, score_max, strong_threshold) -> str:
    """一條 -score_max ~ +score_max 的橫條,從中間(0分)往漲/跌那一側填色,一眼看出這次
    分數離「打平」還是「滿分」有多遠,比只看數字直覺。刻度是兩端(滿分)、中間(0分)、加上
    強弱門檻(strong_threshold)共5個,直接對應「強多/偏多/中性/偏空/強空」五級的分界。
    美股夜盤連動指標(±SCORE_MAX、門檻 SCORE_STRONG_THRESHOLD)跟盤中強弱(±100、門檻50)
    共用這個 helper,分數範圍不同所以參數化。score 是 None(資料不足)時回傳空字串。
    """
    if score is None:
        return ""
    pct = max(0.0, min(100.0, (score + score_max) / (2 * score_max) * 100))
    if score > 0:
        fill_left, fill_width, fill_color = 50, pct - 50, "var(--tw-up)"
    elif score < 0:
        fill_left, fill_width, fill_color = pct, 50 - pct, "var(--tw-down)"
    else:
        fill_left, fill_width, fill_color = 50, 0, "#9ca3af"

    def _tick_pct(t):
        return (t + score_max) / (2 * score_max) * 100

    def _tick_label(t):
        if t == -score_max:
            return f"強空{t:+.0f}"
        if t == score_max:
            return f"強多{t:+.0f}"
        return "0" if t == 0 else f"{t:+.0f}"

    def _tick_transform(t):
        if t == -score_max:
            return "0%"
        if t == score_max:
            return "-100%"
        return "-50%"

    tick_scores = [-score_max, -strong_threshold, 0, strong_threshold, score_max]
    # 刻意寫成單行、不縮排——Streamlit 的 markdown 引擎看到多行 HTML 裡有 4 個空白以上的
    # 縮排會誤判成「縮排程式碼區塊」,導致後面接著的 stat-row HTML 整段變成純文字顯示,
    # 不會被當成 HTML 渲染(先前這裡用多行縮排字串時就踩到這個坑,改單行後就正常了)。
    ticks_html = "".join(
        f'<div style="position:absolute; left:{_tick_pct(t)}%; top:-3px; bottom:-3px; width:1px; '
        f'background:rgba(255,255,255,{0.35 if t == 0 else 0.18});"></div>'
        for t in tick_scores
    )
    labels_html = "".join(
        f'<span style="position:absolute; left:{_tick_pct(t)}%; transform:translateX({_tick_transform(t)}); '
        f'white-space:nowrap;">{_tick_label(t)}</span>'
        for t in tick_scores
    )
    return (
        '<div style="margin-top:0.8rem; max-width:360px;">'
        '<div style="position:relative; height:8px; background:rgba(255,255,255,0.08); border-radius:4px;">'
        f"{ticks_html}"
        f'<div style="position:absolute; left:{fill_left}%; width:{fill_width}%; top:0; bottom:0; background:{fill_color}; border-radius:4px;"></div>'
        "</div>"
        f'<div style="position:relative; height:1rem; font-size:0.68rem; color:#6b7280; margin-top:3px;">{labels_html}</div>'
        "</div>"
    )


def _build_participation_gauge_html(score100: float) -> str:
    """0~100 單向強度橫條,跟 _build_score_gauge_html() 的正負雙向設計不同——台指期夜盤
    參與度只有「熱絡/清淡」的強度之分,沒有多空方向(見 night_session.py 的既有立場),
    用雙向橫條(中間0分、兩端滿分)會誤導成「這是多空分數」,所以另外寫一個單向版本。
    刻度25/75取自 night_session.STRENGTH_WEAK_RATIO/STRENGTH_STRONG_RATIO 換算成分數
    後的位置,跟「清淡/普通/熱絡」文字標籤的分界一致。"""
    pct = max(0.0, min(100.0, score100))
    color = _participation_score_color(pct)
    ticks = [(0, "清淡0"), (25, "25"), (50, "普通50"), (75, "75"), (100, "熱絡100")]
    ticks_html = "".join(f'<div style="position:absolute; left:{t}%; top:-3px; bottom:-3px; width:1px; background:rgba(255,255,255,{0.35 if t in (25, 75) else 0.18});"></div>' for t, _ in ticks)
    labels_html = "".join(f'<span style="position:absolute; left:{t}%; transform:translateX({"0%" if t == 0 else ("-100%" if t == 100 else "-50%")}); white-space:nowrap;">{lbl}</span>' for t, lbl in ticks)
    return (
        '<div style="margin-top:0.8rem; max-width:360px;">'
        '<div style="position:relative; height:8px; background:rgba(255,255,255,0.08); border-radius:4px;">'
        f"{ticks_html}"
        f'<div style="position:absolute; left:0%; width:{pct}%; top:0; bottom:0; background:{color}; border-radius:4px;"></div>'
        "</div>"
        f'<div style="position:relative; height:1rem; font-size:0.68rem; color:#6b7280; margin-top:3px;">{labels_html}</div>'
        "</div>"
    )


def _build_condition_badge_html(label: str, passed: bool) -> str:
    """🚦條件達成燈號用的狀態徽章,取代原本的🟢/🔴 emoji——emoji在不同系統/字型下顏色不受控,
    跟頁面其他地方統一用CSS色點+文字的視覺語言(見quote-badge)不一致,改用CSS徽章維持一樣的
    紅綠燈直覺(達成=綠、未達成=紅,badge-hit/badge-miss,定義在CSS裡)。"""
    badge_class = "badge-hit" if passed else "badge-miss"
    status_text = "達成" if passed else "未達成"
    return (
        f'<div style="font-size:0.8rem; color:#8b93a7; margin-bottom:0.3rem; min-height:2.2rem;">{label}</div>'
        f'<span class="quote-badge {badge_class}" style="font-size:0.82rem; padding:0.2rem 0.6rem;">{status_text}</span>'
    )


def _participation_score_color(score100: float) -> str:
    """0~100參與度分數(熱絡/普通/清淡)對應的顏色,集中一處給gauge/即時分數/昨晚分數/
    強弱badge共用,避免各自複製貼上同一組門檻+顏色,以後要調色只要改這裡一處。"""
    if score100 >= 75:
        return "#f97316"
    if score100 <= 25:
        return "#38bdf8"
    return "#9ca3af"


def _participation_badge_html(text: str, score100: float | None) -> str:
    """強弱badge(熱絡/普通/清淡)改依分數上色,不要固定都是灰色——沿用
    _participation_score_color() 同一套門檻,跟旁邊的大分數數字/量表指針是同一套顏色語言。
    分數拿不到(舊快取字典缺欄位等情況)才退回中性灰。"""
    color = _participation_score_color(score100) if score100 is not None else "#9ca3af"
    return f'<div class="quote-badge" style="color:{color}; background:{color}1f;">{text}</div>'


def _build_level_badges_html(levels: list, current_price: float, color: str) -> str:
    """支撐/阻力價位的徽章列,取代原本純文字caption——顏色直接沿用CHART_THEME裡
    support/resistance的顏色,讓這裡跟K線圖上畫的支撐/阻力線是同一套顏色語言,
    不用另外發明一套配色,看圖時可以直接把caption跟線對起來。"""
    pills = "".join(
        f'<span style="display:inline-block; margin:0.2rem 0.4rem 0.2rem 0; padding:0.2rem 0.6rem; '
        f'border-radius:8px; font-size:0.82rem; font-weight:600; font-variant-numeric:tabular-nums; '
        f'color:{color}; background:{color}1f; border:1px solid {color}40;">'
        f'{lv["price"]:,.2f}<span style="font-weight:400; opacity:0.75;"> ({lv["price"] / current_price - 1:+.1%})</span></span>'
        for lv in levels
    )
    return f'<div style="margin-top:0.3rem;">{pills}</div>'


def _render_reversal_banner(signal: dict) -> None:
    """盤中急殺/反轉警示(見 reversal_alert.py)的畫面呈現,個股跟指數(加權/櫃買)共用。
    永遠顯示(不是只在有警示時才出現),讓使用者能確認「這個監控目前是活的」,而不是
    誤以為畫面一片空白代表沒在運作。"""
    severity = signal["severity"]
    if severity == "急殺":
        st.error(f"🔻 急殺警示:{signal['detail']}")
    elif severity == "拉回":
        st.warning(f"⚠️ 拉回:{signal['detail']}")
    else:
        st.success(f"✅ 正常:{signal['detail']}")


def _make_index_reversal_fragment(
    mis_code: str,
    mis_otc: bool,
    display_symbol: str,
    market_open: bool,
    pullback_warn_pct: float | None,
    pullback_severe_pct: float | None,
    fast_drop_severe_pct: float | None,
):
    """加權指數/櫃買指數共用的「⚠️盤中急殺警示」區塊,包成工廠函式(不是直接定義一次
    fragment)是因為呼叫的位置不一樣——^TWII 走一般流程(後面才有K線/df可以用),^TWOII
    在報價卡片那段就直接 st.stop()(這個指數沒有歷史資料,後面的區塊完全用不到),兩邊
    都要在各自的 st.stop() 之前呼叫這個,不能共用同一個「寫死在某個位置」的 fragment。

    pullback_warn_pct/pullback_severe_pct/fast_drop_severe_pct 是側邊欄「⚙️急殺警示門檻
    設定」讀出來的值,使用者沒勾選「使用自訂門檻」時是 None(對應checkbox不勾選=用預設值)
    ——這裡先把 None 換成 reversal_alert.py 的全域預設值,一方面是給 compute_reversal_signal
    的關鍵字參數(它自己也認得 None,這裡先轉換純粹是為了下面caption字串裡要格式化成數字,
    傳None進去f-string的{:.1f}會直接噴TypeError,不是為了邏輯需要)。

    只做急殺警示,不做像個股那樣的5訊號綜合評分——指數沒有近5日均量/委買委賣力道以外的
    訊號可以組成那一套評分邏輯,硬套意義不大。
    """
    pullback_warn_pct = reversal_alert.PULLBACK_WARN_PCT if pullback_warn_pct is None else pullback_warn_pct
    pullback_severe_pct = reversal_alert.PULLBACK_SEVERE_PCT if pullback_severe_pct is None else pullback_severe_pct
    fast_drop_severe_pct = reversal_alert.FAST_DROP_SEVERE_PCT if fast_drop_severe_pct is None else fast_drop_severe_pct

    history_key = f"reversal_price_history_{mis_code}_{mis_otc}"
    st.session_state.setdefault(history_key, [])

    @st.fragment(run_every=10 if market_open else None)
    def _render():
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">⚠️ 盤中急殺警示</div>', unsafe_allow_html=True)

        iq = intraday.get_intraday_quote(mis_code, mis_otc)
        if iq is None or iq["last_price"] is None:
            st.info("盤中即時資料暫時無法取得(可能尚未開盤)。")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        hist = st.session_state[history_key]
        hist.append((time_module.time(), iq["last_price"]))
        st.session_state[history_key] = hist[-30:]

        signal = reversal_alert.compute_reversal_signal(
            iq.get("day_high"),
            iq["last_price"],
            st.session_state[history_key],
            pullback_warn_pct=pullback_warn_pct,
            pullback_severe_pct=pullback_severe_pct,
            fast_drop_severe_pct=fast_drop_severe_pct,
        )

        st.markdown(
            f"""
            <div class="quote-card">
                <div class="quote-symbol">{display_symbol} · {name}</div>
                <div class="quote-price-row">
                    <div class="quote-price">{iq['last_price']:,.2f}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_reversal_banner(signal)

        if market_open:
            st.caption(f"每10秒自動更新・資料時間 {iq.get('date', '')} {iq.get('time', '')}(TWSE官方即時報價,非精確逐筆)")
        else:
            st.caption(f"目前非交易時段(09:00-13:30),顯示最後資料・資料時間 {iq.get('date', '')} {iq.get('time', '')}")
            st.button("🔄 立即刷新", key=f"index_reversal_manual_refresh_{mis_code}")

        st.caption(
            f"拉回幅度=距今日高點回落%,近5分鐘變動幅度抓的是「速度」,任一超過門檻就升級警示。"
            f"目前門檻:拉回{pullback_warn_pct:.1f}%/急殺{pullback_severe_pct:.1f}%/近5分鐘"
            f"{fast_drop_severe_pct:.1f}%(側邊欄「⚙️急殺警示門檻設定」可調整)。還沒有背景推播,"
            "離開頁面不會主動通知你。純觀察參考,不是下單訊號。"
        )
        st.markdown("</div>", unsafe_allow_html=True)

    return _render


# --- K線圖:TradingView 官方開源的 lightweight-charts 引擎(取代原本的 Plotly 版本) ---
# 資料還是我們自己算好的 indicators.add_indicators() 結果,只是換了一個真正的 TradingView
# 圖表渲染引擎(十字游標、拖曳縮放、K棒樣式都是原生 TradingView 手感),不是嵌入 TradingView
# 官方 widget 抓它自己的資料(那樣會跟 EMA6/40/56 等自訂指標脫鉤)。
LIGHTWEIGHT_CHARTS_CDN = "https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"

CHART_THEME = {
    "text": "#f8fafc",
    "grid": "rgba(255,255,255,0.06)",
    "up": "#ef4444",       # 台股慣例:漲為紅,跟頁面其他地方(--tw-up)一致
    "down": "#22c55e",     # 跌為綠
    "ema6": "#f59e0b",
    "ema40": "#38bdf8",
    "ema56": "#a78bfa",
    "bb": "rgba(156,163,175,0.55)",
    "rsi": "#eab308",
    "macd": "#38bdf8",
    "macd_signal": "#f59e0b",
    "macd_hist": "#4b5563",
    "volume_up": "rgba(239,68,68,0.5)",
    "volume_down": "rgba(34,197,94,0.5)",
    "support": "#2dd4bf",
    "resistance": "#f472b6",
}

_CHART_HTML_TEMPLATE = """
<div id="tv-wrap" style="position:relative; width:100%;">
  <div id="tv-legend" style="position:absolute; top:6px; left:10px; z-index:2;
       font: 12px/1.6 Inter, 'Noto Sans TC', sans-serif; pointer-events:none;
       background: rgba(14,18,35,0.55); padding:6px 10px; border-radius:8px; backdrop-filter: blur(2px);"></div>
  <div id="tv-chart" style="width:100%;"></div>
</div>
<script src="__CDN_URL__"></script>
<script>
(function () {
  const DATA = __DATA_JSON__;
  const THEME = __THEME_JSON__;
  const SYMBOL = __SYMBOL_JSON__;
  const HEIGHT = __HEIGHT__;

  const container = document.getElementById('tv-chart');
  const legend = document.getElementById('tv-legend');
  legend.style.color = THEME.text;

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: HEIGHT,
    layout: { background: { color: 'transparent' }, textColor: THEME.text, fontFamily: "Inter, 'Noto Sans TC', sans-serif" },
    grid: { vertLines: { color: THEME.grid }, horzLines: { color: THEME.grid } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, rightOffset: 3, timeVisible: false },
  });

  const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: THEME.up, downColor: THEME.down, borderVisible: false,
    wickUpColor: THEME.up, wickDownColor: THEME.down,
  }, 0);
  candleSeries.setData(DATA.candles);
  candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.22 } });

  DATA.supports.forEach(function (s, i) {
    candleSeries.createPriceLine({
      price: s.price, color: THEME.support, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
      title: '支撐' + (i + 1),
    });
  });
  DATA.resistances.forEach(function (r, i) {
    candleSeries.createPriceLine({
      price: r.price, color: THEME.resistance, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
      title: '阻力' + (i + 1),
    });
  });

  const volumeSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
    priceFormat: { type: 'volume' }, priceScaleId: 'vol', priceLineVisible: false, lastValueVisible: false,
  }, 0);
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  volumeSeries.setData(DATA.volume);

  function addLine(paneIdx, color, dashed) {
    return chart.addSeries(LightweightCharts.LineSeries, {
      color: color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      lineStyle: dashed ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Solid,
    }, paneIdx);
  }

  const ema6Series = addLine(0, THEME.ema6); ema6Series.setData(DATA.ema6);
  const ema40Series = addLine(0, THEME.ema40); ema40Series.setData(DATA.ema40);
  const ema56Series = addLine(0, THEME.ema56); ema56Series.setData(DATA.ema56);
  const bbUpperSeries = addLine(0, THEME.bb, true); bbUpperSeries.setData(DATA.bbUpper);
  const bbLowerSeries = addLine(0, THEME.bb, true); bbLowerSeries.setData(DATA.bbLower);

  const rsiSeries = addLine(1, THEME.rsi); rsiSeries.setData(DATA.rsi);
  rsiSeries.createPriceLine({ price: 70, color: THEME.up, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true });
  rsiSeries.createPriceLine({ price: 30, color: THEME.down, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true });

  const macdSeries = addLine(2, THEME.macd); macdSeries.setData(DATA.macd);
  const macdSignalSeries = addLine(2, THEME.macd_signal); macdSignalSeries.setData(DATA.macdSignal);
  const macdHistSeries = chart.addSeries(LightweightCharts.HistogramSeries, {
    color: THEME.macd_hist, priceLineVisible: false, lastValueVisible: false,
  }, 2);
  macdHistSeries.setData(DATA.macdHist);

  const panes = chart.panes();
  const priceH = Math.round(HEIGHT * 5 / 9);
  const rsiH = Math.round(HEIGHT * 2 / 9);
  const macdH = HEIGHT - priceH - rsiH;
  if (panes[0]) panes[0].setHeight(priceH);
  if (panes[1]) panes[1].setHeight(rsiH);
  if (panes[2]) panes[2].setHeight(macdH);

  try {
    LightweightCharts.createTextWatermark(panes[0], {
      horzAlign: 'center', vertAlign: 'center',
      lines: [{ text: SYMBOL, color: 'rgba(248,250,252,0.07)', fontSize: 40 }],
    });
  } catch (e) {}

  function fmt(n, digits) {
    if (n === undefined || n === null || Number.isNaN(n)) return '--';
    return Number(n).toFixed(digits === undefined ? 2 : digits);
  }
  function lastVal(arr) { return arr.length ? arr[arr.length - 1].value : null; }

  function renderLegend(candle, vol, ema6v, ema40v, ema56v, rsiv, macdv, macdsv) {
    const closeColor = (candle && candle.close >= candle.open) ? THEME.up : THEME.down;
    legend.innerHTML =
      '<div style="font-weight:700;margin-bottom:2px;">' + SYMBOL + '</div>' +
      '<div>O ' + fmt(candle && candle.open) + '&nbsp; H ' + fmt(candle && candle.high) +
      '&nbsp; L ' + fmt(candle && candle.low) +
      '&nbsp; C <span style="color:' + closeColor + '">' + fmt(candle && candle.close) + '</span></div>' +
      '<div>量 ' + (vol ? Math.round(vol / 1000).toLocaleString() + ' 張' : '--') + '</div>' +
      '<div><span style="color:' + THEME.ema6 + '">EMA6 ' + fmt(ema6v) + '</span>&nbsp; ' +
      '<span style="color:' + THEME.ema40 + '">EMA40 ' + fmt(ema40v) + '</span>&nbsp; ' +
      '<span style="color:' + THEME.ema56 + '">EMA56 ' + fmt(ema56v) + '</span></div>' +
      '<div><span style="color:' + THEME.rsi + '">RSI ' + fmt(rsiv, 1) + '</span>&nbsp; ' +
      '<span style="color:' + THEME.macd + '">MACD ' + fmt(macdv) + '</span>&nbsp; ' +
      '<span style="color:' + THEME.macd_signal + '">Signal ' + fmt(macdsv) + '</span></div>';
  }

  function initialLegend() {
    renderLegend(
      DATA.candles.length ? DATA.candles[DATA.candles.length - 1] : null,
      lastVal(DATA.volume), lastVal(DATA.ema6), lastVal(DATA.ema40), lastVal(DATA.ema56),
      lastVal(DATA.rsi), lastVal(DATA.macd), lastVal(DATA.macdSignal)
    );
  }
  initialLegend();

  chart.subscribeCrosshairMove(function (param) {
    if (!param || !param.time || !param.seriesData || param.seriesData.size === 0) {
      initialLegend();
      return;
    }
    const candle = param.seriesData.get(candleSeries);
    const vol = param.seriesData.get(volumeSeries);
    const ema6v = param.seriesData.get(ema6Series);
    const ema40v = param.seriesData.get(ema40Series);
    const ema56v = param.seriesData.get(ema56Series);
    const rsiv = param.seriesData.get(rsiSeries);
    const macdv = param.seriesData.get(macdSeries);
    const macdsv = param.seriesData.get(macdSignalSeries);
    renderLegend(
      candle, vol && vol.value,
      ema6v && ema6v.value, ema40v && ema40v.value, ema56v && ema56v.value,
      rsiv && rsiv.value, macdv && macdv.value, macdsv && macdsv.value
    );
  });

  function resize() { chart.applyOptions({ width: container.clientWidth }); }
  window.addEventListener('resize', resize);
  resize();
})();
</script>
"""


def _series_data(df, column):
    """把某一欄轉成 lightweight-charts 的 [{time, value}] 格式,過濾掉開頭指標還沒算出來的 NaN 列。"""
    sub = df[column].dropna()
    return [{"time": idx.strftime("%Y-%m-%d"), "value": float(v)} for idx, v in sub.items()]


def _build_tradingview_chart_html(df: pd.DataFrame, symbol_label: str, height: int = 760, supports=None, resistances=None) -> str:
    price_rows = df.dropna(subset=["Open", "High", "Low", "Close"])
    candles = [
        {
            "time": idx.strftime("%Y-%m-%d"),
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": float(row["Close"]),
        }
        for idx, row in price_rows.iterrows()
    ]
    volume = [
        {
            "time": idx.strftime("%Y-%m-%d"),
            "value": float(row["Volume"]),
            "color": CHART_THEME["volume_up"] if row["Close"] >= row["Open"] else CHART_THEME["volume_down"],
        }
        for idx, row in price_rows.dropna(subset=["Volume"]).iterrows()
    ]

    data = {
        "candles": candles,
        "volume": volume,
        "ema6": _series_data(df, "EMA6"),
        "ema40": _series_data(df, "EMA40"),
        "ema56": _series_data(df, "EMA56"),
        "bbUpper": _series_data(df, "BB_upper"),
        "bbLower": _series_data(df, "BB_lower"),
        "rsi": _series_data(df, "RSI14"),
        "macd": _series_data(df, "MACD"),
        "macdSignal": _series_data(df, "MACD_signal"),
        "macdHist": _series_data(df, "MACD_hist"),
        "supports": supports or [],
        "resistances": resistances or [],
    }

    html = _CHART_HTML_TEMPLATE
    html = html.replace("__CDN_URL__", LIGHTWEIGHT_CHARTS_CDN)
    html = html.replace("__DATA_JSON__", json.dumps(data))
    html = html.replace("__THEME_JSON__", json.dumps(CHART_THEME))
    html = html.replace("__SYMBOL_JSON__", json.dumps(symbol_label))
    html = html.replace("__HEIGHT__", str(height))
    return html


if not code:
    st.info("請在左側輸入股票代號")
    st.stop()

# 個股/加權指數/櫃買指數在 TWSE MIS 的代號規則——加權指數固定代號"t00"、櫃買指數固定
# 代號"o00"(都已用跟yfinance/TPEx官方數字對得上驗證過不是抓錯),個股就是股票代號本身。
# 這組(mis_code, mis_otc)後面「⚠️盤中急殺警示」區塊也會用到,所以獨立算出來、不要塞在
# try/except裡面重算一次。
if code == "^TWOII":
    mis_code, mis_otc, display_symbol = "o00", True, "^TWOII"
elif code == "^TWII":
    mis_code, mis_otc, display_symbol = "t00", False, "^TWII"
else:
    mis_code, mis_otc, display_symbol = code, otc, to_yf_symbol(code, otc)

try:
    if code == "^TWOII":
        # 櫃買指數原本以為沒有即時報價來源(yfinance的^TWOII嚴重過時,TPEx官方OpenAPI也
        # 只有每日收盤),後來實測發現 TWSE MIS(intraday.py用的同一支API)其實也有服務
        # 櫃買指數——已用「跟TPEx官方昨收比對」的方式驗證過不是抓錯資料(MIS的前一日收盤
        # y=399.21,剛好等於TPEx官方昨天(20260916)的收盤399.21,兩個資料源對得上)。
        # MIS抓不到時才退回 TPEx 官方每日收盤行情。
        quote = intraday.get_intraday_quote(mis_code, mis_otc)
        if quote is None or quote.get("last_price") is None:
            quote = load_tpex_otc_index_quote()
        elif quote is not None:
            quote["symbol"] = display_symbol
    else:
        # 個股跟加權指數的主報價卡都改抓 TWSE 官方 mis.twse.com.tw 即時報價(intraday.py),
        # 不再用 yfinance——yfinance 對台股有官方15~20分鐘延遲,MIS 才是真正即時,這樣個股
        # 主報價卡才會跟下面「⚡盤中即時強弱」卡片顯示同一個價格,不會兩張卡數字對不上。
        # MIS 抓不到時(網路問題等)才退回 yfinance 的延遲報價,優雅降級不中斷。
        quote = intraday.get_intraday_quote(mis_code, mis_otc)
        if quote is None or quote.get("last_price") is None:
            quote = load_quote(code, otc)
        elif quote is not None:
            quote["symbol"] = display_symbol  # 顯示格式跟其他卡片一致,例如"2330.TW"/"^TWII"
    if quote is None:
        raise RuntimeError("查無資料")
except Exception as e:
    st.error(f"抓取報價失敗:{e}")
    st.stop()

# 存起來給側邊欄「⚙️急殺警示門檻設定」的換算價格預覽用(見該區塊的說明)——每次成功抓到
# 報價就更新,不管這次是走 MIS 即時還是退回 yfinance/TPEx,只要有 day_high 就存。
if quote.get("day_high"):
    st.session_state[f"_last_day_high_{code}"] = quote["day_high"]

# 指數不是個股,查不到 TWSE ISIN 中文簡稱,yfinance 的 longName 也是亂碼代號(例如
# "^TWOII,113497,928500"),直接用固定的顯示名稱,不走 load_name() 那套查詢股票的邏輯。
INDEX_DISPLAY_NAMES = {"^TWII": "加權指數(台股大盤)", "^TWOII": "櫃買指數(OTC)"}
name = INDEX_DISPLAY_NAMES[code] if code in INDEX_DISPLAY_NAMES else load_name(code, otc)

st.markdown(
    f"""<div class="app-header">
        <h1>{LOGO_IMG_TAG} 台股查詢模型 <span class="badge-gold">TW MARKET</span></h1>
        <div class="sub">即時報價・技術指標・做多訊號 — 學習用途,非投資建議</div>
    </div>""",
    unsafe_allow_html=True,
)

# --- 美股夜盤連動指標(獨立於個股查詢,跟股票代號無關,所以放在報價卡片之前) ---
try:
    us_signal = load_us_overnight_signal()
except Exception:
    us_signal = None

if us_signal:
    score = us_signal["score"]
    score_label = us_signal["score_label"]
    if score_label in ("強多", "偏多"):
        us_badge_class, us_badge_text = "badge-up", f"▲ {score_label}(淨分{score:+d})"
    elif score_label in ("強空", "偏空"):
        us_badge_class, us_badge_text = "badge-down", f"▼ {score_label}(淨分{score:+d})"
    elif score_label == "中性":
        us_badge_class, us_badge_text = "badge-flat", f"▬ 中性(淨分{score:+d})"
    else:
        us_badge_class, us_badge_text = "badge-flat", "資料不足"

    # 淨分視覺化:見 _build_score_gauge_html()(跟盤中強弱指標共用同一個 helper)。
    # 只有 SCORE_SYMBOLS(小道瓊/那斯達克期貨)計入淨分,費半/台積電ADR不計分只顯示漲跌。
    SCORE_MAX = max(STRENGTH_WEIGHT.values()) * len(SCORE_SYMBOLS)
    score_gauge_html = _build_score_gauge_html(score, SCORE_MAX, SCORE_STRONG_THRESHOLD)

    US_STRENGTH_COLOR = {"強": "var(--accent-gold)", "普通": "#8b93a7", "弱": "#6b7280"}

    # 燈號顏色:沿用台股紅漲綠跌慣例,同一個色相用深淺表示強弱(深=強、中=普通、淺=弱),
    # 不用傳統紅黃綠三色燈號——那套「紅=差/綠=好」的語意會跟這裡「紅=漲」的配色衝突。
    US_LIGHT_COLORS = {
        ("up", "強"): "#b91c1c",
        ("up", "普通"): "#ef4444",
        ("up", "弱"): "#fca5a5",
        ("down", "強"): "#15803d",
        ("down", "普通"): "#22c55e",
        ("down", "弱"): "#86efac",
    }
    US_LIGHT_FLAT_COLOR = "#9ca3af"

    def _us_light_html(key, label, r):
        if not r:
            return (
                f'<span title="{label}:資料不足" style="display:inline-block; width:11px; height:11px; '
                f'border-radius:50%; background:{US_LIGHT_FLAT_COLOR}; margin-right:6px; vertical-align:middle;"></span>'
            )
        if r["change_pct"] == 0:
            color, detail = US_LIGHT_FLAT_COLOR, "持平"
        else:
            # 燈號顏色反映「對台股的方向」(可能跟自己漲跌方向相反,見INVERTED_SYMBOLS),
            # 這樣紅綠點才能跟旁邊淨分badge的紅漲綠跌是同一套語意,一眼掃過去就是一個
            # 一致的故事,不用逐個換算反向指標
            sign = "up" if effective_sign(key, r["change_pct"]) > 0 else "down"
            strength = r.get("strength") or "普通"
            color = US_LIGHT_COLORS[(sign, strength)]
            inv_note = ""
            if key in INVERTED_SYMBOLS:
                inv_note = ",對台股偏空" if r["change_pct"] > 0 else ",對台股偏多"
            detail = f"{r['change_pct']:+.2f}%" + (f"({r['strength']})" if r.get("strength") else "") + inv_note
        return (
            f'<span title="{label}:{detail}" style="display:inline-block; width:11px; height:11px; '
            f'border-radius:50%; background:{color}; margin-right:6px; vertical-align:middle;"></span>'
        )

    def _us_stat_html(key, label, r, show_value=False):
        if not r:
            return f'<div class="stat-item"><div class="label">{label}</div><div class="value">—</div></div>'
        # 這裡的箭頭/顏色維持顯示「自己實際的漲跌」(跟其他報價卡一致的紅漲綠跌),不是
        # 換算過的「對台股方向」——換算方向另外用一行文字附註(inverted_html),不跟顏色
        # 混在一起,避免箭頭朝上卻用綠色這種視覺上自相矛盾的畫面
        arrow = "▲" if r["change_pct"] > 0 else ("▼" if r["change_pct"] < 0 else "▬")
        color = "var(--tw-up)" if r["change_pct"] > 0 else ("var(--tw-down)" if r["change_pct"] < 0 else "#9ca3af")
        strength = r.get("strength")
        strength_html = (
            f' <span style="color:{US_STRENGTH_COLOR[strength]}; font-size:0.75rem; font-weight:700;">'
            f"{strength}</span>"
            if strength
            else ""
        )
        # show_value:計分指標額外顯示實際數值(指數點位/匯率/VIX點位),不然使用者只看得到
        # %數,不知道漲跌的實際數值——費半/ADR只給參考用,維持單純顯示%不加這個
        value_html = (
            f' <span style="font-size:0.72rem; color:var(--accent-gold); font-weight:500;">・{r["close"]:,.2f}</span>'
            if show_value
            else ""
        )
        inverted_html = ""
        if key in INVERTED_SYMBOLS and r["change_pct"] != 0:
            inverted_html = (
                ' <span style="font-size:0.7rem; color:#8b93a7;">(對台股'
                + ("偏空" if r["change_pct"] > 0 else "偏多")
                + ")</span>"
            )
        return (
            f'<div class="stat-item"><div class="label">{label}(收{r["asof"][:10]})</div>'
            f'<div class="value" style="color:{color}">{arrow} {r["change_pct"]:+.2f}%{strength_html}{value_html}{inverted_html}</div></div>'
        )

    stat_items = "".join(
        _us_stat_html(key, label, us_signal[key], show_value=(key in SCORE_SYMBOLS))
        for key, (_, label) in US_MARKET_SYMBOLS.items()
    )
    # 燈號圓點只給計入淨分的5個指標——費半/ADR不計分,旁邊放燈號會讓人誤以為全部都算進淨分裡
    light_items = "".join(
        _us_light_html(key, label, us_signal[key])
        for key, (_, label) in US_MARKET_SYMBOLS.items()
        if key in SCORE_SYMBOLS
    )

    # 櫃買指數即時位階——使用者親身經歷櫃買市場盤中從高點急殺,而櫃買指數(電子/半導體權重高)
    # 跟那斯達克期貨連動性高,所以特地放進同一張卡片,方便直接對照「美股夜盤預測的方向」
    # 跟「現在盤中實際走勢」。這是TWSE MIS即時資料(現在進行式),跟上面4個指標的yfinance
    # 隔夜資料(已經發生、相對固定)是不同性質的東西,故意不混進同一個加權分數裡一起算,
    # 分開呈現才不會互相稀釋各自的訊號意義。
    def _otc_position_badge(score100: int) -> tuple[str, str]:
        if score100 >= 60:
            return "badge-up", "貼近高點"
        if score100 > 0:
            return "badge-up", "偏上半區"
        if score100 == 0:
            return "badge-flat", "區間中點"
        if score100 > -60:
            return "badge-down", "偏下半區"
        return "badge-down", "貼近低點"

    try:
        otc_quote = intraday.get_intraday_quote("o00", True)
    except Exception:
        otc_quote = None
    otc_position = intraday.signal_range_position(otc_quote) if otc_quote else {"available": False}

    # 昨日收盤位階——單看今天容易誤判(例如今天才剛開高,還看不出是不是要急殺),多比對
    # 「昨天收盤時,自己是貼近當天高點還是低點」,才看得出是不是連續好幾天高檔盤堅後才急殺
    # 這種型態。用 fetch_data.get_tpex_otc_index_previous_day() 拿昨天的完整OHLC,包成
    # 跟即時報價相容的dict格式,直接複用 intraday.signal_range_position() 算法一致不用
    # 另外寫一份。
    try:
        otc_prev = get_tpex_otc_index_previous_day()
    except Exception:
        otc_prev = None
    otc_prev_position = (
        intraday.signal_range_position(
            {"day_high": otc_prev["high"], "day_low": otc_prev["low"], "last_price": otc_prev["close"]}
        )
        if otc_prev
        else {"available": False}
    )

    if otc_position.get("available"):
        otc_score100 = round(otc_position["value"] * 100)
        otc_badge_class, otc_label = _otc_position_badge(otc_score100)
        otc_gauge_html = _build_score_gauge_html(otc_score100, 100, 60)
        # 使用者想要「一眼看分數就能判斷」,不用逐字看badge文字——直接沿用主報價卡
        # quote-price那個3rem大字級,+100~-100直接當分數看,顏色比照紅漲綠跌(貼近高點偏紅、
        # 貼近低點偏綠),0分用中性灰。
        otc_score_color = "var(--tw-up)" if otc_score100 > 0 else ("var(--tw-down)" if otc_score100 < 0 else "#9ca3af")
        today_row_html = (
            f'<div class="quote-price-row"><div class="quote-price" style="font-size:2.4rem; color:{otc_score_color};">{otc_score100:+d}</div>'
            f'<div class="quote-badge {otc_badge_class}">今日 {otc_label}</div>'
            f'<div style="font-size:0.8rem; color:#8b93a7;">{otc_position["detail"]}</div></div>'
            f"{otc_gauge_html}"
        )
    else:
        today_row_html = '<div style="color:#8b93a7; font-size:0.85rem;">今日資料暫時無法取得</div>'

    if otc_prev_position.get("available"):
        prev_score100 = round(otc_prev_position["value"] * 100)
        _, prev_label = _otc_position_badge(prev_score100)
        prev_date_str = f"{otc_prev['date'][:4]}/{otc_prev['date'][4:6]}/{otc_prev['date'][6:]}"
        prev_row_html = (
            f'<div style="margin-top:0.5rem; font-size:0.78rem; color:#8b93a7;">'
            f"📅 昨日({prev_date_str})收盤位階:{prev_label}({prev_score100:+d}) · "
            f"收{otc_prev['close']:,.2f}(高{otc_prev['high']:,.2f}/低{otc_prev['low']:,.2f})</div>"
        )
    else:
        prev_row_html = '<div style="margin-top:0.5rem; font-size:0.78rem; color:#8b93a7;">📅 昨日收盤位階:資料暫時無法取得</div>'

    otc_block_html = (
        '<div style="margin-top:1rem; padding-top:0.8rem; border-top:1px solid rgba(255,255,255,0.08);">'
        '<div class="quote-symbol" style="font-size:0.85rem;">🎯 櫃買指數位階(今日即時 vs 昨日收盤)</div>'
        f"{today_row_html}{prev_row_html}</div>"
    )

    st.markdown(
        f"""
        <div class="quote-card">
            <div class="quote-symbol">🌐 全球連動強弱指標</div>
            <div class="quote-price-row">
                <div class="quote-badge {us_badge_class}">{us_badge_text}</div>
                <div>{light_items}</div>
            </div>
            {score_gauge_html}
            <div class="stat-row">
                {stat_items}
            </div>
            {otc_block_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "小道瓊/那斯達克期貨(YM=F/NQ=F)近24小時交易,涵蓋最新夜盤走勢;韓國KOSPI(^KS11)"
        "是同一個交易日已經開盤的亞洲市場,產業結構跟台灣接近;美元/新台幣(TWD=X)反映外資"
        "資金流向;VIX恐慌指數(^VIX)反映全球風險偏好。這5個都額外附實際數值(指數點位/"
        "匯率/VIX點位),不只有%數。美元新台幣、VIX是「反向指標」——自己漲對台股反而是偏空"
        "訊號(新台幣貶值=外資匯出、VIX漲=避險情緒濃厚),已經在燈號顏色/淨分方向裡換算過,"
        "不用自己心算。費半(^SOX)、台積電ADR(TSM)是美股現貨收盤價,只顯示漲跌幅供參考、"
        "不計入淨分。「強/普通/弱」是今天漲跌幅度跟自己近20日平均單日波動的比較,不是固定的"
        "絕對門檻。淨分是這5個指標依強弱加權(弱1分/普通2分/強3分,已換算反向指標的方向)後"
        f"加總得出的多空分數,範圍 ±{SCORE_MAX},橫條顯示淨分離兩端滿分有多遠,越極端代表"
        "訊號越一致越強烈。badge旁邊5個燈號依序對應這5個計分指標,顏色代表對台股的方向"
        "(紅偏多/綠偏空,反向指標已換算)、深淺=強弱(hover可看細節)。純觀察參考,不是"
        "下單訊號。"
    )
    st.caption(
        "🎯 櫃買指數位階:「今日」是現價在今天高低區間的哪個位置(TWSE即時報價,每次頁面"
        "重新整理就會更新);「昨日」是上一個完整交易日收盤時,自己收在當天區間的哪個位置"
        "(TPEx官方每日行情)——兩個一起看才看得出是不是連續好幾天高檔盤堅後才急殺,不是只看"
        "今天一天。放在這裡是因為櫃買指數電子/半導體權重高,常跟那斯達克期貨連動——可以直接"
        "對照「美股夜盤預測的方向」有沒有跟「櫃買實際走勢」背離,例如美股預測偏多但這裡已經"
        "滑到偏下半區,可能代表盤中氣氛在轉弱。純觀察參考,不是下單訊號,交易時段外"
        "(非09:00-13:45)今日資料是最後一筆。"
    )
else:
    st.warning("美股夜盤資料抓取失敗,暫時無法顯示連動指標。")

# --- 台指期(TX)夜盤參與度(獨立於個股查詢,跟股票代號無關) ---
# 跟上面的美股夜盤連動指標是不同角度:那個看美股對台股的連動方向,這個看台灣資金自己在
# 夜盤(15:00~次日05:00)的參與熱度——純粹是量能強弱,不判斷多空方向(見 night_session.py)。
try:
    night_signal = load_night_session_strength()
except Exception:
    night_signal = None

if night_signal:
    strength = night_signal["strength"]
    strength_emoji = {"熱絡": "🔥", "普通": "➖", "清淡": "💤"}.get(strength, "❔")
    strength_text = f"{strength_emoji} {strength}" if strength else "資料不足"
    # 用 .get() 不要直接 [key]——st.cache_data 包的 load_night_session_strength() 換過
    # 回傳格式(新增 long_score100/short_* 這幾個key)後,實測發現 Streamlit Cloud 部署時
    # 沒有整個重啟process、殘留了改版前的舊快取字典,直接用[key]取新欄位會直接 KeyError
    # 讓整頁掛掉。改用.get()至少能優雅降級(這次的新增資訊先不顯示,不影響其他既有內容),
    # 不會讓一個新欄位的快取結構不匹配拖垮整張卡片。
    # long_score100 是 ratio 的純函式,即使快取字典是改版前的舊格式(沒有這個key)也能就地
    # 重算,不用整個等 .get() 落空就放棄顯示——ratio 這個欄位從最早版本就存在,舊快取一定有。
    long_score100 = night_signal.get("long_score100", night_session.ratio_to_score100(night_signal["ratio"]))
    score_suffix = f"・評分{long_score100}" if long_score100 is not None else ""
    ratio_str = f"(量能比 {night_signal['ratio']:.2f}倍{score_suffix})" if night_signal["ratio"] is not None else ""

    # 近3夜短期比較——使用者要求「量能比可以做個評分跟前三天比較」,近20夜均量反應比較慢,
    # 跟這幾天比才看得出是不是剛開始轉熱/轉冷,兩個時間窗一起看比單看20夜全面(見
    # night_session.compute_night_session_strength() 的方法說明)。
    if night_signal.get("short_ratio") is not None:
        short_emoji = {"熱絡": "🔥", "普通": "➖", "清淡": "💤"}.get(night_signal.get("short_strength"), "❔")
        short_badge_str = (
            f"{short_emoji} 近{night_signal.get('short_window_days')}夜{night_signal.get('short_strength')}"
            f"(量能比{night_signal['short_ratio']:.2f}倍・評分{night_signal.get('short_score100')})"
        )
    else:
        short_badge_str = None

    chg = night_signal["front_month_change_pct"]
    if chg is None:
        chg_badge_class, chg_str = "badge-flat", "—"
    elif chg > 0:
        chg_badge_class, chg_str = "badge-up", f"▲ {chg:+.2f}%"
    elif chg < 0:
        chg_badge_class, chg_str = "badge-down", f"▼ {chg:+.2f}%"
    else:
        chg_badge_class, chg_str = "badge-flat", "▬ 0.00%"

    # 即時參與度評分——使用者要求「跟櫃買市場一樣有即時監控的評分分數」,見
    # night_session.compute_live_participation_score() 的方法說明(步調pace比較,
    # 0~100單向強度分數,不是雙向多空分數)。不用 st.cache_data 包,每次重跑(頁面
    # 自動刷新或使用者互動)都直接重打,才能反映「現在」的即時累積量能。
    try:
        live_participation = night_session.compute_live_participation_score()
    except Exception:
        live_participation = {"available": False, "reason": "insufficient"}

    if live_participation.get("available"):
        p_score = live_participation["score100"]
        p_color = _participation_score_color(p_score)
        p_gauge_html = _build_participation_gauge_html(p_score)
        p_chg = live_participation["change_pct"]
        if p_chg is None:
            p_chg_str = "—"
        elif p_chg > 0:
            p_chg_str = f"▲ {p_chg:+.2f}%"
        elif p_chg < 0:
            p_chg_str = f"▼ {p_chg:+.2f}%"
        else:
            p_chg_str = "▬ 0.00%"
        p_price_str = f"{live_participation['last_price']:,.0f}" if live_participation["last_price"] is not None else "—"
        live_block_html = (
            '<div style="margin-top:1rem; padding-top:0.8rem; border-top:1px solid rgba(255,255,255,0.08);">'
            '<div class="quote-symbol" style="font-size:0.85rem;">⚡ 夜盤即時參與度評分</div>'
            f'<div class="quote-price-row"><div class="quote-price" style="font-size:2.4rem; color:{p_color};">{p_score}</div>'
            f'{_participation_badge_html(live_participation["label"], p_score)}</div>'
            f'<div style="font-size:0.85rem; color:#8b93a7; margin-top:0.2rem;">'
            f'<span style="color:var(--accent-gold); font-weight:700; font-size:0.95rem;">'
            f'{live_participation["symbol_id"]} {p_price_str} {p_chg_str}</span>'
            f'・累積{live_participation["live_volume"]:,}口(已開盤{live_participation["elapsed_minutes"]:.0f}分鐘,'
            f'正常步調基準約{live_participation["expected_volume"]:,.0f}口)</div>'
            f"{p_gauge_html}</div>"
        )
    elif live_participation.get("reason") == "closed":
        # 現在不在夜盤時段,不代表使用者不想看評分——「上一個已結束的夜盤」最終評分還是有
        # 參考價值(比照櫃買指數位階卡片「今日/昨日」兩段式呈現的既有設計:不是有資料才顯示、
        # 沒資料就整段消失,而是永遠有東西可以看)。優先沿用 night_signal 已經算好的
        # long_score100,拿不到(舊快取字典沒有這個key)才就地用 ratio 重算,見上面 ratio_str
        # 那邊同樣的 .get() 容錯理由。
        last_night_score = long_score100

        # 這個fallback區塊原本只有量能比評分,完全沒有指數數字——使用者反映「指數都沒顯示
        # 出來」,不管夜盤有沒有開都要看到現在的TX指數連動。get_tx_live_quote()不管夜盤
        # 開沒開都會回傳「目前最後已知報價」,用is_live旗標判斷這筆報價是不是剛剛真的成交
        # 的,不是的話老實標示資料時間讓使用者自己判斷,不要包裝成即時。
        try:
            current_quote = night_session.get_tx_live_quote()
        except Exception:
            current_quote = None
        quote_line_html = ""
        if current_quote and current_quote.get("last_price") is not None:
            cq_chg = current_quote["change_pct"]
            if cq_chg is None:
                cq_chg_str = "—"
            elif cq_chg > 0:
                cq_chg_str = f"▲ {cq_chg:+.2f}%"
            elif cq_chg < 0:
                cq_chg_str = f"▼ {cq_chg:+.2f}%"
            else:
                cq_chg_str = "▬ 0.00%"
            ctime_raw = current_quote.get("ctime")
            ctime_str = f"{ctime_raw[:2]}:{ctime_raw[2:4]}:{ctime_raw[4:6]}" if ctime_raw and len(ctime_raw) == 6 else "—"
            freshness_note = "" if current_quote["is_live"] else "・非夜盤成交,日盤收盤定住的價格"
            quote_line_html = (
                f'<div style="font-size:0.85rem; color:#8b93a7; margin-top:0.4rem;">'
                f'目前指數 <span style="color:var(--accent-gold); font-weight:700; font-size:0.95rem;">'
                f'{current_quote["symbol_id"]} {current_quote["last_price"]:,.0f} {cq_chg_str}</span>'
                f'(資料時間{ctime_str}{freshness_note})</div>'
            )
        if last_night_score is not None:
            ln_color = _participation_score_color(last_night_score)
            short_score100 = night_signal.get("short_score100")
            short_score_note = (
                f'近{night_signal.get("short_window_days")}夜評分{short_score100}({night_signal.get("short_strength")})・'
                if short_score100 is not None
                else ""
            )
            # 今晚是不是真的還會開夜盤——週六~週日晚上TAIFEX不開夜盤(週六接的是週日、
            # 週日接的是週一才有開盤但週日晚上本身不算),這兩天不能說「今晚15:00開盤後
            # 換成即時評分」,不然講的話會兌現不了,使用者等到15:00還是看不到即時評分,
            # 會誤以為功能壞了。週一~週五晚上都正常開盤(見night_session.py的說明)。
            next_session_note = (
                "今晚15:00開盤後這裡會換成盤中即時評分。"
                if night_session.has_night_session_tonight()
                else "今晚(週六~週日)沒有夜盤,下一個夜盤要等到下週一15:00開盤。"
            )
            live_block_html = (
                '<div style="margin-top:1rem; padding-top:0.8rem; border-top:1px solid rgba(255,255,255,0.08);">'
                f'<div class="quote-symbol" style="font-size:0.85rem;">📅 昨晚({night_signal["date"]})最終評分(收盤結算,非步調比較)</div>'
                f'<div class="quote-price-row"><div class="quote-price" style="font-size:2.4rem; color:{ln_color};">{last_night_score}</div>'
                f'{_participation_badge_html(strength_text, last_night_score)}</div>'
                f"{quote_line_html}"
                f"{_build_participation_gauge_html(last_night_score)}"
                f'<div style="font-size:0.78rem; color:#8b93a7; margin-top:0.4rem;">{short_score_note}{next_session_note}</div></div>'
            )
        else:
            live_block_html = (
                '<div style="margin-top:1rem; padding-top:0.8rem; border-top:1px solid rgba(255,255,255,0.08); '
                'color:#8b93a7; font-size:0.85rem;">⚡ 目前非夜盤時段(15:00~次日05:00),昨晚資料不足無法評分。'
                f"{quote_line_html}</div>"
            )
    else:
        live_block_html = (
            '<div style="margin-top:1rem; padding-top:0.8rem; border-top:1px solid rgba(255,255,255,0.08); '
            'color:#8b93a7; font-size:0.85rem;">⚡ 夜盤剛開盤或即時資料暫時無法取得,評分還無法計算。</div>'
        )

    # short_badge_html/short_stat_html 可能是空字串(舊快取或資料不足時)——不能把它們各自
    # 放在下面 st.markdown 樣板裡自己獨立的一行,那一行會整行縮排完全沒有內容,被 Streamlit
    # 的 markdown 引擎誤判成「縮排程式碼區塊」的開頭,導致後面所有 HTML 整段變成原始文字
    # 顯示(這個坑先前在 _build_score_gauge_html 也踩過一次,這次是新的變形:不是「多行HTML」
    # 而是「單一個可能是空字串的{變數}獨占一行」也會觸發同樣的問題)。改成把整排badge/整排
    # stat-item先組成一行完整字串,再整個當一個佔位符塞進樣板,樣板裡每一行都保證有實際內容。
    short_badge_html = (
        _participation_badge_html(short_badge_str, night_signal.get("short_score100"))
        if short_badge_str
        else ""
    )
    short_avg_volume = night_signal.get("short_avg_volume")
    short_stat_html = (
        f'<div class="stat-item"><div class="label">近{night_signal.get("short_window_days")}夜均量</div>'
        f'<div class="value">{short_avg_volume:,.0f} 口</div></div>'
        if short_avg_volume is not None
        else ""
    )
    badge_row_html = (
        f'{_participation_badge_html(f"{strength_text}{ratio_str}", long_score100)}'
        f'<div class="quote-badge {chg_badge_class}">{chg_str}</div>'
        f"{short_badge_html}"
    )
    stat_row_html = (
        f'<div class="stat-item"><div class="label">夜盤成交量</div><div class="value">{night_signal["volume"]:,} 口</div></div>'
        f'<div class="stat-item"><div class="label">近20日均量</div><div class="value">{night_signal["avg_volume"]:,.0f} 口</div></div>'
        f"{short_stat_html}"
        f'<div class="stat-item"><div class="label">資料日期</div><div class="value">{night_signal["date"]}</div></div>'
    )

    st.markdown(
        f"""
        <div class="quote-card">
            <div class="quote-symbol">🌆 台指期夜盤參與度</div>
            <div class="quote-price-row">
                {badge_row_html}
            </div>
            <div class="stat-row">
                {stat_row_html}
            </div>
            {live_block_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "台股期貨(TX)15:00~次日05:00盤後交易時段全合約加總成交量,對比近20個交易日均量的"
        "比值,反映的是台灣資金自己的參與度/信心強度,不是多空方向——量大只代表當晚交易熱絡、"
        "資金關注度高,不代表偏多或偏空。近月合約當晚漲跌%只是補充參考,不是這個指標的主體。"
        "另外多算一組「近3夜」短期比較(評分算法一樣,只是基準天數從20夜換成3夜)——20夜均量"
        "反應比較慢,跟最近3夜比才看得出是不是剛開始轉熱/轉冷,兩個時間窗一起看比單看20夜全面。"
        "純觀察參考,不是下單訊號。"
    )
    st.caption(
        "⚡ 夜盤即時參與度評分:跟上面的量能比不同資料源(這裡是mis.taifex.com.tw即時報價,"
        "隨盤中成交即時更新,頁面自動刷新就會跟著變動),分數是「目前累積量能」對比「這個時間點"
        "正常應該累積多少(用近20夜均量乘上目前經過時間佔全部夜盤時段的比例當基準)」的步調"
        "比較,50分=正常步調,100分封頂=至少2倍熱絡,0分=還沒成交。這是假設量能均勻分佈在整個"
        "夜盤時段的簡化假設,開盤前段/尾盤實際通常比半夜熱絡,算出來的比值在時段頭尾會有系統性"
        "偏差,當粗略參考就好。非夜盤時段這裡會改顯示「上一個已結束夜盤」的最終評分(完整一夜"
        "量能比對近20夜均量,不是步調比較,兩者計算基準不同但共用同一套0~100分數尺度方便比較),"
        "不會整段消失沒東西看。一樣不判斷多空方向,純觀察參考,不是下單訊號。"
    )
else:
    st.info("台指期夜盤資料暫時無法取得或還在累積中。")

# --- 報價卡片 ---
# 櫃買指數(^TWOII)優先用 TWSE MIS 即時報價,MIS 沒有歷史資料可以拉(不管即時報價這邊
# 抓不抓得到都一樣),所以只能顯示一張報價卡,技術分析/做多訊號/分點掃描都需要歷史K線
# 資料才能運作,不支援。
if code == "^TWOII":
    otc_change = quote["last_price"] - quote["previous_close"]
    otc_change_pct = otc_change / quote["previous_close"] * 100
    if otc_change == 0:
        otc_badge_class, otc_arrow = "badge-flat", "▬"
    elif otc_change > 0:
        otc_badge_class, otc_arrow = "badge-up", "▲"
    else:
        otc_badge_class, otc_arrow = "badge-down", "▼"
    otc_change_str = f"{otc_arrow} {abs(otc_change):.2f} ({abs(otc_change_pct):.2f}%)"

    st.markdown(
        f"""
        <div class="quote-card">
            <div class="quote-symbol">{quote['symbol']} · {name}</div>
            <div class="quote-price-row">
                <div class="quote-price">{quote['last_price']:,.2f}</div>
                <div class="quote-badge {otc_badge_class}">{otc_change_str}</div>
            </div>
            <div class="stat-row">
                <div class="stat-item"><div class="label">昨收</div><div class="value">{quote['previous_close']:,.2f}</div></div>
                <div class="stat-item"><div class="label">最高</div><div class="value">{quote['day_high']:,.2f}</div></div>
                <div class="stat-item"><div class="label">最低</div><div class="value">{quote['day_low']:,.2f}</div></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if "date" in quote:  # 有 date/time 欄位代表這次是 TWSE MIS 即時報價,不是 TPEx 每日收盤
        st.caption(f"資料時間 {quote.get('date', '')} {quote.get('time', '')}(TWSE官方即時報價,非精確逐筆)")
    else:
        st.caption(f"⚠️ TWSE即時報價暫時無法取得,顯示的是TPEx官方每日收盤行情(資料日期:{quote['asof'][:4]}-{quote['asof'][4:6]}-{quote['asof'][6:]})。")

    # K線圖+籌碼支撐/阻力——櫃買指數沒有任意區間歷史資料API,用本地快取逐次累積湊出來
    # (見fetch_data.update_otc_index_history_cache的docstring),跟margin_data.py同一套
    # 「沒有歷史API只能本地累積」的解法,差別是這裡一次進帳一整個月,累積速度快很多。
    # 支撐/阻力也是同一份資料算的,先用「目前累積到的天數」算初步結果,不等湊滿
    # volume_profile.py理想的60個交易日(近3個月)才顯示——使用者確認過寧可先看不完整
    # 版本、每天累積慢慢變準。EMA56/MACD/RSI這些需要較長天數才有意義的指標,累積天數
    # 不足時add_indicators()算出來的值大多是NaN,_series_data()會自動濾掉、圖上就是
    # 沒有那條線,不會顯示錯誤數字,隨著本地快取累積會自動出現,不用另外處理。
    try:
        otc_hist_df = update_otc_index_history_cache()
    except Exception:
        otc_hist_df = None

    if otc_hist_df is not None and not otc_hist_df.empty:
        otc_hist_days = len(otc_hist_df)
        otc_indexed_df = add_indicators(otc_hist_df)
        otc_supports = find_nearest_supports(otc_indexed_df)
        otc_resistances = find_nearest_resistances(otc_indexed_df)
        otc_chart_html = _build_tradingview_chart_html(
            otc_indexed_df, f"{quote['symbol']} · {name}", height=560,
            supports=otc_supports, resistances=otc_resistances,
        )
        components.html(otc_chart_html, height=560, scrolling=False)
        st.caption(
            f"K線圖累積{otc_hist_days}個交易日(目標近3個月/60日,每次查詢自動累積新的一個月),"
            "不是任意區間——沒有歷史API可以直接拉,用本地快取逐次累積,均線/RSI/MACD等需要"
            "較長天數的指標在累積足夠天數前會是空的,不影響蠟燭本身。"
        )
        if otc_supports:
            st.caption(f"籌碼支撐(成交量分佈區域高峰,離目前指數最近的{len(otc_supports)}個):")
            st.markdown(
                _build_level_badges_html(otc_supports, quote["last_price"], CHART_THEME["support"]),
                unsafe_allow_html=True,
            )
        else:
            st.caption("籌碼資料累積中,還找不到明顯支撐。")
        if otc_resistances:
            st.caption(f"籌碼阻力(成交量分佈區域高峰,離目前指數最近的{len(otc_resistances)}個):")
            st.markdown(
                _build_level_badges_html(otc_resistances, quote["last_price"], CHART_THEME["resistance"]),
                unsafe_allow_html=True,
            )
        else:
            st.caption("籌碼資料累積中,還找不到明顯阻力。")
    else:
        st.info("K線圖資料暫時無法取得。")
    st.info("這個指數沒有任意區間可選的歷史資料(只能靠本地累積),做多訊號、分點掃描這兩個功能需要三大法人/權證/分點籌碼資料,指數沒有這些資料,不支援。")

    # 這裡就要 st.stop() 了(下面沒有df可以用),所以急殺警示得在這裡就呼叫,不能等到
    # 後面跟^TWII共用的區塊——那個區塊 ^TWOII 永遠不會執行到。
    if enable_reversal_alert:
        _make_index_reversal_fragment(
            mis_code, mis_otc, display_symbol, intraday.is_market_open_now(),
            custom_pullback_warn_pct, custom_pullback_severe_pct, custom_fast_drop_severe_pct,
        )()
    st.stop()

last_price = quote["last_price"]
prev_close = quote["previous_close"]
change = last_price - prev_close if (last_price is not None and prev_close) else None
change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None

if change is None or change == 0:
    badge_class, arrow = "badge-flat", "▬"
elif change > 0:
    badge_class, arrow = "badge-up", "▲"
else:
    badge_class, arrow = "badge-down", "▼"

change_str = f"{arrow} {abs(change):.2f} ({abs(change_pct):.2f}%)" if change is not None else "—"
volume_str = f"{quote['volume'] / 1000:,.0f} 張" if quote["volume"] else "—"  # yfinance 回傳的是股,換算成張(1張=1000股)跟台股慣例一致

st.markdown(
    f"""
    <div class="quote-card">
        <div class="quote-symbol">{quote['symbol']} · {name}</div>
        <div class="quote-price-row">
            <div class="quote-price">{last_price:,.2f}</div>
            <div class="quote-badge {badge_class}">{change_str}</div>
        </div>
        <div class="stat-row">
            <div class="stat-item"><div class="label">昨收</div><div class="value">{prev_close:,.2f}</div></div>
            <div class="stat-item"><div class="label">最高</div><div class="value">{quote['day_high']:,.2f}</div></div>
            <div class="stat-item"><div class="label">最低</div><div class="value">{quote['day_low']:,.2f}</div></div>
            <div class="stat-item"><div class="label">成交量</div><div class="value">{volume_str}</div></div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
# 走到這裡的只會是「個股」或「加權指數」,^TWOII 前面已經 st.stop()——兩種情況都適用
# 同一套「這次到底是MIS即時還是yfinance退回」的說明文字。
if "date" in quote:  # 有 date/time 欄位代表這次是 TWSE MIS 官方即時報價,不是 yfinance
    st.caption(f"資料時間 {quote.get('date', '')} {quote.get('time', '')}(TWSE官方即時報價,非精確逐筆)")
else:
    st.caption("⚠️ TWSE即時報價暫時無法取得,顯示的是yfinance延遲報價(官方延遲15~20分鐘)。")

# --- 特殊時間點/重大訊息提醒(只有「個股」模式,不是多空訊號,只有「有事」時才顯示) ---
# 除權息、期貨結算日、重大訊息公告都沒有方向性(不是利多也不是利空的判斷),所以不放進
# 🚦條件達成燈號計分卡,只在這裡用提示訊息顯示——平常沒有接近的事件時完全不顯示,不佔版面。
if query_mode == "個股":
    ex_div = calendar_events.get_upcoming_ex_dividend(code, otc)
    if ex_div:
        st.info(
            f"📅 {ex_div['date']} 除權息(每股{ex_div['cash_dividend']:.2f}元),"
            f"還有{ex_div['trading_days_until']}個交易日,注意價格會有除權息缺口。"
        )
    settlement = calendar_events.get_futures_settlement_info()
    if settlement["is_near"]:
        st.info(
            f"📅 {settlement['settlement_date']} 是期貨/選擇權結算日,還有{settlement['days_until']}天,"
            "結算前後價格可能有非基本面的技術性波動。"
        )
    announcements = calendar_events.get_recent_material_announcements(code, otc)
    if announcements:
        latest = announcements[0]
        st.info(
            f"📰 近3天有{len(announcements)}則重大訊息公告,最新:「{latest['subject']}」({latest['date']})"
            "——公告內容本身沒有方向性,建議自己看內容判斷。"
        )

try:
    df = load_history_with_indicators(code, period, otc)
except Exception as e:
    st.error(f"抓取歷史資料失敗:{e}")
    st.stop()

# --- 盤中即時強弱(只有「個股」模式,加權指數/櫃買指數沒有 TWSE MIS 即時報價可用) ---
# 用 TWSE 官方 mis.twse.com.tw 即時報價(見 intraday.py 說明,yfinance 對台股不夠即時),
# 交易時間內用 st.fragment(run_every=10) 每10秒自動刷新這個區塊,不會拖累整頁重繪
# (K線圖等其他區塊不會跟著每10秒重畫)。非交易時間不自動輪詢,只提供手動刷新按鈕。
if query_mode == "個股":
    intraday_history_key = f"intraday_price_history_{code}_{otc}"
    st.session_state.setdefault(intraday_history_key, [])
    intraday_market_open = intraday.is_market_open_now()

    @st.fragment(run_every=10 if intraday_market_open else None)
    def _render_intraday_strength_section():
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">⚡ 盤中即時強弱</div>', unsafe_allow_html=True)

        iq = intraday.get_intraday_quote(code, otc)
        if iq is None or iq["last_price"] is None:
            st.info("盤中即時資料暫時無法取得(可能尚未開盤或代號查無資料)。")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        hist = st.session_state[intraday_history_key]
        hist.append((time_module.time(), iq["last_price"]))
        st.session_state[intraday_history_key] = hist[-30:]

        avg_vol = float(df["Volume"].tail(5).mean()) if not df.empty else None
        result = intraday.compute_intraday_strength(iq, avg_vol, st.session_state[intraday_history_key])
        score, score_label = result["score"], result["score_label"]

        if score_label in ("強多", "偏多"):
            badge_cls, badge_text = "badge-up", f"▲ {score_label}(分數{score:+.1f})"
        elif score_label in ("強空", "偏空"):
            badge_cls, badge_text = "badge-down", f"▼ {score_label}(分數{score:+.1f})"
        elif score_label == "中性":
            badge_cls, badge_text = "badge-flat", f"▬ 中性(分數{score:+.1f})"
        else:
            badge_cls, badge_text = "badge-flat", "資料不足"

        gauge_html = _build_score_gauge_html(score, 100, intraday.SCORE_STRONG_THRESHOLD)
        stat_items = "".join(
            f'<div class="stat-item"><div class="label">{intraday.SIGNAL_LABELS[key]}</div>'
            f'<div class="value" style="font-size:0.8rem;">{result["signals"][key]["detail"]}</div></div>'
            for key in intraday.SIGNAL_WEIGHTS
        )

        st.markdown(
            f"""
            <div class="quote-card">
                <div class="quote-symbol">{iq['symbol']} · {iq.get('name') or name}</div>
                <div class="quote-price-row">
                    <div class="quote-price">{iq['last_price']:,.2f}</div>
                    <div class="quote-badge {badge_cls}">{badge_text}</div>
                </div>
                {gauge_html}
                <div class="stat-row">
                    {stat_items}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if enable_reversal_alert:
            reversal_signal = reversal_alert.compute_reversal_signal(
                iq.get("day_high"),
                iq["last_price"],
                st.session_state[intraday_history_key],
                pullback_warn_pct=custom_pullback_warn_pct,
                pullback_severe_pct=custom_pullback_severe_pct,
                fast_drop_severe_pct=custom_fast_drop_severe_pct,
            )
            _render_reversal_banner(reversal_signal)

        if intraday_market_open:
            st.caption(f"每10秒自動更新・資料時間 {iq.get('date', '')} {iq.get('time', '')}(TWSE官方即時報價,非精確逐筆)")
        else:
            st.caption(f"目前非交易時段(09:00-13:30),顯示最後資料・資料時間 {iq.get('date', '')} {iq.get('time', '')}")
            st.button("🔄 立即刷新", key=f"intraday_manual_refresh_{code}_{otc}")

        st.caption(
            "5個訊號(當日區間位置/開盤動能/量能比/委買委賣力道/短線動能)加權平均成分數,"
            "權重跟正規化幅度都是主觀訂的,不是統計驗證過的數字。委買委賣力道是五檔掛單量的"
            "代理指標,不是逐筆成交的真實內外盤比;短線動能只從打開這檔股票開始累積樣本,"
            "不是從開盤算起。純觀察參考,不是下單訊號。"
        )
        st.markdown("</div>", unsafe_allow_html=True)

    _render_intraday_strength_section()
elif code == "^TWII" and enable_reversal_alert:
    # 櫃買指數(^TWOII)的急殺警示已經在報價卡片那段、st.stop()之前呼叫過了,這裡只剩
    # 加權指數需要處理。enable_reversal_alert 是False時完全不呼叫,連fragment都不建立,
    # 不會多打TWSE即時報價API去輪詢。
    _make_index_reversal_fragment(
        mis_code, mis_otc, display_symbol, intraday.is_market_open_now(),
        custom_pullback_warn_pct, custom_pullback_severe_pct, custom_fast_drop_severe_pct,
    )()

IS_INDEX = code in INDEX_DISPLAY_NAMES  # 這裡只會是「個股」或「加權指數」,櫃買指數在上面已經 st.stop()


def _render_chart_section():
    """K線圖+技術指標,個股跟加權指數共用——加權指數沒有三大法人/權證/分點籌碼資料,
    「做多訊號」「分點掃描」這兩個分頁對指數沒有意義,所以只有這個分頁在指數模式下也會顯示。
    """
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">價格走勢與技術指標</div>', unsafe_allow_html=True)

    supports = find_nearest_supports(df)
    resistances = find_nearest_resistances(df)
    chart_html = _build_tradingview_chart_html(
        df, f"{quote['symbol']} · {name}", height=780, supports=supports, resistances=resistances
    )
    components.html(chart_html, height=780, scrolling=False)

    close = float(df["Close"].iloc[-1])
    if supports:
        st.caption(f"近3個月籌碼支撐(成交量分佈區域高峰,離目前收盤價最近的{len(supports)}個):")
        st.markdown(_build_level_badges_html(supports, close, CHART_THEME["support"]), unsafe_allow_html=True)
    else:
        st.caption("近3個月籌碼資料不足,找不到明顯支撐。")
    if resistances:
        st.caption(f"近3個月籌碼阻力(成交量分佈區域高峰,離目前收盤價最近的{len(resistances)}個):")
        st.markdown(_build_level_badges_html(resistances, close, CHART_THEME["resistance"]), unsafe_allow_html=True)
    else:
        st.caption("近3個月籌碼資料不足,找不到明顯阻力。")
    st.markdown("</div>", unsafe_allow_html=True)


if IS_INDEX:
    # 指數沒有三大法人/權證/分點籌碼資料,「做多訊號」「分點掃描」對指數沒有意義,
    # 不用 st.tabs() 包單一分頁,技術分析內容直接照樣渲染。
    _render_chart_section()
    st.stop()

tab_chart, tab_ai, tab_scan = st.tabs(["📊 技術分析", "🎯 做多訊號", "🔎 分點掃描"])

with tab_chart:
    _render_chart_section()

# --- 做多訊號 ---
with tab_ai:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🎯 法人 + 權證做多訊號(自訂規則)</div>', unsafe_allow_html=True)
    st.caption(
        "規則:三大法人近 10 天合計買賣超為正的天數佔比 ≥ 70%,且個股認購權證近 1 天內有單一檔權證成交金額 ≥ 50 萬元且當天收紅,"
        "兩者同時成立才判定為做多訊號。若有提供 XQ 匯出的分點資料,再加上第三個條件:"
        "已知隔日沖大戶分點(凱基-城中/永豐金-市政/富邦-台南/凱基-岡山/凱基-三重/凱基-高雄)有出現在買方名單且買超為正,"
        "沒有命中的話就退回看「買超第1名分點金額 ≥ 500 萬元」。純粹規則比對,不是模型。"
    )
    st.caption(
        "⚠️ TWSE 公開資料只有「每檔權證當天成交總金額」,無法區分買方主導還是賣方主導(大額成交也可能是主力倒貨),"
        "所以用「當天股價有沒有收紅」當粗略代理,只算收紅的大額成交——但這只能濾掉整天收黑的假訊號,"
        "股價上漲的那一天,同一天內仍然可能混有主力倒貨的大額成交(因為權證漲跌高度連動正股當天走勢,"
        "不是逐筆判斷),不是真正的買賣方向判定,僅供參考。"
    )
    if otc:
        st.caption("三大法人資料只支援上市股票,上櫃股票無法檢查此訊號。")
    else:
        if st.button("🔍 檢查做多訊號"):
            # 結果存 session_state、渲染邏輯搬到按鈕區塊外面每次都執行——跟🚦條件達成燈號/
            # 💪RS排行同樣的坑:st.button()只在按下當次回傳True,下一次不管什麼原因(包括
            # 自動刷新計時器)觸發的rerun都會讓結果消失。這裡單股查詢通常很快、不會超過
            # AUTO_REFRESH_SECONDS,但一樣加_long_task_running防護,避免查詢中途剛好被
            # 計時器插隊中斷。
            st.session_state["_long_task_running"] = True
            with st.spinner("查詢籌碼資料中..."):
                try:
                    from signals import evaluate_long_signal

                    sig = evaluate_long_signal(code, otc=otc)
                    st.session_state["_long_signal_result"] = {
                        "mode": "result", "sig": sig, "code": code, "otc": otc,
                    }
                except Exception as e:
                    st.session_state["_long_signal_result"] = {
                        "mode": "error", "message": str(e), "code": code, "otc": otc,
                    }
                finally:
                    st.session_state["_long_task_running"] = False

        long_signal_state = st.session_state.get("_long_signal_result")
        if long_signal_state and long_signal_state["code"] == code and long_signal_state["otc"] == otc:
            if long_signal_state["mode"] == "error":
                st.error(f"查詢失敗:{long_signal_state['message']}")
            else:
                sig = long_signal_state["sig"]
                n_conditions = 3 if sig["branch_available"] else 2
                if sig["long_signal"]:
                    st.success(f"✅ 符合做多訊號——{n_conditions} 個條件同時成立")
                else:
                    st.info("目前不符合做多訊號")

                sc1, sc2, sc3 = st.columns(3)
                sc1.metric(
                    f"三大法人近{sig['institutional_window_days']}天正買超佔比",
                    f"{sig['institutional_positive_ratio']*100:.0f}%",
                    delta="達標 ✓" if sig["institutional_signal"] else "未達 70%",
                    delta_color="off",
                )
                sc1.caption(f"{sig['institutional_positive_days']}/{sig['institutional_window_days']} 天為正,資料日期 {sig['institutional_asof']}")
                sc2.metric(
                    f"認購權證近{sig['warrant_window_days']}天≥50萬且收紅檔數",
                    f"{sig['warrant_large_trade_count']} 筆",
                    delta="達標 ✓" if sig["warrant_signal"] else "未達 1 筆",
                    delta_color="off",
                )
                sc2.caption(f"單一檔權證當天成交金額 ≥ 50 萬且當天收紅才算 1 筆,資料日期 {sig['warrant_asof']}")

                if sig["branch_available"]:
                    known_hits = sig["branch_known_hits"]
                    if known_hits:
                        hit_desc = "、".join(f"{h['known_name']}({h['net_buy_wan']:,.0f}萬)" for h in known_hits)
                        sc3.metric(
                            "已知隔日沖大戶分點命中",
                            f"{len(known_hits)} 個",
                            delta="達標 ✓" if sig["branch_signal"] else "無買超",
                            delta_color="off",
                        )
                        sc3.caption(f"{hit_desc}(資料日期 {sig['branch_asof']},檔案:{sig['branch_file']})")
                    else:
                        sc3.metric(
                            "認購權證買超第1名分點",
                            f"{sig['branch_top1_net_buy_wan']:,.0f} 萬",
                            delta="達標 ✓" if sig["branch_signal"] else "未達 500 萬",
                            delta_color="off",
                        )
                        sc3.caption(
                            f"{sig['branch_top1_broker']}(沒有命中已知隔日沖大戶分點,改用排名第1當代理指標;"
                            f"資料日期 {sig['branch_asof']},檔案:{sig['branch_file']})"
                        )
                else:
                    sc3.caption(
                        "沒有找到分點資料,此條件略過。"
                        "可在 XQ 匯出「個股進階籌碼→權證券商→買方TOP15→類型:認購」CSV,"
                        f"存到 xq_branch_data/ 資料夾、檔名開頭是股票代號(例如 {code}xxx.csv)。"
                    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🚦 條件達成燈號(自訂規則)</div>', unsafe_allow_html=True)
    st.caption(
        "7 個獨立條件各自顯示目前有沒有達成,不要求同時成立(跟上面的做多訊號不同,這裡只是計分卡,"
        "進場判斷交給你自己看):① 股價剛同時站上6/40/56EMA(今天剛突破,不是已經站上一段時間)"
        "② 三大法人近2天剛轉為買超(前一天還是賣超)③ 認購權證近1天單筆≥50萬且當天收紅的檔數超過4筆"
        "④ 成交量超過前3日均量 ⑤ RS(近20天報酬率-加權指數同期報酬率)在0軸之上"
        "⑥ 融資餘額比前一筆減少(籌碼轉健康,只支援上市,且這個資料源沒有歷史API只能逐日累積,"
        "剛開始查一檔新股票會顯示資料不足)⑦ 最新一個月營收年增率為正。"
    )

    light_scope = st.radio(
        "檢查範圍", ["只檢查目前查詢的這檔股票", "檢查整個 .dsl 股票池(有個股期貨的標的)"], horizontal=True, key="light_scope"
    )
    light_min_count = None
    if light_scope == "檢查整個 .dsl 股票池(有個股期貨的標的)":
        light_min_count = st.slider("只列出至少達成幾項條件的股票", min_value=1, max_value=7, value=3)
        st.caption(
            "股價/RS 逐檔股價要逐股抓,法人/權證資料則是整個股票池一次批次抓取(不是每檔股票各自打一次),"
            "所以不管股票池多大,主要瓶頸是股票數量本身(逐檔抓股價這段),第一次跑會比較久。"
        )

    def _style_condition_cell(v):
        # 跟quote-badge的badge-hit/badge-miss維持同一套紅綠燈直覺(達成綠/未達成紅)
        if v == "達成":
            return "background-color: rgba(34,197,94,0.16); color: #22c55e; font-weight: 600;"
        return "background-color: rgba(239,68,68,0.12); color: #ef4444;"

    if st.button("🚦 檢查燈號"):
        # 這裡只負責「算」,算完存進session_state,不直接畫結果——批次掃描動輒跑好幾分鐘,
        # 跑完之後如果剛好碰上auto_refresh的下一次整頁重跑(st.button()的按下狀態只在
        # 「這一次」腳本執行有效,下一次重跑不管什麼原因觸發,這個if區塊都不會再進來),
        # 畫在這個if區塊裡的結果會直接消失,使用者會看到「跑完的瞬間又整個不見」。
        # 存進session_state、在下面用獨立區塊每次重跑都讀出來畫,才能保證結果會一直留著,
        # 直到使用者再按一次「檢查燈號」或換股票重新查詢。
        st.session_state["_long_task_running"] = True
        with st.spinner("檢查中..."):
            try:
                if light_scope == "檢查整個 .dsl 股票池(有個股期貨的標的)":
                    from xq_watchlist import get_stock_futures_codes_from_watchlists
                    from signals import scan_light_signals

                    codes = sorted(get_stock_futures_codes_from_watchlists())
                    if not codes:
                        st.session_state["_light_result"] = {"mode": "no_watchlist"}
                    else:
                        progress = st.progress(0.0, text=f"檢查中... 0/{len(codes)}")

                        def _on_light_progress(done, total):
                            progress.progress(done / total if total else 1.0, text=f"檢查中... {done}/{total}")

                        results = scan_light_signals(codes, progress_callback=_on_light_progress)
                        progress.empty()
                        st.session_state["_light_result"] = {
                            "mode": "batch",
                            "total_checked": len(results),
                            "qualified": [r for r in results if r["passed_count"] >= light_min_count],
                            "min_count": light_min_count,
                        }
                else:
                    from signals import evaluate_light_signals

                    st.session_state["_light_result"] = {
                        "mode": "single",
                        "light_result": evaluate_light_signals(code, df, otc=otc),
                    }
            except Exception as e:
                st.session_state["_light_result"] = {"mode": "error", "message": str(e)}
            finally:
                st.session_state["_long_task_running"] = False

    light_state = st.session_state.get("_light_result")
    if light_state:
        if light_state["mode"] == "error":
            st.error(f"檢查失敗:{light_state['message']}")
        elif light_state["mode"] == "no_watchlist":
            st.warning("找不到 .dsl 自選股清單,請確認 xq_branch_data/ 資料夾裡有匯出的 .dsl 檔案。")
        elif light_state["mode"] == "batch":
            from stock_futures import get_stock_futures_name

            qualified = light_state["qualified"]
            min_count = light_state["min_count"]
            if not qualified:
                st.info(f"共檢查 {light_state['total_checked']} 檔股票,沒有股票達成 {min_count} 項以上條件。")
            else:
                st.success(f"共檢查 {light_state['total_checked']} 檔股票,{len(qualified)} 檔達成 {min_count} 項以上條件")
                rows = []
                condition_labels = [c["label"] for c in qualified[0]["conditions"].values()]
                for r in qualified:
                    row = {
                        "代號": r["code"],
                        "名稱": get_stock_futures_name(r["code"]) or "",
                        "達成數": f"{r['passed_count']}/{r['total']}",
                    }
                    for c in r["conditions"].values():
                        row[c["label"]] = "達成" if c["passed"] else "未達成"
                    rows.append(row)
                styled = pd.DataFrame(rows).style.map(_style_condition_cell, subset=condition_labels)
                st.dataframe(styled, use_container_width=True, hide_index=True)
        elif light_state["mode"] == "single":
            light_result = light_state["light_result"]
            st.success(f"{light_result['total']} 個條件中達成 {light_result['passed_count']}/{light_result['total']} 個")
            light_cols = st.columns(light_result["total"])
            for lcol, c in zip(light_cols, light_result["conditions"].values()):
                lcol.markdown(_build_condition_badge_html(c["label"], c["passed"]), unsafe_allow_html=True)
                lcol.caption(c["detail"])
    st.markdown("</div>", unsafe_allow_html=True)

# --- 分點掃描 ---
with tab_scan:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🔎 掃描已知大戶分點買超</div>', unsafe_allow_html=True)
    st.caption(
        "掃描 xq_branch_data/ 資料夾裡「所有」你已經匯出過的分點檔案,"
        "找出哪些股票目前有已知隔日沖大戶分點在買超認購權證。只要有匯出分點檔案的股票都會納入掃描,"
        "不限制是不是個股期貨標的。"
        "這是跨股票的批次掃描,跟上面「做多訊號」分頁裡針對單一股票的做多訊號檢查是分開的功能。"
    )
    if st.button("🔎 開始掃描"):
        # 結果存session_state、渲染搬到按鈕區塊外——跟🚦條件達成燈號/💪RS排行同樣的坑,
        # 這裡掃描整個xq_branch_data/資料夾檔案數多時可能不快,加_long_task_running防護
        # 避免被auto_refresh計時器中途打斷。
        st.session_state["_long_task_running"] = True
        with st.spinner("掃描中..."):
            try:
                from xq_branch import scan_known_branch_buying

                raw_hits, _ = scan_known_branch_buying()
                # 不再限制個股期貨標的之後,CSV 檔名開頭抓出來的代號有可能是打錯字、已下市、
                # 或其他不是真正股票代號的字串——用 TWSE ISIN 對照表(get_chinese_name)驗證
                # 代號真的存在,查不到中文名稱的代號視為「代號對不上真實股票」,不列入結果。
                hits, unverified = [], []
                for h in raw_hits:
                    hit_name = get_chinese_name(h["code"], otc=False) or get_chinese_name(h["code"], otc=True)
                    if hit_name:
                        hits.append({**h, "name": hit_name})
                    else:
                        unverified.append(h["code"])

                st.session_state["_branch_scan_result"] = {
                    "mode": "result", "hits": hits, "unverified": unverified,
                }
            except Exception as e:
                st.session_state["_branch_scan_result"] = {"mode": "error", "message": str(e)}
            finally:
                st.session_state["_long_task_running"] = False

    branch_scan_state = st.session_state.get("_branch_scan_result")
    if branch_scan_state:
        if branch_scan_state["mode"] == "error":
            st.error(f"掃描失敗:{branch_scan_state['message']}")
        else:
            hits = branch_scan_state["hits"]
            unverified = branch_scan_state["unverified"]
            if not hits:
                st.info("目前沒有任何股票命中已知隔日沖大戶分點買超。")
            else:
                st.success(f"共 {len(hits)} 檔命中")
                for h in hits:
                    hit_desc = "、".join(f"{x['known_name']}({x['net_buy_wan']:,.0f}萬)" for x in h["known_branch_hits"])
                    st.markdown(f"**{h['code']} {h['name']}** — {hit_desc}（資料日期 {h['asof']}，檔案:{h['file']}）")
            if unverified:
                st.caption(f"已略過 {len(unverified)} 個代號對不上真實股票的檔案:{'、'.join(unverified)}")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">💪 RS 相對強弱排行(個股期貨標的)</div>', unsafe_allow_html=True)
    st.caption(
        "概念取自 IBD 公開的 RS Rating 方法論:近3個月報酬權重是近6/9/12個月的兩倍,加權後"
        "在整個股票池裡排百分位名次(99 = 全池最強,1 = 全池最弱)。股票池用你在 XQ 維護的"
        "「個股期貨標的」自選清單(.dsl 檔)。第一次計算要逐檔抓 14 個月股價資料,"
        "會比較久,當天算過的結果會存快取,同一天內重複計算會很快。上市不到 12 個月的新股會被跳過。"
    )
    def _rs_rating_bg(v):
        # RS Rating(0~99)用金色底色深淺表示強弱,一眼看出排名高低,不用逐格看數字比大小——
        # 單一色相由淺到深,是排名/幅度資料常見的視覺分級做法
        alpha = 0.04 + (v / 99) * 0.34
        return f"background-color: rgba(234,179,8,{alpha:.2f}); font-weight:600;"

    def _return_pct_color(v):
        # 報酬率欄位比照全站紅漲綠跌慣例上色,跟其他地方(quote-badge等)是同一套顏色語言
        try:
            num = float(str(v).rstrip("%"))
        except ValueError:
            return ""
        if num > 0:
            return "color: #ef4444;"
        if num < 0:
            return "color: #22c55e;"
        return ""

    if st.button("💪 開始計算 RS 排行"):
        # 只負責算、存進session_state,不直接畫——理由跟上面「🚦檢查燈號」批次掃描完全一樣:
        # 逐檔抓14個月股價資料動輒跑1~2分鐘以上,跑完那一刻如果直接畫在這個if區塊裡,
        # 只要接下來auto_refresh的下一次整頁重跑一到(st.button()這次沒被按,if不會進來),
        # 剛跑出來的結果就會整個消失。存起來、在下面用獨立區塊每次重跑都讀出來畫,才能一直留著。
        st.session_state["_long_task_running"] = True
        try:
            from xq_watchlist import get_stock_futures_codes_from_watchlists
            from relative_strength import compute_rs_ranking

            universe = sorted(get_stock_futures_codes_from_watchlists())
            if not universe:
                st.session_state["_rs_result"] = {"mode": "no_watchlist"}
            else:
                progress = st.progress(0.0, text=f"計算中... 0/{len(universe)}")

                def _on_rs_progress(done, total):
                    progress.progress(done / total if total else 1.0, text=f"計算中... {done}/{total}")

                ranking = compute_rs_ranking(universe, progress_callback=_on_rs_progress)
                progress.empty()
                st.session_state["_rs_result"] = {
                    "mode": "ranking",
                    "ranking": ranking,
                    "universe_size": len(universe),
                    "code": code,
                    "otc": otc,
                }
        except Exception as e:
            st.session_state["_rs_result"] = {"mode": "error", "message": str(e)}
        finally:
            st.session_state["_long_task_running"] = False

    rs_state = st.session_state.get("_rs_result")
    if rs_state:
        if rs_state["mode"] == "error":
            st.error(f"RS 排行計算失敗:{rs_state['message']}")
        elif rs_state["mode"] == "no_watchlist":
            st.warning("找不到 .dsl 自選股清單,請確認 xq_branch_data/ 資料夾裡有匯出的 .dsl 檔案。")
        elif rs_state["mode"] == "ranking":
            from stock_futures import get_stock_futures_name

            ranking = rs_state["ranking"]
            if ranking.empty:
                st.info("沒有算出任何股票的 RS 排行(可能股票池裡都是上市不到 12 個月的新股)。")
            else:
                st.success(f"共算出 {len(ranking)} / {rs_state['universe_size']} 檔股票的 RS 排行")
                display = ranking.copy()
                display["名稱"] = display["code"].map(lambda c: get_stock_futures_name(c) or "")
                for col, label in [("r3m", "近3月報酬"), ("r6m", "近6月報酬"), ("r12m", "近12月報酬")]:
                    display[label] = display[col].map(lambda x: f"{x*100:+.1f}%")
                display = display.rename(columns={"code": "代號", "rs_rating": "RS Rating"})

                return_cols = ["近3月報酬", "近6月報酬", "近12月報酬"]
                styled_ranking = (
                    display[["代號", "名稱", "RS Rating"] + return_cols]
                    .style.map(_rs_rating_bg, subset=["RS Rating"])
                    .map(_return_pct_color, subset=return_cols)
                )
                st.dataframe(styled_ranking, use_container_width=True, hide_index=True)

                current_row = ranking[ranking["code"] == rs_state["code"]]
                if not current_row.empty:
                    st.caption(f"目前查詢的 {rs_state['code']} 在此排行裡的 RS Rating:{int(current_row.iloc[0]['rs_rating'])}")
                elif not rs_state["otc"]:
                    st.caption(f"{rs_state['code']} 不在目前的 .dsl 自選清單股票池裡,所以沒有算入這次排行。")
    st.markdown("</div>", unsafe_allow_html=True)

