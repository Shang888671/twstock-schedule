"""價格籌碼分佈(Volume Profile)——找出離目前收盤價最近的支撐/阻力價位。

概念:把一段期間的股價範圍切成很多價格區間(bin),把每天的成交量依當天 Low~High
涵蓋哪些區間、按重疊比例分攤進去,還原出「成交量在各個價位的分佈」。分佈裡量能特別
密集的價位代表過去有大量換手發生過,通常比較容易形成支撐/阻力(在那個價位持有部位的人多,
股價跌回/漲回那裡容易有人加碼防守或獲利了結)——密集區在目前收盤價之下當支撐、之上當阻力,
同一份分佈、同一套選點邏輯,差別只在篩選方向。

daily OHLCV 沒有逐筆成交明細,這裡的「分佈」是用 Low~High 區間近似還原,不是真正的
逐筆委託成交價格統計。

**距離上限**:近3個月的高低價區間裡,量能密集區不一定剛好落在現價附近——如果這段期間走勢
偏單邊(例如一路上漲),密集區可能還停留在區間低點,離現價很遠,當成「最近支撐」參考
價值不大。所以只挑離現價 `VOLUME_PROFILE_MAX_DISTANCE_PCT` 以內的候選,超過這個範圍寧可
少列一個位置,也不列太遠、對短線沒意義的價位(使用者確認用 ±15%,短線交易用途)。

**選點邏輯(前N高量能+最小間距)**:一開始用「嚴格區域高峰」(某bin量能要同時大於左右
兩側幾個bin)來找支撐/阻力,但實際測試發現有些股票近期的籌碼分佈比較像單一平滑的山丘
(例如一路趨勢盤,量能從低點連續堆到現價附近的最高點再遞減),這種形狀天然就沒有3個各自
獨立分開的區域高峰,嚴格判斷法常常湊不滿3個位置。改成:在距離上限內的候選bin依量能由高到低
排序,依序挑選,只要跟已經選到的位置間距 >= `VOLUME_PROFILE_MIN_SPACING_PCT` 就選進來
(避免同一坨籌碼裡兩個相鄰bin都被選中,變成重複列同一個位置),直到湊滿 `num_levels` 個或
候選用完為止——這樣只要籌碼分佈裡有夠分散的量能區,通常都能湊滿3個;真的整段都是同一坨
籌碼、完全分不開的極端情況,才會少於3個。
"""

import numpy as np
import pandas as pd

VOLUME_PROFILE_LOOKBACK_DAYS = 60  # 近3個月交易日——使用者做短線交易,確認用這個區間
VOLUME_PROFILE_BINS = 50
VOLUME_PROFILE_MAX_DISTANCE_PCT = 0.15  # 離現價超過這個比例就不列入,使用者確認短線用±15%
VOLUME_PROFILE_MIN_SPACING_PCT = 0.02  # 挑選出的位置彼此至少要距離現價這個比例,避免同一坨籌碼被拆成兩個位置


