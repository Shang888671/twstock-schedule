"""股市查詢模型 — Streamlit 網頁介面。

執行方式: streamlit run app.py
"""

import json
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as components

from fetch_data import get_quote, get_history, to_yf_symbol, get_chinese_name
from indicators import add_indicators
from dcf import run_dcf
from volume_profile import find_nearest_supports, find_nearest_resistances

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

with st.sidebar:
    st.markdown("### 📈 台股查詢模型")
    st.caption("即時報價・技術指標・做多訊號・DCF 估值")
    st.divider()
    code = st.text_input("股票代號", value="2330", help="輸入純數字代號,例如 2330")
    otc = st.checkbox("上櫃股票", value=False, help="預設為上市股票")
    period = st.selectbox("歷史資料區間", ["3mo", "6mo", "1y", "2y", "5y"], index=2)
    st.divider()
    st.caption("學習用途,所有數字僅供參考,不構成投資建議。")


@st.cache_data(ttl=300)
def load_quote(code, otc):
    return get_quote(code, otc=otc)


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

try:
    quote = load_quote(code, otc)
except Exception as e:
    st.error(f"抓取報價失敗:{e}")
    st.stop()

name = load_name(code, otc)

st.markdown(
    f"""<div class="app-header">
        <h1>📈 台股查詢模型 <span class="badge-gold">TW MARKET</span></h1>
        <div class="sub">即時報價・技術指標・做多訊號・DCF 估值 — 學習用途,非投資建議</div>
    </div>""",
    unsafe_allow_html=True,
)

# --- 報價卡片 ---
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
volume_str = f"{quote['volume']:,}" if quote["volume"] else "—"

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

try:
    df = load_history_with_indicators(code, period, otc)
except Exception as e:
    st.error(f"抓取歷史資料失敗:{e}")
    st.stop()

tab_chart, tab_ai, tab_scan, tab_backtest, tab_dcf = st.tabs(
    ["📊 技術分析", "🎯 做多訊號", "🔎 分點掃描", "🧪 回測", "💰 DCF 估值"]
)

# --- 技術分析 ---
with tab_chart:
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

# --- 做多訊號 ---
with tab_ai:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🎯 法人 + 權證做多訊號(自訂規則)</div>', unsafe_allow_html=True)
    st.caption(
        "規則:三大法人近 10 天合計買賣超為正的天數佔比 ≥ 70%,且個股認購權證近 1 天內有單一檔權證成交金額 ≥ 50 萬元,"
        "兩者同時成立才判定為做多訊號。若有提供 XQ 匯出的分點資料,再加上第三個條件:"
        "已知隔日沖大戶分點(凱基-城中/永豐金-市政/富邦-台南/凱基-岡山/凱基-三重/凱基-高雄)有出現在買方名單且買超為正,"
        "沒有命中的話就退回看「買超第1名分點金額 ≥ 500 萬元」。純粹規則比對,不是模型。"
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
                    f"認購權證近{sig['warrant_window_days']}天≥50萬檔數",
                    f"{sig['warrant_large_trade_count']} 筆",
                    delta="達標 ✓" if sig["warrant_signal"] else "未達 1 筆",
                    delta_color="off",
                )
                sc2.caption(f"單一檔權證當天成交金額 ≥ 50 萬才算 1 筆,資料日期 {sig['warrant_asof']}")

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
        "5 個獨立條件各自顯示目前有沒有達成,不要求同時成立(跟上面的做多訊號不同,這裡只是計分卡,"
        "進場判斷交給你自己看):① 股價剛同時站上6/40/56EMA(今天剛突破,不是已經站上一段時間)"
        "② 三大法人近2天剛轉為買超(前一天還是賣超)③ 認購權證近1天單筆≥50萬檔數超過4筆"
        "④ 成交量超過前3日均量 ⑤ RS(近20天報酬率-加權指數同期報酬率)在0軸之上。"
    )

    light_scope = st.radio(
        "檢查範圍", ["只檢查目前查詢的這檔股票", "檢查整個 .dsl 股票池(有個股期貨的標的)"], horizontal=True, key="light_scope"
    )
    light_min_count = None
    if light_scope == "檢查整個 .dsl 股票池(有個股期貨的標的)":
        light_min_count = st.slider("只列出至少達成幾項條件的股票", min_value=1, max_value=5, value=3)
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
                    st.success(f"5 個條件中達成 {light_result['passed_count']}/{light_result['total']} 個")

                    light_cols = st.columns(5)
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

