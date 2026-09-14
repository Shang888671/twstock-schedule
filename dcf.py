"""DCF(現金流折現)估值模型。

用 yfinance 自動抓取財報數據(自由現金流、負債、現金、股數),
搭配使用者指定(或預設)的成長率與折現率,估算每股內在價值。

方法:兩階段成長模型
1. 未來 N 年:用 growth_rate 讓最近一年自由現金流逐年成長
2. 第 N 年之後:用 terminal_growth 算永續價值(Gordon Growth)
3. 把每一年現金流與終值折現回現在,加總得企業價值(EV)
4. 股權價值 = EV - 總負債 + 現金
5. 每股內在價值 = 股權價值 / 流通股數
"""

import yfinance as yf


def get_latest_fcf(ticker: yf.Ticker) -> float:
    cf = ticker.cashflow
    if "Free Cash Flow" not in cf.index:
        raise ValueError("財報資料中找不到 Free Cash Flow,可能是資料不足")
    # 欄位由新到舊排序,取最新一年且非 NaN 的值
    series = cf.loc["Free Cash Flow"].dropna()
    if series.empty:
        raise ValueError("Free Cash Flow 全部是 NaN,資料不足以估值")
    return float(series.iloc[0])


def get_balance_sheet_items(ticker: yf.Ticker) -> dict:
    bs = ticker.balance_sheet
    total_debt = float(bs.loc["Total Debt"].dropna().iloc[0]) if "Total Debt" in bs.index else 0.0
    cash = (
        float(bs.loc["Cash And Cash Equivalents"].dropna().iloc[0])
        if "Cash And Cash Equivalents" in bs.index
        else 0.0
    )
    return {"total_debt": total_debt, "cash": cash}


def run_dcf(
    code: str,
    otc: bool = False,
    growth_rate: float = 0.08,
    terminal_growth: float = 0.025,
    discount_rate: float = 0.10,
    projection_years: int = 5,
) -> dict:
    """
    growth_rate: 未來 N 年自由現金流年成長率假設(預設 8%)
    terminal_growth: 永續成長率(預設 2.5%,通常不應超過長期 GDP 成長率)
    discount_rate: 折現率/WACC(預設 10%)
    projection_years: 明確預測年數(預設 5 年)
    """
    if discount_rate <= terminal_growth:
        raise ValueError("折現率必須大於永續成長率,否則終值會發散")

    symbol = f"{code}.TWO" if otc else f"{code}.TW"
    ticker = yf.Ticker(symbol)

    base_fcf = get_latest_fcf(ticker)
    bs_items = get_balance_sheet_items(ticker)
    shares = ticker.fast_info.get("shares")
    current_price = ticker.fast_info.get("lastPrice")

    # 1. 逐年預測 FCF 並折現
    pv_fcfs = []
    fcf = base_fcf
    for year in range(1, projection_years + 1):
        fcf = fcf * (1 + growth_rate)
        pv = fcf / (1 + discount_rate) ** year
        pv_fcfs.append(pv)

    # 2. 終值(第 projection_years 年之後,以最後一年 FCF 為基礎)
    terminal_value = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal_value = terminal_value / (1 + discount_rate) ** projection_years

    enterprise_value = sum(pv_fcfs) + pv_terminal_value
    equity_value = enterprise_value - bs_items["total_debt"] + bs_items["cash"]
    intrinsic_value_per_share = equity_value / shares if shares else None

    return {
        "symbol": symbol,
        "base_fcf": base_fcf,
        "assumptions": {
            "growth_rate": growth_rate,
            "terminal_growth": terminal_growth,
            "discount_rate": discount_rate,
            "projection_years": projection_years,
        },
        "total_debt": bs_items["total_debt"],
        "cash": bs_items["cash"],
        "shares_outstanding": shares,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "intrinsic_value_per_share": intrinsic_value_per_share,
        "current_price": current_price,
        "upside_pct": (
            (intrinsic_value_per_share / current_price - 1) * 100
            if intrinsic_value_per_share and current_price
            else None
        ),
    }


if __name__ == "__main__":
    result = run_dcf("2330")
    print("=== DCF 估值結果:2330 台積電 ===")
    for k, v in result.items():
        print(f"{k}: {v}")
    print(
        "\n注意:DCF 結果對 growth_rate / discount_rate 這兩個假設極度敏感,"
        "數字調一點,估值就差很多——這是學習/參考用途,不是投資建議。"
    )
