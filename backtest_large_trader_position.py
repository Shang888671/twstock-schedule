"""回測台指期貨大額交易人未沖銷部位結構(large_trader_position.py)對加權指數後續
報酬有沒有預測力——跟`backtest_foreign_option_position.py`(已驗證net_ratio水位有
顯著預測力)同一套方法論,測的是**同一類但不同商品面向**的假說,不能假設這裡也一定
有效,必須分開驗證。

**測什麼**:大額交易人報表本身沒有像選擇權買賣權那樣的天然「多空」拆分,這裡從
`top5_buy`/`top5_sell`/`top10_buy`/`top10_sell`/`total_oi`(還有法人子集
`inst_top5_buy`等)組出幾個候選指標,分開測:

- `top5_net_ratio` = (top5_buy - top5_sell) / (top5_buy + top5_sell):前五大交易人
  的多空淨傾向(正=前五大整體偏多)——概念上對應`foreign_option_position.py`的
  `net_ratio`,但這裡的「前五大」不分身分別,是「不管是誰,只要是大額交易人」。
- `top10_net_ratio`:同上,換成前十大。
- `concentration_top10` = (top10_buy + top10_sell) / (2 * total_oi):前十大交易人
  佔全市場未沖銷量的比例(不分方向),量的是「籌碼集中度本身」,不是方向——這是
  `foreign_option_position.py`完全沒有的維度,是這份資料源特有的假說。
- `inst_net_ratio` = (inst_top5_buy - inst_top5_sell) / (inst_top5_buy + inst_top5_sell):
  特定法人子集自己的多空淨傾向。**這個指標2026-09-21用少量實測資料(9/15~9/21)
  觀察到`inst_top5_buy==top5_buy`且`inst_top5_sell==top5_sell`(台指期貨的前五大
  交易人100%都是特定法人),如果這個模式在完整回測期間都成立,這個指標會跟
  `top5_net_ratio`完全共線(算兩次一樣的東西),不是獨立訊號——`build_dataset()`
  執行時會印出跟`top5_net_ratio`的相關係數,相關係數接近1的話這裡的評估函式一樣
  會跑,但结果解讀上要當成同一件事的重複驗證,不是兩個獨立假說都測到。**

跟`backtest_foreign_option_position.py`一樣,四分位分組測水位(level)對未來
5/10/20日^TWII報酬有沒有預測力,`concentration_top10`跟`net_ratio`系列還額外測
0軸二分法(net_ratio系列)/歷史中位數二分法(concentration,天生沒有0這個天然
分界)。

**2026-09-21用真實資料(2024-01-02~2026-09-21,654個交易日)跑過上面這套測試**:
`top10_net_ratio`最強最一致(0軸二分法20日高組p=1.2e-6),`top5_net_ratio`較弱,
`inst_net_ratio`跟`top5_net_ratio`高度共線(不算獨立訊號),`concentration_top10`
證據最弱(不建議用)。**但這654天整體基準勝率63%~74%,跟`foreign_option_
position.py`當時測出的63~74%幾乎是同一組數字——因為根本是同一段強多頭期間、
同一個大盤,不能排除`top10_net_ratio`只是在追蹤這段多頭趨勢本身,不是真的獨立
訊號**。使用者2026-09-21選擇先做穩健性檢驗才決定要不要接進`signals.py`/`app.py`,
於是新增`evaluate_controlling_for_trend()`:先用^TWII自己的`trailing_return_20d`
(近20日走勢,代表「當時是不是已經在漲」)切三分位(低/中/高動能),**在每個動能
分位「內部」**(同一批已經走勢類似的日子裡)重新測`top10_net_ratio`0軸二分法還
顯不顯著——如果只是在追蹤大盤趨勢,net_ratio高低應該集中在「近期已經在漲」那個
分位裡,控制掉近期走勢之後net_ratio自己應該就測不出東西;如果net_ratio在每個
動能分位裡都還能獨立分出後續報酬,才是真的有超出趨勢本身的資訊量。用合成資料測過
兩種情境(net_ratio完全是趨勢的偽裝 vs net_ratio有獨立於趨勢的資訊)都能正確
分辨,**但還沒拿真實資料跑過**。

跑法(前提:`large_trader_position.py backfill`已經跑過,累積了
`branch_history/large_trader_position_history.csv`):
    python backtest_large_trader_position.py
"""

import sys

import pandas as pd
from scipy.stats import binomtest

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import fetch_data
from large_trader_position import get_history

FORWARD_DAYS = (5, 10, 20)
MIN_SAMPLES_FOR_TEST = 15


