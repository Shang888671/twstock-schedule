"""整合篩選——把「RS相對強弱排行」(`relative_strength.compute_rs_ranking`)、
「條件達成燈號」批次掃描(`signals.scan_light_signals`)、外資選擇權/大額交易人的
大盤濾網(`foreign_option_position`/`large_trader_position` 的 `classify_current_reading`)
合併成一張表,不用在三、四個分頁之間手動對照。

**這裡刻意不新增任何判斷邏輯或門檻**——這三個資料源各自都已經是這個 repo 裡驗證過、
上線中的既有功能,這個模組只做「合併顯示」,不改變任何既有函式的計算結果,也不發明新的
統計假設。股票池沿用既有的 `.dsl` 個股期貨自選清單(跟 🚦條件達成燈號、💪RS排行兩個分頁
用同一份池子),不是另外拉一份。

`get_market_tone()` 回傳的兩個大盤濾網是全市場層級的單一讀數(不分股票),只當「進場環境」
的旁邊提示用,不會拿來過濾/排序下面逐股的表格——理由是它們的回測都還有「回測期間剛好是
多頭期間」的但書(見兩個模組各自的 `classify_current_reading()` docstring),沒有驗證過
拿來當逐股篩選條件會不會有效,不能無憑無據地當成篩選規則。
"""


def get_market_tone() -> dict:
    """把外資選擇權(TXO)、大額交易人(TX)兩個大盤濾網的最新分類包成一個 dict。
    任一個資料源還沒有累積資料(或讀取失敗)時,對應的值是 None,呼叫端要自己處理。"""
    tones = {}
    try:
        from foreign_option_position import get_history as get_opt_history, classify_current_reading as classify_opt

        tones["外資選擇權"] = classify_opt(get_opt_history())
    except Exception:
        tones["外資選擇權"] = None
    try:
        from large_trader_position import get_history as get_lt_history, classify_current_reading as classify_lt

        tones["大額交易人"] = classify_lt(get_lt_history())
    except Exception:
        tones["大額交易人"] = None
    return tones


def run_combined_screen(progress_callback=None) -> dict:
    """對 `.dsl` 個股期貨自選股池,合併算出 RS Rating 跟條件達成燈號,用代號合併成一張表,
    依 RS Rating 由高到低排序(沿用 `compute_rs_ranking()` 本身的排序,這裡不重新排)。

    某些股票資料不足(例如新股上市不到12個月)算不出 RS Rating,`compute_rs_ranking()`
    本身就會把它排除在結果外——這裡的合併表沿用這個既有行為,不特別處理,跟 💪RS排行
    分頁看到的股票池是同一批。

    progress_callback(stage, done, total) 可選,stage 是 "rs" 或 "light"——RS排行跟
    燈號掃描是分開的兩輪批次(各自的資料源/瓶頸不同),先跑完 RS 才跑燈號,不是同一個迴圈,
    所以進度要分階段回報,UI 才能各自顯示各自的進度條。
    """
    from relative_strength import compute_rs_ranking
    from signals import scan_light_signals
    from stock_futures import get_stock_futures_name
    from xq_watchlist import get_stock_futures_codes_from_watchlists

    universe = sorted(get_stock_futures_codes_from_watchlists())
    if not universe:
        return {"mode": "no_watchlist"}

    def _rs_progress(done, total):
        if progress_callback:
            progress_callback("rs", done, total)

    def _light_progress(done, total):
        if progress_callback:
            progress_callback("light", done, total)

    ranking = compute_rs_ranking(universe, progress_callback=_rs_progress)
    light_results = scan_light_signals(universe, progress_callback=_light_progress)
    light_by_code = {r["code"]: r for r in light_results}

    rows = []
    for _, r in ranking.iterrows():
        code = r["code"]
        light = light_by_code.get(code)
        rows.append({
            "code": code,
            "name": get_stock_futures_name(code) or "",
            "rs_rating": int(r["rs_rating"]),
            "r3m": float(r["r3m"]),
            "r6m": float(r["r6m"]),
            "r12m": float(r["r12m"]),
            "passed_count": light["passed_count"] if light else None,
            "total_conditions": light["total"] if light else None,
            "conditions": light["conditions"] if light else {},
        })

    return {
        "mode": "result",
        "rows": rows,
        "universe_size": len(universe),
        "market_tone": get_market_tone(),
    }
