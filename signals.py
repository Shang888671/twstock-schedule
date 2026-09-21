"""使用者自訂的「三大法人 + 個股權證」隔日做多訊號規則。

規則(使用者提供的短線交易經驗法則):
- 三大法人買賣超近 10 個交易日,「合計買賣超為正」的天數佔比 >= 70%,視為法人籌碼偏多。
- 個股認購權證近 1 個交易日內,單一檔權證「一次」成交金額 >= 50 萬元且當天收紅的檔數 >= 1 筆,
  視為主力大買認購權證——隔日沖大戶隔天早盤可能拉高股價出貨(賣權證),提供短線做多的價差空間。
  (這裡的「一次」是以「單一權證代號當天的成交總金額」為單位,不是真正逐筆成交明細——
  TWSE 公開資料只到「每檔權證、每天」的加總,沒有到「每一筆成交」的等級,所以沒辦法直接判斷
  這筆大額成交金額是買方主導還是賣方主導,大額成交也可能是主力倒貨造成的。這裡用「當天股價
  有沒有收紅」當粗略代理——只算收紅的大額成交,過濾掉收黑(可能是倒貨)的大額成交,不是真正
  的買賣方向判定,只是在沒有逐筆成交+報價資料的前提下,退而求其次的做法。)
- （選用,第三個條件)個股「認購權證」買方分點裡,有沒有「對這檔股票本身已驗證有效」的分點
  同時在加碼——分點名單來自 `branch_win_rate.get_significant_broker_names()`(用歷史買超後
  20日超額報酬勝率驗證過,不是主觀認定的清單),只要其中任何一個分點出現在買方 TOP15 名單裡
  且買超金額為正,就算達標(見 `xq_branch.match_significant_branches()`)。不加總全部分點的
  買超金額——重點是「哪個已驗證分點在加碼」,不是金額規模。
  這份資料 TWSE 官網有 CAPTCHA 擋自動查詢,沒辦法自動抓,所以改成使用者自己用 XQ 全球贏家
  手動匯出 CSV、放進 `xq_branch_data/` 資料夾,這個條件才會生效——
  **沒有提供檔案時,直接略過這個條件,只看前兩個條件**(不會因為缺資料就判定失敗)。
- 有提供分點檔案時,三個條件同時成立才判定為「做多訊號」;沒有提供時,前兩個條件同時成立就判定。

這是使用者自己歸納的短線交易經驗法則,純粹是規則比對(if/else 門檻判斷),
不是統計或機器學習模型。
"""

import pandas as pd

from chip_data import get_institutional_flow, get_stock_call_warrant_detail
from xq_branch import read_call_warrant_broker_ranking, match_significant_branches

INSTITUTIONAL_WINDOW_DAYS = 10
INSTITUTIONAL_POSITIVE_RATIO_THRESHOLD = 0.7
WARRANT_WINDOW_DAYS = 1
WARRANT_SINGLE_TRADE_THRESHOLD = 500_000
WARRANT_LARGE_TRADE_MIN_COUNT = 1

# 「條件達成燈號」用的門檻(見 evaluate_light_signals)——這是另一組使用者自訂規則,
# 跟上面 evaluate_long_signal() 的門檻是分開的兩套規則,不要共用/混淆。
LIGHT_MA_COLUMNS = ["EMA6", "EMA40", "EMA56"]
LIGHT_INSTITUTIONAL_LOOKBACK_DAYS = 15
WARRANT_STRONG_TRADE_MIN_COUNT = 4  # 要求「超過」這個筆數,即 >4(至少5筆)
LIGHT_VOLUME_AVG_WINDOW = 3
MARGIN_LOOKBACK_DAYS = 3  # 融資融券只能逐日累積本地快取(見 margin_data.py),這裡只需要「今日 vs 前一筆」
REVENUE_YOY_THRESHOLD = 0.0

