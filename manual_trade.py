"""手動建倉 / 平倉 CLI 工具。

用法：
  python manual_trade.py add 2330 1000 2450 --stop-loss 2400 --take-profit 2600
  python manual_trade.py close 2330 2500
  python manual_trade.py list
"""

import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])

from risk import RiskManager


def main():
    rm = RiskManager(total_capital=1_000_000)

    if len(sys.argv) < 2:
        print(__doc__)
        return

    action = sys.argv[1]

    if action == "add":
        # add <code> <shares> <avg_price> [--stop-loss X] [--take-profit Y]
        code = sys.argv[2]
        shares = int(sys.argv[3])
        avg_price = float(sys.argv[4])
        stop_loss = None
        take_profit = None
        i = 5
        while i < len(sys.argv):
            if sys.argv[i] == "--stop-loss":
                stop_loss = float(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "--take-profit":
                take_profit = float(sys.argv[i + 1])
                i += 2
            else:
                i += 1
        rm.add_position(code, shares, avg_price, stop_loss, take_profit)
        print(f"✅ 建倉 {code} {shares}股 @ {avg_price}, 停損 {stop_loss}, 停利 {take_profit}")

    elif action == "close":
        # close <code> <exit_price>
        code = sys.argv[2]
        exit_price = float(sys.argv[3])
        pnl = rm.close_position(code, exit_price)
        print(f"✅ 平倉 {code} @ {exit_price}, 損益 {pnl:+,.0f}")

    elif action == "list":
        summary = rm.get_portfolio_summary()
        if not summary["positions"]:
            print("目前無持倉")
        for p in summary["positions"]:
            print(f"{p['code']} {p['shares']}股 均價 {p['avg_price']} 現價 {p['current_price']} 損益 {p['pnl']:+,.0f}")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