def _require_history() -> pd.DataFrame:
    df = get_history()
    if df.empty:
        raise SystemExit(
            "large_trader_position表/CSV快照是空的——要先跑過`python "
            "large_trader_position.py backfill <start>`累積歷史,這個回測才有東西可以測,"
            "不是這個腳本的bug。"
        )
    return df


def build_dataset() -> pd.DataFrame:
    lt = _require_history()
    print(f"[backtest] 大額交易人未沖銷部位:{len(lt):,} 個交易日"
          f"({lt['date'].min().date()} ~ {lt['date'].max().date()})")

    lt = lt.sort_values("date").reset_index(drop=True)
    # top5_net_ratio/top10_net_ratio/concentration_top10已經是get_history()回傳時
    # 就算好的衍生欄位(見large_trader_position._add_derived_columns()),這裡不重算
    # 一次,避免公式在兩個地方各存一份、之後改一邊忘了改另一邊。inst_net_ratio只有
    # 這個回測腳本用來檢查跟top5_net_ratio是不是共線,不是production會用到的欄位,
    # 才留在這裡自己算。
    lt["inst_net_ratio"] = (lt["inst_top5_buy"] - lt["inst_top5_sell"]) / (
        lt["inst_top5_buy"] + lt["inst_top5_sell"]
    )

    corr = lt[["top5_net_ratio", "inst_net_ratio"]].corr().iloc[0, 1]
    print(f"[backtest] top5_net_ratio跟inst_net_ratio的相關係數:{corr:.4f}"
          f"{'(接近1,見檔頭說明——這兩個很可能是同一件事)' if corr > 0.95 else ''}")

    twii = fetch_data.get_history(
        "^TWII",
        start=(lt["date"].min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        end=(lt["date"].max() + pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
    )
    # 跟backtest_foreign_option_position.py同一個坑:get_history()回傳tz-aware
    # (Asia/Taipei)index,SQLite parse_dates出來的是tz-naive,不轉的話對齊永遠比對
    # 失敗、悄悄變成0筆,不會報錯。
    twii.index = twii.index.tz_localize(None)
    twii = twii["Close"].dropna()
    print(f"[backtest] ^TWII:{len(twii):,} 個交易日({twii.index.min().date()} ~ {twii.index.max().date()})")

    signal_cols = ["top5_net_ratio", "top10_net_ratio", "concentration_top10", "inst_net_ratio"]
    TREND_WINDOW = 20
    rows = []
    for _, r in lt.iterrows():
        d = r["date"]
        if d not in twii.index:
            continue
        pos = twii.index.get_loc(d)
        row = {"date": d, **{c: r[c] for c in signal_cols}}
        # ^TWII自己近20日的走勢(當天收盤前),當「已經在漲/在跌」的動能控制變數用
        # (見檔頭evaluate_controlling_for_trend()說明),不是預測目標。
        row["trailing_return_20d"] = (
            twii.iloc[pos] / twii.iloc[pos - TREND_WINDOW] - 1 if pos >= TREND_WINDOW else None
        )
        for n in FORWARD_DAYS:
            if pos + n >= len(twii):
                row[f"fwd_return_{n}d"] = None
                continue
            row[f"fwd_return_{n}d"] = twii.iloc[pos + n] / twii.iloc[pos] - 1
        rows.append(row)

    df = pd.DataFrame(rows)
    print(f"[backtest] 對齊後共 {len(df):,} 筆(日期)快照")
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

        try:
            quartiles = pd.qcut(valid[signal_col], 4, labels=["Q1(最低)", "Q2", "Q3", "Q4(最高)"], duplicates="drop")
        except ValueError:
            print(f"  {window_label}: {signal_col}幾乎沒有變化(唯一值太少),沒辦法切分位,跳過")
            continue
        if quartiles.nunique() < 4:
            print(f"  {window_label}: {signal_col}實際切出來只有{quartiles.nunique()}個分位"
                  f"(可能大部分值一樣),以下結果要打折解讀")

        print(f"  {window_label}後報酬:整體基準 {overall_hits}/{overall_n}={overall_rate*100:.1f}%")
        for q in quartiles.cat.categories:
            group = valid[quartiles == q]
            n = len(group)
            if n < MIN_SAMPLES_FOR_TEST:
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


def evaluate_binary_split(df: pd.DataFrame, signal_col: str, split_value: float, split_label: str) -> None:
    print(f"\n{'='*20} 二分法:{signal_col}, {split_label}(分界={split_value:.4f}) {'='*20}")
    for n in FORWARD_DAYS:
        col = f"fwd_return_{n}d"
        valid = df.dropna(subset=[col, signal_col])
        overall_n = len(valid)
        overall_hits = int((valid[col] > 0).sum())
        overall_rate = overall_hits / overall_n if overall_n else 0

        mask = valid[signal_col] >= split_value
        for label, group in [("高(>=分界)", valid[mask]), ("低(<分界)", valid[~mask])]:
            gn = len(group)
            if gn < MIN_SAMPLES_FOR_TEST:
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


def evaluate_controlling_for_trend(df: pd.DataFrame, signal_col: str, label: str) -> None:
    """穩健性檢驗:先用^TWII自己的`trailing_return_20d`(近20日走勢)切三分位,
    在每個動能分位「內部」重新測`signal_col`的0軸二分法還顯不顯著——見檔頭說明。
    如果只是在追蹤大盤趨勢,控制掉近期走勢之後訊號應該就測不出東西了。"""
    print(f"\n{'='*20} 穩健性檢驗(控制近期趨勢):{label}({signal_col}) {'='*20}")
    base = df.dropna(subset=["trailing_return_20d", signal_col])
    try:
        tercile = pd.qcut(base["trailing_return_20d"], 3, labels=["低動能", "中動能", "高動能"], duplicates="drop")
    except ValueError:
        print("  trailing_return_20d幾乎沒有變化,沒辦法切三分位,跳過")
        return
    if tercile.nunique() < 3:
        print(f"  只切出{tercile.nunique()}個動能分位(可能大部分值一樣),以下結果要打折解讀")

    for t in tercile.cat.categories:
        bucket = base[tercile == t]
        if bucket.empty:
            continue
        trend_range = bucket["trailing_return_20d"]
        print(f"\n  --- {t}(trailing_return_20d範圍: {trend_range.min()*100:+.1f}% ~ "
              f"{trend_range.max()*100:+.1f}%, n={len(bucket)}) ---")
        for n in FORWARD_DAYS:
            col = f"fwd_return_{n}d"
            valid = bucket.dropna(subset=[col])
            if len(valid) < 40:
                print(f"    {n}日: 樣本太少({len(valid)}),跳過")
                continue
            # 基準用「這個動能分位自己的」勝率,不是全期間的——這才是真正控制掉
            # 趨勢之後的比較,不是`evaluate_binary_split()`那個全期間基準。
            bucket_n = len(valid)
            bucket_hits = int((valid[col] > 0).sum())
            bucket_rate = bucket_hits / bucket_n

            mask = valid[signal_col] >= 0.0
            for hl_label, group in [("高(>=0)", valid[mask]), ("低(<0)", valid[~mask])]:
                gn = len(group)
                if gn < MIN_SAMPLES_FOR_TEST:
                    print(f"    {n}日 [{hl_label}] n={gn} 太少,跳過")
                    continue
                hits = int((group[col] > 0).sum())
                rate = hits / gn
                avg_ret = group[col].mean()
                test_up = binomtest(hits, gn, bucket_rate, alternative="greater")
                test_down = binomtest(hits, gn, bucket_rate, alternative="less")
                if test_up.pvalue < 0.05:
                    sig = f"✓ 顯著優於本分位基準(p={test_up.pvalue:.4f})"
                elif test_down.pvalue < 0.05:
                    sig = f"✓ 顯著劣於本分位基準(p={test_down.pvalue:.4f})"
                else:
                    sig = "✗ 不顯著"
                print(f"    {n}日 [{hl_label}] n={gn}, 勝率={rate*100:.1f}%(本分位基準{bucket_rate*100:.1f}%), "
                      f"平均報酬={avg_ret*100:+.2f}% {sig}")


if __name__ == "__main__":
    dataset = build_dataset()
    out_path = "data_cache/large_trader_position_backtest_events.csv"
    dataset.to_csv(out_path, index=False)
    print(f"\n[backtest] 事件明細已存到 {out_path}")

    evaluate(dataset, "top5_net_ratio", "前五大交易人多空淨傾向")
    evaluate(dataset, "top10_net_ratio", "前十大交易人多空淨傾向")
    evaluate(dataset, "concentration_top10", "前十大交易人籌碼集中度")
    evaluate(dataset, "inst_net_ratio", "特定法人多空淨傾向")

    evaluate_binary_split(dataset, "top5_net_ratio", 0.0, "0軸")
    evaluate_binary_split(dataset, "top10_net_ratio", 0.0, "0軸")
    evaluate_binary_split(dataset, "concentration_top10", dataset["concentration_top10"].median(), "歷史中位數平分")

    evaluate_controlling_for_trend(dataset, "top10_net_ratio", "前十大交易人多空淨傾向")
    evaluate_controlling_for_trend(dataset, "top5_net_ratio", "前五大交易人多空淨傾向")