# 權證燈號的門檻分級——只用來在單股查詢時「額外顯示」各門檻各自的筆數,方便自己判斷買盤
# 強度有多猛;燈號本身「達成/未達成」仍然只看 WARRANT_SINGLE_TRADE_THRESHOLD(50萬)這一級的
# 筆數是否超過 WARRANT_STRONG_TRADE_MIN_COUNT,不會因為加了這些門檻而改變燈號判斷邏輯。
WARRANT_TIER_THRESHOLDS = [500_000, 1_000_000, 1_500_000, 2_000_000]


def _recent_call_warrant_detail(code: str, window_days: int, max_lookback: int = 15) -> pd.DataFrame:
    """往前找最近 window_days 個「有資料的交易日」,把每天的個別權證明細接在一起。"""
    frames = []
    day = pd.Timestamp.today().normalize()
    tries = 0
    while len(frames) < window_days and tries < max_lookback:
        detail = get_stock_call_warrant_detail(code, day.strftime("%Y%m%d"))
        if detail is not None:
            detail = detail.copy()
            detail["date"] = day.strftime("%Y-%m-%d")
            frames.append(detail)
        day -= pd.Timedelta(days=1)
        tries += 1
    if not frames:
        return pd.DataFrame(columns=["warrant_code", "warrant_name", "volume", "value", "price_up", "date"])
    return pd.concat(frames, ignore_index=True)


def evaluate_long_signal(code: str, otc: bool = False) -> dict:
    """檢查使用者自訂的做多訊號規則,回傳兩個子條件的細節與最終判定。

    只支援上市股票(三大法人資料來源 TWSE T86 不支援上櫃)。
    """
    if otc:
        raise ValueError("三大法人籌碼資料目前只支援上市股票(TWSE),尚未支援上櫃(TPEx)")

    end_date = pd.Timestamp.today().strftime("%Y-%m-%d")

    flow_start = (pd.Timestamp(end_date) - pd.Timedelta(days=INSTITUTIONAL_WINDOW_DAYS * 2 + 5)).strftime("%Y-%m-%d")
    flow = get_institutional_flow(code, flow_start, end_date)
    flow_recent = flow.tail(INSTITUTIONAL_WINDOW_DAYS)
    institutional_days = len(flow_recent)
    positive_days = int((flow_recent["total_net"] > 0).sum()) if institutional_days else 0
    institutional_ratio = (positive_days / institutional_days) if institutional_days else 0.0
    institutional_signal = institutional_ratio >= INSTITUTIONAL_POSITIVE_RATIO_THRESHOLD

    warrant_detail = _recent_call_warrant_detail(code, WARRANT_WINDOW_DAYS)
    large_trades = (
        warrant_detail[(warrant_detail["value"] >= WARRANT_SINGLE_TRADE_THRESHOLD) & (warrant_detail["price_up"] == True)]
        if not warrant_detail.empty
        else warrant_detail
    )
    warrant_large_trade_count = int(len(large_trades))
    warrant_signal = warrant_large_trade_count >= WARRANT_LARGE_TRADE_MIN_COUNT

    branch = read_call_warrant_broker_ranking(code)
    branch_available = branch is not None
    significant_hits = match_significant_branches(code, ranking=branch) if branch_available else []
    significant_buying = [h for h in significant_hits if h["net_buy_wan"] > 0]
    branch_signal = bool(significant_buying) if branch_available else None

    conditions = [institutional_signal, warrant_signal]
    if branch_available:
        conditions.append(branch_signal)

    result = {
        "code": code,
        "institutional_window_days": institutional_days,
        "institutional_positive_days": positive_days,
        "institutional_positive_ratio": institutional_ratio,
        "institutional_signal": institutional_signal,
        "institutional_asof": flow_recent.index[-1].strftime("%Y-%m-%d") if institutional_days else None,
        "warrant_window_days": WARRANT_WINDOW_DAYS,
        "warrant_large_trade_count": warrant_large_trade_count,
        "warrant_signal": warrant_signal,
        "warrant_asof": warrant_detail["date"].max() if not warrant_detail.empty else None,
        "branch_available": branch_available,
        "long_signal": all(conditions),
    }
    if branch_available:
        result.update(
            {
                "branch_top1_broker": branch["top1_broker"],
                "branch_top1_net_buy_wan": branch["top1_net_buy_wan"],
                "branch_significant_hits": significant_hits,
                "branch_signal": branch_signal,
                "branch_asof": branch["date_end"],
                "branch_file": branch["file"],
            }
        )
    return result


