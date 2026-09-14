"""在歷史股價 DataFrame 上疊加常用技術指標。"""

import numpy as np
import pandas as pd
import ta

from fetch_data import get_history


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在 OHLCV DataFrame 上加入常用技術指標欄位。

    需要欄位:Open, High, Low, Close, Volume(yfinance history() 的預設欄名)
    """
    df = df.copy()

    # 移動平均線(EMA,近期價格權重較高,比 SMA 對趨勢轉折更敏感)
    df["EMA6"] = ta.trend.ema_indicator(df["Close"], window=6)
    df["EMA40"] = ta.trend.ema_indicator(df["Close"], window=40)
    df["EMA56"] = ta.trend.ema_indicator(df["Close"], window=56)

    # RSI(相對強弱指標)——ta.momentum.rsi() 內部平均漲跌幅是用 Wilder 平滑(alpha=1/window,
    # 業界最常見的「標準版RSI」),不是一般 EMA(alpha=2/(window+1))。使用者要求跟上面的
    # 均線/布林通道一致都改EMA,所以這裡改成自己組:漲跌幅分別用 ta.trend.ema_indicator
    # 同一套 EMA 公式平滑,不再用 ta 函式庫的 Wilder 版本。
    rsi_window = 14
    diff = df["Close"].diff()
    gain = diff.where(diff > 0, 0.0)
    loss = -diff.where(diff < 0, 0.0)
    avg_gain = ta.trend.ema_indicator(gain, window=rsi_window)
    avg_loss = ta.trend.ema_indicator(loss, window=rsi_window)
    rs = avg_gain / avg_loss
    df["RSI14"] = np.where(avg_loss == 0, 100, 100 - (100 / (1 + rs)))

    # MACD
    macd = ta.trend.MACD(df["Close"])
    df["MACD"] = macd.macd()
    df["MACD_signal"] = macd.macd_signal()
    df["MACD_hist"] = macd.macd_diff()

    # 布林通道(中軌改用 EMA,跟上面均線一致——ta 函式庫的 BollingerBands 只有 SMA 中軌,
    # 沒有 EMA 選項,所以中軌改用跟 EMA6/40/56 同一個 ta.trend.ema_indicator 自己組,
    # 標準差還是用一般的滾動標準差,沒有「指數加權標準差」這種業界標準算法可以套用)
    bb_window, bb_dev = 20, 2
    bb_std = df["Close"].rolling(window=bb_window).std()
    df["BB_mid"] = ta.trend.ema_indicator(df["Close"], window=bb_window)
    df["BB_upper"] = df["BB_mid"] + bb_dev * bb_std
    df["BB_lower"] = df["BB_mid"] - bb_dev * bb_std

    return df


if __name__ == "__main__":
    code = "2330"
    hist = get_history(code, period="1y")
    result = add_indicators(hist)

    cols = ["Close", "EMA6", "EMA40", "EMA56", "RSI14", "MACD", "MACD_signal", "BB_upper", "BB_lower"]
    print(f"=== {code} 技術指標(最近 5 筆) ===")
    print(result[cols].tail())
