"""價格籌碼分佈(Volume Profile)——找出離目前收盤價最近的支撐/阻力價位。

概念:把一段期間的股價範圍切成很多價格區間(bin),把每天的成交量依當天 Low~High
涵蓋哪些區間、按重疊比例分攤進去,還原出「成交量在各個價位的分佈」。分佈的區域高峰
代表過去有大量換手發生過的價位,通常比較容易形成支撐/阻力(在那個價位持有部位的人多,
股價跌回/漲回那裡容易有人加碼防守或獲利了結)——高峰在目前收盤價之下當支撐、之上當阻力,
同一份分佈、同一套峰值偵測邏輯,差別只在篩選方向。

daily OHLCV 沒有逐筆成交明細,這裡的「分佈」是用 Low~High 區間近似還原,不是真正的
逐筆委託成交價格統計。
"""

import numpy as np
import pandas as pd

VOLUME_PROFILE_LOOKBACK_DAYS = 60  # 近3個月交易日——使用者做短線交易,確認用這個區間
VOLUME_PROFILE_BINS = 50
VOLUME_PROFILE_PEAK_WINDOW = 2  # 區域高峰要比左右各幾個 bin 都高,數字越大雜訊越少


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
    """核心邏輯,支撐(above=False)跟阻力(above=True)共用同一套峰值偵測,只差篩選方向。"""
    window = price_df.tail(VOLUME_PROFILE_LOOKBACK_DAYS).dropna(subset=["Low", "High", "Volume", "Close"])
    if len(window) < 5:
        return []

    close = float(window["Close"].iloc[-1])
    bin_centers, volume_per_bin = _build_volume_histogram(window, VOLUME_PROFILE_BINS)

    n = VOLUME_PROFILE_PEAK_WINDOW
    peaks = []
    for i in range(n, len(volume_per_bin) - n):
        v = volume_per_bin[i]
        if v <= 0:
            continue
        neighbors = np.concatenate([volume_per_bin[i - n : i], volume_per_bin[i + 1 : i + 1 + n]])
        if v > neighbors.max():
            peaks.append({"price": float(bin_centers[i]), "volume": float(v)})

    if above:
        candidates = sorted((p for p in peaks if p["price"] > close), key=lambda p: p["price"])
    else:
        candidates = sorted((p for p in peaks if p["price"] < close), key=lambda p: p["price"], reverse=True)
    return candidates[:num_levels]


def find_nearest_supports(price_df: pd.DataFrame, num_supports: int = 2) -> list[dict]:
    """算出離目前收盤價最近的 num_supports 個「籌碼密集區」支撐價位(收盤價之下的區域高峰)。

    price_df 需要 Low/High/Volume/Close 欄位,用呼叫端已經算好指標的 df 直接複用即可,
    不用重抓資料。回傳依離收盤價由近到遠排序的 [{"price": float, "volume": float}, ...],
    資料不足或找不到足夠的支撐時回傳少於 num_supports 筆(不是錯誤)。
    """
    return _find_nearest_peaks(price_df, num_supports, above=False)


def find_nearest_resistances(price_df: pd.DataFrame, num_resistances: int = 2) -> list[dict]:
    """算出離目前收盤價最近的 num_resistances 個「籌碼密集區」阻力價位(收盤價之上的區域高峰)。

    跟 `find_nearest_supports()` 是同一套邏輯,方向相反——用途、資料需求、回傳格式都一樣。
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