def _ma_breakout_signal(price_df: pd.DataFrame) -> dict:
    """今天收盤同時站上 EMA6/EMA40/EMA56,且昨天不是同時站上——抓「剛突破」那一天。"""
    if len(price_df) < 2 or any(m not in price_df.columns for m in LIGHT_MA_COLUMNS):
        return {"passed": False, "detail": "資料不足"}
    today, yesterday = price_df.iloc[-1], price_df.iloc[-2]
    today_above = all(today["Close"] > today[m] for m in LIGHT_MA_COLUMNS)
    yesterday_above = all(yesterday["Close"] > yesterday[m] for m in LIGHT_MA_COLUMNS)
    passed = today_above and not yesterday_above
    if passed:
        detail = "今天剛同時站上三條均線"
    elif today_above:
        detail = "已站上三條均線一段時間(不是剛突破)"
    else:
        detail = "目前未同時站上三條均線"
    return {"passed": passed, "detail": detail}


def _institutional_turn_from_series(total_net: pd.Series) -> dict:
    """核心判斷邏輯,吃「已經抓好的 total_net 序列」——單股查詢跟批次掃描共用,
    避免掃描一整個股票池時每一檔股票各自重抓一次(見 scan_light_signals)。"""
    last3 = total_net.tail(3)
    if len(last3) < 3:
        return {"passed": False, "detail": "資料不足"}
    day0, day1, day2 = last3.iloc[-1], last3.iloc[-2], last3.iloc[-3]
    passed = day0 > 0 and day1 > 0 and day2 < 0
    return {
        "passed": passed,
        "detail": f"近3天買賣超(張):{day2/1000:+,.0f} → {day1/1000:+,.0f} → {day0/1000:+,.0f}",
    }