# --- 回測 ---
with tab_backtest:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🧪 回測「法人+權證」做多訊號</div>', unsafe_allow_html=True)
    st.caption(
        "回測「🎯 做多訊號」分頁裡規則的前兩個條件(三大法人近10天70%正買超、認購權證近1天≥50萬檔數≥1筆),"
        "驗證訊號出現後隔日的實際勝率/平均報酬,並跟「不篩訊號、全部交易日」的基準線比較,看訊號有沒有真的多出邊際。"
        "**只回測前兩個條件**——第三個條件(已知大戶分點)沒有歷史資料可回測,那份資料是手動匯出的當下快照,"
        "TWSE 官方分點系統有 CAPTCHA 擋自動查詢,無法回溯歷史。**結果沒有計入手續費/證交稅/滑價**,實際淨報酬會更低。"
    )

    bt_scope = st.radio("回測範圍", ["只回測目前查詢的這檔股票", "回測整個 .dsl 股票池"], horizontal=True)
    if bt_scope == "回測整個 .dsl 股票池":
        st.caption(
            "不管股票池有幾檔股票,抓法人/權證資料的請求數只跟天數成正比(逐日抓一次全市場資料,"
            "一次分給股票池裡所有股票),所以回測整個股票池不會比回測一檔股票慢很多。"
        )

    bt_col1, bt_col2 = st.columns(2)
    bt_start = bt_col1.date_input("開始日期", value=date.today() - timedelta(days=180))
    bt_end = bt_col2.date_input("結束日期", value=date.today())

    if st.button("🧪 開始回測"):
        if bt_start >= bt_end:
            st.error("開始日期必須早於結束日期。")
        else:
            try:
                from backtest import backtest_universe, summarize_backtest

                if bt_scope == "回測整個 .dsl 股票池":
                    from xq_watchlist import get_stock_futures_codes_from_watchlists

                    codes = sorted(get_stock_futures_codes_from_watchlists())
                    if not codes:
                        st.warning("找不到 .dsl 自選股清單,請確認 xq_branch_data/ 資料夾裡有匯出的 .dsl 檔案。")
                else:
                    codes = [code]

                if codes:
                    progress = st.progress(0.0, text="回測中...")

                    def _on_bt_progress(stage, done, total):
                        progress.progress(done / total if total else 1.0, text=f"回測中...【{stage}】{done}/{total}")

                    signal_days, all_days = backtest_universe(
                        codes, bt_start.isoformat(), bt_end.isoformat(), progress_callback=_on_bt_progress
                    )
                    progress.empty()

                    if all_days.empty:
                        st.info("這個區間/範圍沒有抓到任何可用的交易日資料。")
                    else:
                        summary = summarize_backtest(signal_days, all_days)
                        sc, bc, soc = summary["signal_close"], summary["baseline_close"], summary["signal_open_close"]

                        if sc["count"] == 0:
                            st.info(f"這段區間/範圍內訊號從未觸發過(基準樣本共 {bc['count']} 個交易日),沒有東西可以算勝率。")
                        else:
                            st.success(f"訊號共觸發 {sc['count']} 次(基準樣本共 {bc['count']} 個交易日)")

                            m1, m2, m3, m4 = st.columns(4)
                            m1.metric(
                                "訊號隔日勝率(收盤買)",
                                f"{sc['win_rate']*100:.0f}%",
                                delta=f"基準線 {bc['win_rate']*100:.0f}%",
                                delta_color="off",
                            )
                            m2.metric(
                                "訊號隔日平均報酬(收盤買)",
                                f"{sc['avg_return']*100:+.2f}%",
                                delta=f"基準線 {bc['avg_return']*100:+.2f}%",
                                delta_color="off",
                            )
                            m3.metric(
                                "訊號隔日勝率(早盤買)",
                                f"{soc['win_rate']*100:.0f}%" if soc["win_rate"] is not None else "N/A",
                            )
                            m4.metric(
                                "訊號隔日平均報酬(早盤買)",
                                f"{soc['avg_return']*100:+.2f}%" if soc["avg_return"] is not None else "N/A",
                            )
                            st.caption(
                                "「早盤買」是隔日開盤買、隔日收盤賣,對應原始理論(大戶隔日早盤拉高出貨);"
                                "「收盤買」是訊號當天收盤買、隔日收盤賣,是比較標準的隔日報酬率定義。"
                            )

                            display = signal_days.copy()
                            if bt_scope == "回測整個 .dsl 股票池":
                                from stock_futures import get_stock_futures_name

                                display["名稱"] = display["code"].map(lambda c: get_stock_futures_name(c) or "")
                            display["date"] = display["date"].dt.strftime("%Y-%m-%d")
                            display["institutional_ratio"] = display["institutional_ratio"].map(lambda x: f"{x*100:.0f}%")
                            display["forward_return_close"] = display["forward_return_close"].map(lambda x: f"{x*100:+.2f}%")
                            display["forward_return_open_close"] = display["forward_return_open_close"].map(lambda x: f"{x*100:+.2f}%")
                            cols = (
                                ["code"]
                                + (["名稱"] if "名稱" in display.columns else [])
                                + ["date", "institutional_ratio", "warrant_count", "forward_return_close", "forward_return_open_close"]
                            )
                            st.dataframe(
                                display[cols].rename(
                                    columns={
                                        "code": "代號",
                                        "date": "訊號日期",
                                        "institutional_ratio": "法人正買超比例",
                                        "warrant_count": "權證大額筆數",
                                        "forward_return_close": "隔日報酬(收盤買)",
                                        "forward_return_open_close": "隔日報酬(早盤買)",
                                    }
                                ),
                                use_container_width=True,
                                hide_index=True,
                            )
            except Exception as e:
                st.error(f"回測失敗:{e}")

    st.markdown(
        '<div class="disclaimer">回測結果基於歷史資料,不保證未來表現,也沒有計入交易成本,僅供學習參考,不構成投資建議。</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

# --- DCF 估值 ---
with tab_dcf:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">DCF 現金流折現估值</div>', unsafe_allow_html=True)

    d1, d2, d3 = st.columns(3)
    growth_rate = d1.slider("未來成長率假設", 0.0, 0.30, 0.08, 0.01)
    terminal_growth = d2.slider("永續成長率假設", 0.0, 0.05, 0.025, 0.005)
    discount_rate = d3.slider("折現率(WACC)假設", terminal_growth + 0.01, 0.25, 0.10, 0.01)

    try:
        dcf_result = run_dcf(
            code, otc=otc, growth_rate=growth_rate, terminal_growth=terminal_growth, discount_rate=discount_rate
        )
        upside = dcf_result["upside_pct"]
        c1, c2, c3 = st.columns(3)
        c1.metric("每股內在價值(估算)", f"{dcf_result['intrinsic_value_per_share']:,.2f}")
        c2.metric("目前股價", f"{dcf_result['current_price']:,.2f}")
        c3.metric(
            "估值差距",
            f"{upside:.1f}%" if upside is not None else "N/A",
            delta=f"{upside:.1f}%" if upside is not None else None,
            delta_color="inverse",
        )
    except Exception as e:
        st.error(f"DCF 計算失敗:{e}")

    st.markdown(
        '<div class="disclaimer">DCF 結果對成長率/折現率假設極度敏感,僅供學習參考,不構成投資建議。</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)
