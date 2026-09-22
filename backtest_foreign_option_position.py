"""回測外資臺指選擇權合成多空部位(foreign_option_position.py)對加權指數後續報酬
有沒有預測力。

2026-09-21:使用者截圖看到WantGoo玩股網的「外資選擇權合成部位」圖表,要求做一個新
分頁並加上判斷分析。這個資料源(期交所官方「三大法人-選擇權買賣權分計」)跟這個repo
其他法人/籌碼資料源不同,查詢工具本身就支援約最近3年的日期範圍回溯(不用像
institutional_flow/margin_balance那樣每天累積才有樣本),所以在畫圖、寫任何「判斷」
文案之前,先用這3年(2024-01~2026-09,658個交易日)的完整歷史測一次有沒有用——延續
這個repo這一輪的做法(分點訊號、🚦燈號都是先回測才敢寫進UI文案)。

**測什麼**:這個資料源直接對應的就是加權指數(臺指選擇權=TXO=加權指數的衍生品),
不是「個股 vs 大盤」的超額報酬架構,這裡測的是「^TWII本身的後續報酬」。

- net_ratio(多空淨額佔合成多空總量的比例,= net_lots / (long_lots+short_lots),
  範圍約-1~1):把口數正規化,避免2024跟2026整體未平倉量本來就不一樣大,直接比較
  net_lots原始水位會混進這個規模成長的雜訊。用四分位分組測「淨額比例」的水位
  (level)對未來5/10/20日^TWII報酬有沒有預測力。
- net_ratio_change(前一交易日到當天net_ratio的變化量):測的是「法人立場轉變的
  速度」而不是靜態水位,四分位分組同樣測法。

跑法:
    python backtest_foreign_option_position.py
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import binomtest

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import fetch_data
from foreign_option_position import get_history

FORWARD_DAYS = (5, 10, 20)


def build_dataset() -> pd.DataFrame:
    opt = get_history()
    print(f"[backtest] 外資選擇權合成部位:{len(opt):,} 個交易日"
          f"({opt['date'].min().date()} ~ {opt['date'].max().date()})")

    opt = opt.sort_values("date").reset_index(drop=True)
    opt["net_ratio"] = opt["net_lots"] / (opt["synthetic_long_lots"] + opt["synthetic_short_lots"])
    opt["net_ratio_change"] = opt["net_ratio"].diff()

    twii = fetch_data.get_history(
        "^TWII",
        start=(opt["date"].min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        end=(opt["date"].max() + pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
    )
    # get_history()回傳的index是tz-aware(Asia/Taipei),跟SQLite parse_dates出來的
    # tz-naive Timestamp直接比較/對齊一律對不上(不會報錯,只是`d in twii.index`永遠
    # False,0筆對齊悄悄發生,不容易第一時間發現)——先轉tz-naive。
    twii.index = twii.index.tz_localize(None)
    twii = twii["Close"].dropna()
    print(f"[backtest] ^TWII:{len(twii):,} 個交易日({twii.index.min().date()} ~ {twii.index.max().date()})")

    rows = []
    for _, r in opt.iterrows():
        d = r["date"]
        if d not in twii.index:
            continue
        pos = twii.index.get_loc(d)
        row = {
            "date": d,
            "net_ratio": r["net_ratio"],
            "net_ratio_change": r["net_ratio_change"],
        }
        for n in FORWARD_DAYS:
            if pos + n >= len(twii):
                row[f"fwd_return_{n}d"] = None
                continue
            row[f"fwd_return_{n}d"] = twii.iloc[pos + n] / twii.iloc[pos] - 1
        rows.append(row)

    df = pd.DataFrame(rows)
    print(f"[backtest] 對齊後共 {len(df):,} 筆(股票,日期)快照")
    return df


def evaluate(df: pd.DataFrame, signal_col: str, label: str) -> None:
    print(f"\n{'='*20} 訊號:{label}({signal_col}) {'='*20}")
    return_cols = [f"fwd_return_{n}d" for n in FORWARD_DAYS]

    for col in return_cols:
        window_label = col.replace("fwd_return_", "").replace("d", "日")
        valid = df.dropna(subset=[col, signal_col])
        if len(valid) < 40:
            print(f"  {window_label}: 樣本太少({len(valid)}),跳過")
            continue

        overall_n = len(valid)
        overall_hits = int((valid[col] > 0).sum())
        overall_rate = overall_hits / overall_n

        quartiles = pd.qcut(valid[signal_col], 4, labels=["Q1(最低)", "Q2", "Q3", "Q4(最高)"])
        print(f"  {window_label}後報酬:整體基準 {overall_hits}/{overall_n}={overall_rate*100:.1f}%")
        for q in ["Q1(最低)", "Q2", "Q3", "Q4(最高)"]:
            group = valid[quartiles == q]
            n = len(group)
            if n < 15:
                print(f"    [{q}] n={n} 太少,跳過")
                continue
            hits = int((group[col] > 0).sum())
            rate = hits / n
            avg_ret = group[col].mean()
            test_up = binomtest(hits, n, overall_rate, alternative="greater")
            test_down = binomtest(hits, n, overall_rate, alternative="less")
            if test_up.pvalue < 0.05:
                sig = f"✓ 顯著優於整體(p={test_up.pvalue:.4f})"
            elif test_down.pvalue < 0.05:
                sig = f"✓ 顯著劣於整體(p={test_down.pvalue:.4f})"
            else:
                sig = "✗ 不顯著"
            print(f"    [{q}] n={n}, 勝率={rate*100:.1f}%, 平均報酬={avg_ret*100:+.2f}% {sig}")


def evaluate_binary_split(df: pd.DataFrame, split_value: float, split_label: str) -> None:
    """測二分法(多/空各一半左右,拿0軸跟歷史中位數比較哪個當分界線更好)——
    foreign_option_position.classify_current_reading()最終採用的分界線就是照這裡的
    結果決定的(0軸勝出,見2026-09-21的commit),這個函式留著讓這個判斷可以重現/
    往後有更多資料時重新驗證,不是量完就丟。"""
    print(f"\n{'='*20} 二分法:{split_label}(分界={split_value:.4f}) {'='*20}")
    for n in FORWARD_DAYS:
        col = f"fwd_return_{n}d"
        valid = df.dropna(subset=[col, "net_ratio"])
        overall_n = len(valid)
        overall_hits = int((valid[col] > 0).sum())
        overall_rate = overall_hits / overall_n if overall_n else 0

        mask = valid["net_ratio"] >= split_value
        for label, group in [("多(>=分界)", valid[mask]), ("空(<分界)", valid[~mask])]:
            gn = len(group)
            if gn < 15:
                print(f"  {n}日 [{label}] n={gn} 太少,跳過")
                continue
            hits = int((group[col] > 0).sum())
            rate = hits / gn
            avg_ret = group[col].mean()
            test_up = binomtest(hits, gn, overall_rate, alternative="greater")
            test_down = binomtest(hits, gn, overall_rate, alternative="less")
            if test_up.pvalue < 0.05:
                sig = f"✓ 顯著優於整體(p={test_up.pvalue:.2e})"
            elif test_down.pvalue < 0.05:
                sig = f"✓ 顯著劣於整體(p={test_down.pvalue:.2e})"
            else:
                sig = "✗ 不顯著"
            print(f"  {n}日 [{label}] n={gn}, 勝率={rate*100:.1f}%(整體{overall_rate*100:.1f}%), "
                  f"平均報酬={avg_ret*100:+.2f}% {sig}")


if __name__ == "__main__":
    dataset = build_dataset()
    out_path = "data_cache/foreign_option_position_backtest_events.csv"
    dataset.to_csv(out_path, index=False)
    print(f"\n[backtest] 事件明細已存到 {out_path}")

    evaluate(dataset, "net_ratio", "多空淨額比例(水位)")
    evaluate(dataset, "net_ratio_change", "多空淨額比例變化量(立場轉變速度)")

    evaluate_binary_split(dataset, 0.0, "0軸")
    evaluate_binary_split(dataset, dataset["net_ratio"].median(), "歷史中位數平分")