def _institutional_turn_signal(code: str) -> dict:
    """近2個交易日三大法人合計買賣超轉正,且再往前一天是負——抓「剛轉為買超」的轉折點。"""
    end_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    start_date = (pd.Timestamp(end_date) - pd.Timedelta(days=LIGHT_INSTITUTIONAL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    flow = get_institutional_flow(code, start_date, end_date)
    return _institutional_turn_from_series(flow["total_net"] if not flow.empty else pd.Series(dtype=float))


def _warrant_strong_from_count(count: int) -> dict:
    """核心判斷邏輯,吃「已經算好的當天大額且收紅筆數」——單股查詢跟批次掃描共用。"""
    passed = count > WARRANT_STRONG_TRADE_MIN_COUNT
    return {"passed": passed, "detail": f"近{WARRANT_WINDOW_DAYS}天單筆≥50萬且收紅檔數:{count}"}


def _warrant_strong_signal(code: str) -> dict:
    """近1個交易日,單一檔認購權證成交金額 >=50萬且當天收紅的檔數 > 4(比 evaluate_long_signal 的門檻更嚴格)。

    只算收紅的大額成交,理由見 `evaluate_long_signal()` 模組docstring的說明——大額成交金額
    不代表是買方主導,用「當天有沒有收紅」當粗略代理過濾掉可能是倒貨造成的大額成交。

    detail 額外附上 WARRANT_TIER_THRESHOLDS 每一級門檻各自的筆數(50萬/100萬/150萬/200萬,
    同樣只算收紅的),純粹是給單股查詢時參考買盤強度分布用——只有 50萬這一級的筆數會決定 passed。
    """
    warrant_detail = _recent_call_warrant_detail(code, WARRANT_WINDOW_DAYS)
    large_trades = (
        warrant_detail[(warrant_detail["value"] >= WARRANT_SINGLE_TRADE_THRESHOLD) & (warrant_detail["price_up"] == True)]
        if not warrant_detail.empty
        else warrant_detail
    )
    result = _warrant_strong_from_count(int(len(large_trades)))

    tier_counts = (
        [int(((warrant_detail["value"] >= t) & (warrant_detail["price_up"] == True)).sum()) for t in WARRANT_TIER_THRESHOLDS]
        if not warrant_detail.empty
        else [0] * len(WARRANT_TIER_THRESHOLDS)
    )
    tier_detail = " / ".join(
        f"{t // 10000}萬↑{c}筆" for t, c in zip(WARRANT_TIER_THRESHOLDS, tier_counts)
    )
    result["detail"] = f"{result['detail']}({tier_detail})"
    return result


def _volume_surge_signal(price_df: pd.DataFrame) -> dict:
    """今天成交量 > 前3個交易日(不含今天)平均成交量。"""
    if len(price_df) < LIGHT_VOLUME_AVG_WINDOW + 1 or "Volume" not in price_df.columns:
        return {"passed": False, "detail": "資料不足"}
    today_volume = price_df["Volume"].iloc[-1]
    avg_volume = price_df["Volume"].iloc[-1 - LIGHT_VOLUME_AVG_WINDOW : -1].mean()
    passed = today_volume > avg_volume
    return {
        "passed": passed,
        "detail": f"今日量 {today_volume/1000:,.0f} 張 vs 前{LIGHT_VOLUME_AVG_WINDOW}日均量 {avg_volume/1000:,.0f} 張",
    }


def _rs_above_zero_from_value(rs: dict | None) -> dict:
    """核心判斷邏輯,吃「已經算好的 compute_rs_vs_benchmark() 結果」——單股查詢跟批次掃描共用。"""
    if rs is None:
        return {"passed": False, "detail": "資料不足"}
    passed = rs["rs_value"] > 0
    return {"passed": passed, "detail": f"近{rs['window_days']}天相對大盤:{rs['rs_value']*100:+.1f}%"}


def _rs_above_zero_signal(code: str, otc: bool) -> dict:
    """個股近20個交易日累計報酬率 - 加權指數(^TWII)同期報酬率,是否為正。"""
    from relative_strength import compute_rs_vs_benchmark

    return _rs_above_zero_from_value(compute_rs_vs_benchmark(code, otc=otc))


def _margin_health_from_series(margin_df) -> dict:
    """核心判斷邏輯,吃「已經抓好的融資融券 DataFrame」——單股查詢跟批次掃描共用。

    今日融資餘額 < 前一筆融資餘額 = 散戶籌碼轉健康,這是主觀認定,不是統計驗證過的規則
    (跟其他幾個條件的免責聲明一致)。這個資料源沒有官方歷史API,只能靠 margin_data.py
    逐日累積本地快取,樣本不足(少於2筆)時顯示「資料不足」,不是bug——第一次查某檔股票
    一定會是這個狀態,要連續用過幾天才會開始有數據。
    """
    if margin_df is None or len(margin_df) < 2:
        return {"passed": False, "detail": "資料不足(融資融券只能逐日累積,需連續使用幾天才有數據)"}
    prev, today = margin_df["margin_balance"].iloc[-2], margin_df["margin_balance"].iloc[-1]
    passed = today < prev
    return {"passed": passed, "detail": f"融資餘額(張):{prev:,.0f} → {today:,.0f}"}


def _margin_health_signal(code: str, otc: bool) -> dict:
    """只支援上市股票——這個資料源沒有上櫃股票的資料(見 margin_data.py 模組docstring)。"""
    if otc:
        return {"passed": False, "detail": "上櫃股票不支援融資融券資料"}
    from margin_data import get_margin_trading

    return _margin_health_from_series(get_margin_trading(code, lookback_days=MARGIN_LOOKBACK_DAYS))


def _revenue_momentum_from_value(revenue: dict | None) -> dict:
    """核心判斷邏輯,吃「已經算好的 fundamentals.get_monthly_revenue() 結果」——單股查詢跟批次掃描共用。"""
    if revenue is None or revenue.get("yoy_pct") is None:
        return {"passed": False, "detail": "資料不足"}
    passed = revenue["yoy_pct"] > REVENUE_YOY_THRESHOLD
    period = revenue["period"]
    period_label = f"{int(period[:3]) + 1911}/{period[3:]}" if len(period) == 5 else period
    return {"passed": passed, "detail": f"{period_label}月營收年增率:{revenue['yoy_pct']:+.1f}%"}


def _revenue_momentum_signal(code: str, otc: bool) -> dict:
    """最新一個月的營收年增率是否為正——中長期基本面動能,跟其他幾個技術/籌碼面條件是互補角度。"""
    from fundamentals import get_monthly_revenue

    return _revenue_momentum_from_value(get_monthly_revenue(code, otc=otc))


def _assemble_light_conditions(
    ma_result: dict, institutional_result: dict, warrant_result: dict, volume_result: dict,
    rs_result: dict, margin_result: dict, revenue_result: dict,
) -> dict:
    """把7個子結果組成 evaluate_light_signals()/scan_light_signals() 共用的回傳格式。"""
    conditions = {
        "ma_breakout": {"label": "股價剛站上6/40/56EMA", **ma_result},
        "institutional_turn": {"label": "法人剛連續轉買超", **institutional_result},
        "warrant_strong": {"label": "認購權證收紅大額>4筆", **warrant_result},
        "volume_surge": {"label": "成交量增3日均量以上", **volume_result},
        "rs_above_zero": {"label": "RS強於大盤(0軸之上)", **rs_result},
        "margin_health": {"label": "融資餘額減少(籌碼轉健康)", **margin_result},
        "revenue_momentum": {"label": "月營收年增率為正", **revenue_result},
    }
    passed_count = sum(1 for c in conditions.values() if c["passed"])
    return {"conditions": conditions, "passed_count": passed_count, "total": len(conditions)}


def evaluate_light_signals(code: str, price_df: pd.DataFrame, otc: bool = False) -> dict:
    """使用者自訂的「條件達成燈號」——7個獨立條件,各自顯示達成與否,不做 AND 判斷。

    跟 `evaluate_long_signal()` 的差異:那個函式算出單一 `long_signal` 布林值(全部條件
    同時成立才算做多訊號);這個函式只回報「7個裡面達成幾個」,進場判斷交給使用者自己看,
    不強制規定「全部達成才算數」。原本是5個技術/籌碼面條件,2026-09-16 加了「融資餘額減少」
    (籌碼面,跟三大法人是互補角度)跟「月營收年增率為正」(第一個基本面條件)兩個。

    price_df:呼叫端已經算好技術指標的價格資料(含 EMA6/EMA40/EMA56/Volume),直接複用
    `app.py` 技術分析分頁的那份,不在這裡重抓——跟 `evaluate_long_signal()` 自己抓資料不同。

    三大法人相關條件在 otc=True 時直接標記未達成(附原因),不像 `evaluate_long_signal()`
    對上櫃股票直接丟例外整組失敗——這裡希望其他條件在上櫃股票上還是能正常顯示。

    單一股票用這個函式;要一次掃整個股票池(例如 .dsl 自選清單)用下面的 `scan_light_signals()`
    ——那個函式用「逐日批次抓取」策略,不是對每一檔股票各自呼叫這個函式。
    """
    if otc:
        institutional_result = {"passed": False, "detail": "上櫃股票不支援三大法人資料"}
    else:
        institutional_result = _institutional_turn_signal(code)

    return _assemble_light_conditions(
        _ma_breakout_signal(price_df),
        institutional_result,
        _warrant_strong_signal(code),
        _volume_surge_signal(price_df),
        _rs_above_zero_signal(code, otc),
        _margin_health_signal(code, otc),
        _revenue_momentum_signal(code, otc),
    )


def scan_light_signals(codes, otc_map: dict | None = None, sleep: float = 0.3, progress_callback=None) -> list:
    """對一批股票(例如 .dsl 個股期貨自選清單)一次算出「條件達成燈號」,回傳依達成數由高到低排序的 list。

    法人、權證資料源本來就回傳「單一交易日、全市場」的完整資料(見 `chip_data.py` 的
    `*_multi` 函式說明),所以這裡用「逐日批次抓取」策略:整個股票池的
    法人資料、權證資料各自只抓一輪,不是對每一檔股票各自打一次 API。大盤指數(RS 條件用)
    也只抓一次、所有股票共用。個股股價本身沒有這種全市場快照 API,還是得逐檔抓
    (跟 RS 排行分頁的既有做法一致)。

    回傳的 list,每筆格式跟 `evaluate_light_signals()` 的回傳值相同,外加 "code" 欄位。
    """
    from chip_data import get_institutional_flow_multi, get_warrant_large_trade_counts_multi
    from relative_strength import compute_rs_vs_benchmark, fetch_benchmark_history
    from fetch_data import get_history
    from indicators import add_indicators
    from margin_data import get_margin_trading_multi

    codes = list(dict.fromkeys(codes))
    otc_map = otc_map or {}

    end_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    institutional_start = (pd.Timestamp(end_date) - pd.Timedelta(days=LIGHT_INSTITUTIONAL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    warrant_start = (pd.Timestamp(end_date) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")

    institutional_multi = get_institutional_flow_multi(codes, institutional_start, end_date, sleep=sleep)
    warrant_multi = get_warrant_large_trade_counts_multi(codes, warrant_start, end_date, WARRANT_SINGLE_TRADE_THRESHOLD, sleep=sleep)
    benchmark_hist = fetch_benchmark_history()
    # 融資融券這個資料源沒有官方歷史API,只能拿「今天」一筆快照(見 margin_data.py),
    # 只對上市股票抓——跟三大法人資料同樣不支援上櫃。
    listed_codes = [c for c in codes if not otc_map.get(c, False)]
    margin_multi = get_margin_trading_multi(listed_codes) if listed_codes else {}

    results = []
    for i, code in enumerate(codes):
        otc = otc_map.get(code, False)
        try:
            price_df = add_indicators(get_history(code, otc=otc, period="6mo"))
        except Exception:
            price_df = pd.DataFrame()

        if otc:
            institutional_result = {"passed": False, "detail": "上櫃股票不支援三大法人資料"}
        else:
            inst_df = institutional_multi.get(code)
            institutional_result = _institutional_turn_from_series(
                inst_df["total_net"] if inst_df is not None and not inst_df.empty else pd.Series(dtype=float)
            )

        warrant_df = warrant_multi.get(code)
        warrant_count = int(warrant_df["count"].iloc[-1]) if warrant_df is not None and not warrant_df.empty else 0

        ma_result = _ma_breakout_signal(price_df) if not price_df.empty else {"passed": False, "detail": "資料不足"}
        volume_result = _volume_surge_signal(price_df) if not price_df.empty else {"passed": False, "detail": "資料不足"}
        rs_result = compute_rs_vs_benchmark(code, otc=otc, benchmark_hist=benchmark_hist) if not price_df.empty else None

        if otc:
            margin_result = {"passed": False, "detail": "上櫃股票不支援融資融券資料"}
        else:
            margin_result = _margin_health_from_series(margin_multi.get(code))
        revenue_result = _revenue_momentum_signal(code, otc)

        light = _assemble_light_conditions(
            ma_result,
            institutional_result,
            _warrant_strong_from_count(warrant_count),
            volume_result,
            _rs_above_zero_from_value(rs_result),
            margin_result,
            revenue_result,
        )
        light["code"] = code
        results.append(light)

        if progress_callback:
            progress_callback(i + 1, len(codes))

    results.sort(key=lambda r: r["passed_count"], reverse=True)
    return results


if __name__ == "__main__":
    print("=== 2330(沒有分點檔案,應該只用前兩個條件判斷)===")
    for k, v in evaluate_long_signal("2330").items():
        print(k, ":", v)

    print("\n=== 6770(有分點檔案,應該納入第三個條件)===")
    for k, v in evaluate_long_signal("6770").items():
        print(k, ":", v)

    print("\n=== 條件達成燈號(2330)===")
    from fetch_data import get_history
    from indicators import add_indicators

    price_df = add_indicators(get_history("2330", period="6mo"))
    light_result = evaluate_light_signals("2330", price_df)
    for key, c in light_result["conditions"].items():
        print(f"{key}: {'✅' if c['passed'] else '❌'} {c['label']} — {c['detail']}")
    print(f"達成 {light_result['passed_count']}/{light_result['total']}")
