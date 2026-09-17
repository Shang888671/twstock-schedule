"""股市查詢模型 — Streamlit 網頁介面。

執行方式: streamlit run app.py
"""

import json
import time as time_module

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
)
from indicators import add_indicators
from volume_profile import find_nearest_supports, find_nearest_resistances
from us_market import get_us_overnight_signal, US_MARKET_SYMBOLS, STRENGTH_WEIGHT, SCORE_STRONG_THRESHOLD
from xq_branch import XQ_BRANCH_DIR

st.set_page_config(page_title="台股查詢模型", page_icon="📈", layout="wide")

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


AUTO_REFRESH_SECONDS = 60


@st.fragment(run_every=AUTO_REFRESH_SECONDS)
def _auto_refresh_tick():
    """純計時器,不畫任何東西——每60秒觸發一次 st.rerun()(預設 scope="app",整頁重跑,
    不是只重跑這個 fragment 自己),讓各區塊的 st.cache_data 過期後能盡快自動撈到新資料,
    不用使用者手動重新整理瀏覽器。60秒間隔選這個數字是因為大部分快取TTL是300秒(5分鐘)、
    夜盤是1800秒——刷新得比TTL短很多也拿不到新資料,只是白白重繪整頁;60秒是「感覺得到
    在動、又不浪費」的折衷點。

    用 session_state 記錄上次真的觸發 rerun 的時間點,靠這個判斷「這次呼叫是不是計時器到期
    才觸發的」——如果不這樣擋,這個 function 每次被呼叫(包括使用者互動造成的全頁重跑,
    或這個 function 自己呼叫 st.rerun() 之後緊接著的那次重跑)都會無條件立刻再呼叫一次
    st.rerun(),變成無窮迴圈(第一版就是這樣寫,結果整頁卡死變空白,一直重跑出不去)。"""
    now = time_module.time()
    last = st.session_state.get("_auto_refresh_last_tick", 0)
    if now - last >= AUTO_REFRESH_SECONDS:
        st.session_state["_auto_refresh_last_tick"] = now
        st.rerun()


with st.sidebar:
    st.markdown("### 📈 台股查詢模型")
    st.caption("即時報價・技術指標・做多訊號")
    st.divider()
    auto_refresh = st.checkbox("🔄 自動刷新(每60秒)", value=True,
                                help="每60秒自動重新整理頁面。各區塊資料實際更新頻率仍取決於"
                                     "各自的快取有效期(大部分5分鐘,夜盤30分鐘),這個開關只是"
                                     "確保快取過期後不用手動重整就能盡快看到新資料。")
    if auto_refresh:
        _auto_refresh_tick()
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
            "櫃買指數(^TWOII)改抓TWSE MIS即時報價(真即時,非延遲),但沒有歷史K線資料,"
            "技術分析/做多訊號/分點掃描功能無法使用。"
            if query_mode == "櫃買指數" else "加權指數(^TWII)功能跟查個股一樣完整。"
        )
    period = st.selectbox("歷史資料區間", ["3mo", "6mo", "1y", "2y", "5y"], index=2)
    st.divider()

    # 急殺警示,原本只能在 alert_config.py(背景推播腳本用的設定檔)裡改門檻,網頁上完全
    # 沒有對應的輸入欄位。第一版補了門檻輸入框,但沒勾選任何東西的狀況下警示卡片還是
    # 一直用系統預設值跑——使用者明確要求「要有勾選才能啟動警示設定,沒有預設」,改成
    # 這個checkbox現在是「整個急殺警示功能」的總開關,不勾就完全不顯示、也不會多打即時
    # 報價API去輪詢,不是「用預設值默默跑在背景」。
    with st.expander("⚙️ 盤中急殺警示設定"):
        st.caption("距今日高點拉回%、或近5分鐘變動%,任一超過門檻就升級警示。預設不啟用,"
                   "勾選下面的開關才會顯示警示卡片、開始輪詢即時報價。")
        enable_reversal_alert = st.checkbox(
            "啟用盤中急殺警示", value=False,
            help="不勾選就完全不顯示急殺警示卡片,也不會多打TWSE即時報價API;"
                 "勾選後才會用下面填的門檻開始監控。",
        )
        custom_pullback_warn_pct = st.number_input(
            "拉回警示門檻(%)", min_value=0.1, max_value=20.0,
            value=reversal_alert.PULLBACK_WARN_PCT, step=0.1, disabled=not enable_reversal_alert,
        )
        custom_pullback_severe_pct = st.number_input(
            "急殺警示門檻(%,距高點拉回)", min_value=0.1, max_value=30.0,
            value=reversal_alert.PULLBACK_SEVERE_PCT, step=0.1, disabled=not enable_reversal_alert,
        )
        custom_fast_drop_severe_pct = st.number_input(
            "急殺警示門檻(%,近5分鐘變動)", min_value=0.1, max_value=10.0,
            value=reversal_alert.FAST_DROP_SEVERE_PCT, step=0.1, disabled=not enable_reversal_alert,
        )
        if enable_reversal_alert and custom_pullback_severe_pct < custom_pullback_warn_pct:
            st.warning("急殺門檻比拉回門檻還小,「拉回」這個中間等級實際上不會出現,"
                       "拉回超過拉回門檻就會直接跳成急殺——如果不是故意的,建議急殺門檻"
                       "設得比拉回門檻大。")

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