def _build_volume_histogram(price_df: pd.DataFrame, num_bins: int) -> tuple[np.ndarray, np.ndarray]:
    """回傳 (bin中心價格陣列, 各bin累積成交量陣列)。"""
    price_min = float(price_df["Low"].min())
    price_max = float(price_df["High"].max())
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_width = (price_max - price_min) / num_bins
    volume_per_bin = np.zeros(num_bins)

    for low, high, vol in zip(price_df["Low"], price_df["High"], price_df["Volume"]):
        if pd.isna(low) or pd.isna(high) or pd.isna(vol) or high <= low or vol <= 0:
            continue
        start_idx = max(0, min(num_bins - 1, int((low - price_min) // bin_width)))
        end_idx = max(0, min(num_bins - 1, int((high - price_min) // bin_width)))
        for i in range(start_idx, end_idx + 1):
            bin_lo, bin_hi = bin_edges[i], bin_edges[i + 1]
            overlap = min(high, bin_hi) - max(low, bin_lo)
            if overlap > 0:
                volume_per_bin[i] += vol * (overlap / (high - low))

    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    return bin_centers, volume_per_bin


def _find_nearest_peaks(price_df: pd.DataFrame, num_levels: int, above: bool) -> list[dict]:
    """核心邏輯,支撐(above=False)跟阻力(above=True)共用同一套選點邏輯,只差篩選方向。

    見模組 docstring 的「選點邏輯」說明:在距離上限內的候選bin依量能由高到低貪婪挑選,
    彼此間距不足 `VOLUME_PROFILE_MIN_SPACING_PCT` 就跳過,直到湊滿 num_levels 或候選用完。
    """
    window = price_df.tail(VOLUME_PROFILE_LOOKBACK_DAYS).dropna(subset=["Low", "High", "Volume", "Close"])
    if len(window) < 5:
        return []

    close = float(window["Close"].iloc[-1])
    bin_centers, volume_per_bin = _build_volume_histogram(window, VOLUME_PROFILE_BINS)

    max_distance = close * VOLUME_PROFILE_MAX_DISTANCE_PCT
    min_spacing = close * VOLUME_PROFILE_MIN_SPACING_PCT

    if above:
        in_range = [(p, v) for p, v in zip(bin_centers, volume_per_bin) if close < p <= close + max_distance and v > 0]
    else:
        in_range = [(p, v) for p, v in zip(bin_centers, volume_per_bin) if close - max_distance <= p < close and v > 0]

    in_range.sort(key=lambda pv: pv[1], reverse=True)  # 量能由高到低貪婪挑選

    picked: list[dict] = []
    for price, vol in in_range:
        if len(picked) == num_levels:
            break
        if all(abs(price - p["price"]) >= min_spacing for p in picked):
            picked.append({"price": float(price), "volume": float(vol)})

    picked.sort(key=lambda p: p["price"], reverse=not above)  # 依離收盤價由近到遠排序
    return picked


def find_nearest_supports(price_df: pd.DataFrame, num_supports: int = 3) -> list[dict]:
    """算出離目前收盤價最近的 num_supports 個「籌碼密集區」支撐價位(收盤價之下的區域高峰)。

    price_df 需要 Low/High/Volume/Close 欄位,用呼叫端已經算好指標的 df 直接複用即可,
    不用重抓資料。回傳依離收盤價由近到遠排序的 [{"price": float, "volume": float}, ...],
    資料不足、找不到足夠的支撐、或高峰都超過 `VOLUME_PROFILE_MAX_DISTANCE_PCT` 距離上限時,
    回傳少於 num_supports 筆(不是錯誤)。
    """
    return _find_nearest_peaks(price_df, num_supports, above=False)


def find_nearest_resistances(price_df: pd.DataFrame, num_resistances: int = 3) -> list[dict]:
    """算出離目前收盤價最近的 num_resistances 個「籌碼密集區」阻力價位(收盤價之上的區域高峰)。

    跟 `find_nearest_supports()` 是同一套邏輯,方向相反——用途、資料需求、回傳格式、
    距離上限規則都一樣。
    """
    return _find_nearest_peaks(price_df, num_resistances, above=True)


if __name__ == "__main__":
    from fetch_data import get_history

    code = "2330"
    hist = get_history(code, period="6mo")
    close = float(hist["Close"].iloc[-1])
    print(f"=== {code} 收盤價 {close:.2f},近{VOLUME_PROFILE_LOOKBACK_DAYS}天籌碼支撐/阻力 ===")

    for label, levels in [("支撐", find_nearest_supports(hist)), ("阻力", find_nearest_resistances(hist))]:
        print(f"--- {label} ---")
        for lvl in levels:
            print(f"  {lvl['price']:.2f}  ({(lvl['price']/close - 1) * 100:+.2f}%)  分佈量約 {lvl['volume']:,.0f} 股")
        if not levels:
            print(f"  (找不到明顯{label})")
