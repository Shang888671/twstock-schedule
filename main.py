"""股市查詢模型 — 統一命令列介面。

整合報價查詢、技術指標兩個模組。

用法範例:
    python main.py 2330                  # 台積電,完整跑一次
    python main.py 6488 --otc            # 上櫃股票
"""

import argparse

from fetch_data import get_quote, get_history
from indicators import add_indicators


def parse_args():
    parser = argparse.ArgumentParser(description="台股查詢模型:報價 / 技術指標")
    parser.add_argument("code", help="股票代號,例如 2330")
    parser.add_argument("--otc", action="store_true", help="上櫃股票(預設為上市)")
    parser.add_argument("--period", default="1y", help="歷史資料/技術指標區間,預設 1y")
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


if __name__ == "__main__":
    main()