@st.cache_data(ttl=300)
def load_history_with_indicators(code, period, otc):
    hist = get_history(code, period=period, otc=otc)
    return add_indicators(hist)


@st.cache_data(ttl=300)
def load_us_overnight_signal():
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
        <h1>📈 台股查詢模型 <span class="badge-gold">TW MARKET</span></h1>
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
    SCORE_MAX = max(STRENGTH_WEIGHT.values()) * len(US_MARKET_SYMBOLS)
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

    def _us_light_html(label, r):
        if not r:
            return (
                f'<span title="{label}:資料不足" style="display:inline-block; width:11px; height:11px; '
                f'border-radius:50%; background:{US_LIGHT_FLAT_COLOR}; margin-right:6px; vertical-align:middle;"></span>'
            )
        if r["change_pct"] == 0:
            color, detail = US_LIGHT_FLAT_COLOR, "持平"
        else:
            sign = "up" if r["change_pct"] > 0 else "down"
            strength = r.get("strength") or "普通"
            color = US_LIGHT_COLORS[(sign, strength)]
            detail = f"{r['change_pct']:+.2f}%" + (f"({r['strength']})" if r.get("strength") else "")
        return (
            f'<span title="{label}:{detail}" style="display:inline-block; width:11px; height:11px; '
            f'border-radius:50%; background:{color}; margin-right:6px; vertical-align:middle;"></span>'
        )

    def _us_stat_html(label, r):
        if not r:
            return f'<div class="stat-item"><div class="label">{label}</div><div class="value">—</div></div>'
        arrow = "▲" if r["change_pct"] > 0 else ("▼" if r["change_pct"] < 0 else "▬")
        color = "var(--tw-up)" if r["change_pct"] > 0 else ("var(--tw-down)" if r["change_pct"] < 0 else "#9ca3af")
        strength = r.get("strength")
        strength_html = (
            f' <span style="color:{US_STRENGTH_COLOR[strength]}; font-size:0.75rem; font-weight:700;">'
            f"{strength}</span>"
            if strength
            else ""
        )
        return (
            f'<div class="stat-item"><div class="label">{label}(收{r["asof"][:10]})</div>'
            f'<div class="value" style="color:{color}">{arrow} {r["change_pct"]:+.2f}%{strength_html}</div></div>'
        )

    stat_items = "".join(
        _us_stat_html(label, us_signal[key]) for key, (_, label) in US_MARKET_SYMBOLS.items()
    )
    light_items = "".join(
        _us_light_html(label, us_signal[key]) for key, (_, label) in US_MARKET_SYMBOLS.items()
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
        today_row_html = (
            f'<div class="quote-price-row"><div class="quote-badge {otc_badge_class}">今日 {otc_label}</div>'
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
            <div class="quote-symbol">🌙 美股夜盤連動指標</div>
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
        "小道瓊/那斯達克期貨(YM=F/NQ=F)近24小時交易,涵蓋最新夜盤走勢;費半(^SOX)、"
        "台積電ADR(TSM)是美股現貨收盤價。「強/普通/弱」是今天漲跌幅度跟自己近20日平均"
        "單日波動的比較,不是固定的絕對門檻。淨分是4個指標依強弱加權(弱1分/普通2分/強3分)"
        f"後加總的多空分數,範圍 ±{SCORE_MAX},橫條顯示淨分離兩端滿分有多遠,越極端代表訊號"
        "越一致越強烈。badge旁邊4個燈號依序對應小道瓊期貨/那斯達克期貨/費半/台積電ADR,"
        "顏色深淺=強弱、紅漲綠跌(hover可看細節)。純觀察參考,不是下單訊號。"
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
    ratio_str = f"(量能比 {night_signal['ratio']:.2f}倍)" if night_signal["ratio"] is not None else ""

    chg = night_signal["front_month_change_pct"]
    if chg is None:
        chg_badge_class, chg_str = "badge-flat", "—"
    elif chg > 0:
        chg_badge_class, chg_str = "badge-up", f"▲ {chg:+.2f}%"
    elif chg < 0:
        chg_badge_class, chg_str = "badge-down", f"▼ {chg:+.2f}%"
    else:
        chg_badge_class, chg_str = "badge-flat", "▬ 0.00%"

    st.markdown(
        f"""
        <div class="quote-card">
            <div class="quote-symbol">🌆 台指期夜盤參與度</div>
            <div class="quote-price-row">
                <div class="quote-badge badge-flat">{strength_text}{ratio_str}</div>
                <div class="quote-badge {chg_badge_class}">{chg_str}</div>
            </div>
            <div class="stat-row">
                <div class="stat-item"><div class="label">夜盤成交量</div><div class="value">{night_signal['volume']:,} 口</div></div>
                <div class="stat-item"><div class="label">近20日均量</div><div class="value">{night_signal['avg_volume']:,.0f} 口</div></div>
                <div class="stat-item"><div class="label">資料日期</div><div class="value">{night_signal['date']}</div></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "台股期貨(TX)15:00~次日05:00盤後交易時段全合約加總成交量,對比近20個交易日均量的"
        "比值,反映的是台灣資金自己的參與度/信心強度,不是多空方向——量大只代表當晚交易熱絡、"
        "資金關注度高,不代表偏多或偏空。近月合約當晚漲跌%只是補充參考,不是這個指標的主體。"
        "純觀察參考,不是下單訊號。"
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
    st.info("這個指數沒有任意區間的歷史K線資料可用,技術分析、做多訊號、分點掃描這幾個功能都需要歷史資料才能運作,暫不支援。")
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
        support_desc = "、".join(f"{s['price']:,.2f}({s['price']/close - 1:+.1%})" for s in supports)
        st.caption(f"近3個月籌碼支撐(成交量分佈區域高峰,離目前收盤價最近的{len(supports)}個):{support_desc}")
    else:
        st.caption("近3個月籌碼資料不足,找不到明顯支撐。")
    if resistances:
        resistance_desc = "、".join(f"{r['price']:,.2f}({r['price']/close - 1:+.1%})" for r in resistances)
        st.caption(f"近3個月籌碼阻力(成交量分佈區域高峰,離目前收盤價最近的{len(resistances)}個):{resistance_desc}")
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
    elif st.button("🔍 檢查做多訊號"):
        with st.spinner("查詢籌碼資料中..."):
            try:
                from signals import evaluate_long_signal

                sig = evaluate_long_signal(code, otc=otc)
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
            except Exception as e:
                st.error(f"查詢失敗:{e}")
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

    if st.button("🚦 檢查燈號"):
        with st.spinner("檢查中..."):
            try:
                if light_scope == "檢查整個 .dsl 股票池(有個股期貨的標的)":
                    from xq_watchlist import get_stock_futures_codes_from_watchlists
                    from stock_futures import get_stock_futures_name
                    from signals import scan_light_signals

                    codes = sorted(get_stock_futures_codes_from_watchlists())
                    if not codes:
                        st.warning("找不到 .dsl 自選股清單,請確認 xq_branch_data/ 資料夾裡有匯出的 .dsl 檔案。")
                    else:
                        progress = st.progress(0.0, text=f"檢查中... 0/{len(codes)}")

                        def _on_light_progress(done, total):
                            progress.progress(done / total if total else 1.0, text=f"檢查中... {done}/{total}")

                        results = scan_light_signals(codes, progress_callback=_on_light_progress)
                        progress.empty()

                        qualified = [r for r in results if r["passed_count"] >= light_min_count]
                        if not qualified:
                            st.info(f"共檢查 {len(results)} 檔股票,沒有股票達成 {light_min_count} 項以上條件。")
                        else:
                            st.success(f"共檢查 {len(results)} 檔股票,{len(qualified)} 檔達成 {light_min_count} 項以上條件")
                            rows = []
                            for r in qualified:
                                row = {
                                    "代號": r["code"],
                                    "名稱": get_stock_futures_name(r["code"]) or "",
                                    "達成數": f"{r['passed_count']}/{r['total']}",
                                }
                                for c in r["conditions"].values():
                                    row[c["label"]] = "🟢" if c["passed"] else "🔴"
                                rows.append(row)
                            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                else:
                    from signals import evaluate_light_signals

                    light_result = evaluate_light_signals(code, df, otc=otc)
                    st.success(f"{light_result['total']} 個條件中達成 {light_result['passed_count']}/{light_result['total']} 個")

                    light_cols = st.columns(light_result["total"])
                    for lcol, c in zip(light_cols, light_result["conditions"].values()):
                        lcol.metric(c["label"], "🟢 達成" if c["passed"] else "🔴 未達成")
                        lcol.caption(c["detail"])
            except Exception as e:
                st.error(f"檢查失敗:{e}")
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

                if not hits:
                    st.info("目前沒有任何股票命中已知隔日沖大戶分點買超。")
                else:
                    st.success(f"共 {len(hits)} 檔命中")
                    for h in hits:
                        hit_desc = "、".join(f"{x['known_name']}({x['net_buy_wan']:,.0f}萬)" for x in h["known_branch_hits"])
                        st.markdown(f"**{h['code']} {h['name']}** — {hit_desc}（資料日期 {h['asof']}，檔案:{h['file']}）")
                if unverified:
                    st.caption(f"已略過 {len(unverified)} 個代號對不上真實股票的檔案:{'、'.join(unverified)}")
            except Exception as e:
                st.error(f"掃描失敗:{e}")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">💪 RS 相對強弱排行(個股期貨標的)</div>', unsafe_allow_html=True)
    st.caption(
        "概念取自 IBD 公開的 RS Rating 方法論:近3個月報酬權重是近6/9/12個月的兩倍,加權後"
        "在整個股票池裡排百分位名次(99 = 全池最強,1 = 全池最弱)。股票池用你在 XQ 維護的"
        "「個股期貨標的」自選清單(.dsl 檔)。第一次計算要逐檔抓 14 個月股價資料,"
        "會比較久,當天算過的結果會存快取,同一天內重複計算會很快。上市不到 12 個月的新股會被跳過。"
    )
    if st.button("💪 開始計算 RS 排行"):
        try:
            from xq_watchlist import get_stock_futures_codes_from_watchlists
            from stock_futures import get_stock_futures_name
            from relative_strength import compute_rs_ranking

            universe = sorted(get_stock_futures_codes_from_watchlists())
            if not universe:
                st.warning("找不到 .dsl 自選股清單,請確認 xq_branch_data/ 資料夾裡有匯出的 .dsl 檔案。")
            else:
                progress = st.progress(0.0, text=f"計算中... 0/{len(universe)}")

                def _on_rs_progress(done, total):
                    progress.progress(done / total if total else 1.0, text=f"計算中... {done}/{total}")

                ranking = compute_rs_ranking(universe, progress_callback=_on_rs_progress)
                progress.empty()

                if ranking.empty:
                    st.info("沒有算出任何股票的 RS 排行(可能股票池裡都是上市不到 12 個月的新股)。")
                else:
                    st.success(f"共算出 {len(ranking)} / {len(universe)} 檔股票的 RS 排行")
                    display = ranking.copy()
                    display["名稱"] = display["code"].map(lambda c: get_stock_futures_name(c) or "")
                    for col, label in [("r3m", "近3月報酬"), ("r6m", "近6月報酬"), ("r12m", "近12月報酬")]:
                        display[label] = display[col].map(lambda x: f"{x*100:+.1f}%")
                    display = display.rename(columns={"code": "代號", "rs_rating": "RS Rating"})
                    st.dataframe(
                        display[["代號", "名稱", "RS Rating", "近3月報酬", "近6月報酬", "近12月報酬"]],
                        use_container_width=True,
                        hide_index=True,
                    )

                    current_row = ranking[ranking["code"] == code]
                    if not current_row.empty:
                        st.caption(f"目前查詢的 {code} 在此排行裡的 RS Rating:{int(current_row.iloc[0]['rs_rating'])}")
                    elif not otc:
                        st.caption(f"{code} 不在目前的 .dsl 自選清單股票池裡,所以沒有算入這次排行。")
        except Exception as e:
            st.error(f"RS 排行計算失敗:{e}")
    st.markdown("</div>", unsafe_allow_html=True)

