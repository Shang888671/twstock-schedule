"""風險儀表板 — Streamlit UI + 風險指標計算。

用法（在 app.py 的 tab 裡）:
    from risk_dashboard import render_risk_dashboard
    with tab_risk:
        render_risk_dashboard()
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

from risk import RiskManager


# ───── 風險指標計算函式（純計算，可匯出給其他模組用）─────


def compute_max_drawdown(pnl_series: pd.Series) -> float:
    """從每日 PnL 序列計算 Max Drawdown（0~1 正數）。

    pnl_series: index=date, values=每日損益（可正可負）。
    """
    if pnl_series.empty:
        return 0.0
    cumulative = pnl_series.cumsum()
    running_max = cumulative.cummax()
    drawdowns = (cumulative - running_max) / running_max.replace(0, float("nan"))
    return float(drawdowns.min()) if not drawdowns.dropna().empty else 0.0


def compute_sharpe_ratio(pnl_series: pd.Series, total_capital: float, periods_per_year: int = 252) -> float:
    """從每日 PnL 計算年化 Sharpe Ratio（無風險利率 = 0）。

    pnl_series: index=date, values=每日損益。
    total_capital: 計算日報酬率的分母（總資金）。
    """
    if pnl_series.empty or len(pnl_series) < 2 or total_capital <= 0:
        return 0.0
    daily_returns = pnl_series / total_capital
    std = daily_returns.std()
    if std == 0 or math.isnan(std):
        return 0.0
    return float(daily_returns.mean() / std * math.sqrt(periods_per_year))


def compute_concentration(positions: list[dict], top_n: int = 5) -> dict:
    """計算部位集中度。

    positions: RiskManager.get_portfolio_summary()["positions"]
    回傳 {"top_n_value": float, "total_value": float, "pct": float, "top_n": list}
    """
    if not positions:
        return {"top_n_value": 0.0, "total_value": 0.0, "pct": 0.0, "top_n": []}
    sorted_pos = sorted(positions, key=lambda p: p["value"], reverse=True)
    total_value = sum(p["value"] for p in sorted_pos)
    top_n = sorted_pos[:top_n]
    top_n_value = sum(p["value"] for p in top_n)
    pct = top_n_value / total_value if total_value > 0 else 0.0
    return {"top_n_value": top_n_value, "total_value": total_value, "pct": pct, "top_n": top_n}


# ───── Streamlit 渲染 ─────


def _render_metric_card(label: str, value: str, delta: str | None = None, delta_color: str = "normal") -> None:
    """單一 metric 卡片。"""
    kwargs = {"label": label, "value": value}
    if delta is not None:
        kwargs["delta"] = delta
        kwargs["delta_color"] = delta_color
    st.metric(**kwargs)


def _style_pnl(val) -> str:
    """PnL 值上色：紅漲綠跌。"""
    try:
        num = float(val)
    except (ValueError, TypeError):
        return ""
    if num > 0:
        return "color: #ef4444; font-weight:600;"
    if num < 0:
        return "color: #22c55e; font-weight:600;"
    return ""


def _style_pct(val) -> str:
    """百分比上色。"""
    try:
        s = str(val).rstrip("%")
        num = float(s)
    except (ValueError, TypeError):
        return ""
    if num > 0:
        return "color: #ef4444;"
    if num < 0:
        return "color: #22c55e;"
    return ""


def render_risk_dashboard(total_capital: float | None = None) -> None:
    """渲染完整風險儀表板。"""
    from risk import get_risk_config, set_risk_config

    db_config = get_risk_config()

    # ── 資金水位設定 ──
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 💰 風控資金設定")
    default_capital = total_capital or db_config.get("total_capital", 1_000_000)
    capital = st.sidebar.number_input(
        "總資金（元）",
        min_value=100_000,
        max_value=100_000_000,
        value=int(default_capital),
        step=100_000,
        key="risk_capital_input",
    )
    # 持久化到 DB
    if capital != db_config.get("total_capital"):
        set_risk_config(total_capital=capital)

    rm = RiskManager(total_capital=capital)

    # ── 資料載入 ──
    try:
        summary = rm.get_portfolio_summary()
    except Exception as e:
        st.error(f"讀取持倉資料失敗: {e}")
        return

    positions = summary["positions"]
    total_value = summary["total_position_value"]
    cash = summary["cash"]
    cash_pct = cash / summary["total_capital"] * 100 if summary["total_capital"] > 0 else 0.0
    position_pct = total_value / summary["total_capital"] * 100 if summary["total_capital"] > 0 else 0.0

    # ── 每日 PnL（風險指標用） ──
    from risk import DB_PATH
    import sqlite3

    try:
        conn = sqlite3.connect(DB_PATH)
        pnl_df = pd.read_sql_query(
            "SELECT date, pnl FROM daily_pnl ORDER BY date", conn, parse_dates=["date"], index_col="date"
        )
        trades_df = pd.read_sql_query(
            "SELECT code, action, shares, price, pnl, traded_at FROM trades ORDER BY traded_at DESC LIMIT 50",
            conn,
        )
        conn.close()
    except Exception:
        pnl_df = pd.DataFrame(columns=["date", "pnl"])
        trades_df = pd.DataFrame()

    # ── 頂部 metrics ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📉 風險儀表板 — 即時部位監控</div>', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        today_pnl = float(pnl_df.iloc[-1]["pnl"]) if not pnl_df.empty else 0.0
        delta_color = "inverse"  # 賺錢=紅(正值), 但 metric delta 紅色通常表虧損，用 inverse 讓正值顯示綠色... 改用 normal+自訂
        _render_metric_card(
            "今日 PnL",
            f"{today_pnl:+,.0f}",
            delta=f"{today_pnl / capital * 100:+.2f}%",
            delta_color="normal",
        )
    with col2:
        _render_metric_card(
            "總部位",
            f"{total_value:,.0f}",
            delta=f"{position_pct:.1f}%",
            delta_color="off",
        )
    with col3:
        _render_metric_card(
            "現金",
            f"{cash:,.0f}",
            delta=f"{cash_pct:.1f}%",
            delta_color="off",
        )
    with col4:
        num_positions = len(positions)
        openable, reason = rm.can_open_new_position()
        if openable is True:
            _render_metric_card(
                "開倉狀態",
                f"✅ 可開倉 ({num_positions} 檔)",
                delta="部位上限內",
                delta_color="off",
            )
        else:
            _render_metric_card(
                "開倉狀態",
                f"⛔ {num_positions} 檔",
                delta=reason[:30],
                delta_color="off",
            )

    st.markdown("</div>", unsafe_allow_html=True)

    # ── 持倉明細 + 停損停利 ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📋 目前持倉 & 停損停利</div>', unsafe_allow_html=True)

    if not positions:
        st.info("目前没有任何持倉。請透過 RiskManager.add_position() 手動建倉（或等待自動交易模組建倉）。")
    else:
        pos_df = pd.DataFrame(positions)
        pos_df = pos_df.rename(columns={
            "code": "代號",
            "shares": "張數",
            "avg_price": "均價",
            "current_price": "現價",
            "value": "市值",
            "pnl": "損益",
            "stop_loss": "停損",
            "take_profit": "停利",
        })
        pos_df = pos_df[["代號", "張數", "均價", "現價", "市值", "損益", "停損", "停利"]]
        # 張數顯示（1張=1000股）
        pos_df["張數"] = pos_df["張數"].apply(lambda x: f"{x // 1000}")

        styled_pos = pos_df.style.map(_style_pnl, subset=["損益"])
        st.dataframe(styled_pos, use_container_width=True, hide_index=True)

        # ── 部位集中度 bar chart ──
        if len(positions) > 1:
            st.markdown('<div class="section-title">📊 部位集中度</div>', unsafe_allow_html=True)
            chart_data = pd.DataFrame([
                {"代號": p["code"], "市值": p["value"]} for p in positions
            ]).set_index("代號").sort_values("市值", ascending=False)
            st.bar_chart(chart_data, use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── 風險指標 ──
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🛡️ 風險指標</div>', unsafe_allow_html=True)

    col_dd, col_sharpe, col_conc = st.columns(3)

    with col_dd:
        max_dd = compute_max_drawdown(pnl_df["pnl"]) if not pnl_df.empty else 0.0
        st.metric("Max Drawdown", f"{max_dd * 100:.2f}%")

    with col_sharpe:
        sharpe = compute_sharpe_ratio(pnl_df["pnl"], capital) if not pnl_df.empty else 0.0
        st.metric("Sharpe Ratio (年化)", f"{sharpe:.2f}")

    with col_conc:
        conc = compute_concentration(positions)
        st.metric(
            f"Top 5 集中度",
            f"{conc['pct'] * 100:.1f}%",
            delta=f"{conc['top_n_value']:,.0f} / {conc['total_value']:,.0f}",
            delta_color="off",
        )

    # 集中度明細
    if conc["top_n"]:
        conc_rows = []
        for p in conc["top_n"]:
            pct_single = p["value"] / conc["total_value"] * 100 if conc["total_value"] > 0 else 0
            conc_rows.append({
                "代號": p["code"],
                "市值": f"{p['value']:,.0f}",
                "佔比": f"{pct_single:.1f}%",
            })
        conc_df = pd.DataFrame(conc_rows)
        st.dataframe(conc_df, use_container_width=True, hide_index=True, width=400)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── 每日 PnL 走勢 ──
    if not pnl_df.empty:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">📈 每日損益走勢</div>', unsafe_allow_html=True)
        cumulative = pnl_df["pnl"].cumsum()
        cumulative_df = pd.DataFrame({"累積損益": cumulative}, index=pnl_df.index)
        st.line_chart(cumulative_df, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # ── 近期交易紀錄 ──
    if not trades_df.empty:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">📜 近期交易紀錄（最近 50 筆）</div>', unsafe_allow_html=True)
        display_trades = trades_df.rename(columns={
            "code": "代號",
            "action": "動作",
            "shares": "張數",
            "price": "價格",
            "pnl": "損益",
            "traded_at": "時間",
        })
        display_trades["張數"] = display_trades["張數"].apply(lambda x: f"{x // 1000}")
        styled_trades = display_trades.style.map(_style_pnl, subset=["損益"])
        st.dataframe(styled_trades, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)


# ───── CLI 快速測試 ─────

if __name__ == "__main__":
    # 用 streamlit run 跑這個檔會直接渲染儀表板
    import sys
    print("請用 `streamlit run risk_dashboard.py` 啟動，或在 app.py 的 tab 中導入 render_risk_dashboard()")
