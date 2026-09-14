"""股市查詢模型 — 統一命令列介面。

整合報價查詢、技術指標、DCF 估值三個模組。

用法範例:
    python main.py 2330                  # 台積電,完整跑一次
    python main.py 6488 --otc            # 上櫃股票
    python main.py 2330 --dcf-growth 0.05 --dcf-discount 0.12
"""

import argparse

from fetch_data import get_quote, get_history
from indicators import add_indicators
from dcf import run_dcf


def parse_args():
    parser = argparse.ArgumentParser(description="台股查詢模型:報價 / 技術指標 / 走勢預測 / DCF 估值")
    parser.add_argument("code", help="股票代號,例如 2330")
    parser.add_argument("--otc", action="store_true", help="上櫃股票(預設為上市)")
    parser.add_argument("--period", default="1y", help="歷史資料/技術指標區間,預設 1y")
    parser.add_argument("--dcf-growth", type=float, default=0.08, help="DCF 未來成長率假設,預設 0.08")
    parser.add_argument("--dcf-terminal", type=float, default=0.025, help="DCF 永續成長率假設,預設 0.025")
    parser.add_argument("--dcf-discount", type=float, default=0.10, help="DCF 折現率(WACC)假設,預設 0.10")
    return parser.parse_args()


def print_section(title: str):
    print(f"\n{'=' * 10} {title} {'=' * 10}")


def main():
    args = parse_args()
    code = args.code

    print_section("即時(延遲)報價")
    quote = get_quote(code, otc=args.otc)
    for k, v in quote.items():
        print(f"{k}: {v}")

    print_section(f"技術指標(區間 {args.period},最近 5 筆)")
    hist = get_history(code, period=args.period, otc=args.otc)
    result = add_indicators(hist)
    cols = ["Close", "EMA6", "EMA40", "EMA56", "RSI14", "MACD", "MACD_signal", "BB_upper", "BB_lower"]
    print(result[cols].tail())

    print_section("DCF 現金流折現估值")
    dcf_result = run_dcf(
        code,
        otc=args.otc,
        growth_rate=args.dcf_growth,
        terminal_growth=args.dcf_terminal,
        discount_rate=args.dcf_discount,
    )
    for k, v in dcf_result.items():
        print(f"{k}: {v}")
    print("注意:DCF 結果對成長率/折現率假設極度敏感,僅供學習參考,不構成投資建議。")


if __name__ == "__main__":
    main()
