"""SQLite 資料庫層 — 統一所有籌碼/融資/權證資料讀寫。

資料表：
- institutional_flow: 三大法人買賣超
- margin_balance: 融資融券餘額
- warrant_flow: 權證籌碼
- warrant_large_trade: 權證大額交易筆數
- twse_mis_cache: TWSE MIS 即時報價快取

用法：
    from database import init_db, import_all_csv, get_institutional_flow
    init_db()
    import_all_csv()  # 一次性匯入所有 CSV
    df = get_institutional_flow('2330', '2026-08-01', '2026-09-18')
"""

import sqlite3
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data_cache"
DB_PATH = DATA_DIR / "stock.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """冪等建表（已存在不重複建）。"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS institutional_flow (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            foreign_net INTEGER,
            trust_net INTEGER,
            dealer_net INTEGER,
            total_net INTEGER,
            market TEXT DEFAULT 'listed',
            PRIMARY KEY (code, date)
        );
        CREATE TABLE IF NOT EXISTS margin_balance (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            margin_balance INTEGER,
            short_balance INTEGER,
            PRIMARY KEY (code, date)
        );
        CREATE TABLE IF NOT EXISTS warrant_flow (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            call_volume REAL,
            put_volume REAL,
            call_value REAL,
            put_value REAL,
            pc_ratio REAL,
            PRIMARY KEY (code, date)
        );
        CREATE TABLE IF NOT EXISTS warrant_large_trade (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            count INTEGER,
            threshold INTEGER,
            PRIMARY KEY (code, date, threshold)
        );
        CREATE TABLE IF NOT EXISTS twse_mis_cache (
            code TEXT NOT NULL PRIMARY KEY,
            last_price REAL,
            previous_close REAL,
            day_high REAL,
            day_low REAL,
            volume INTEGER,
            fetched_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_inst_date ON institutional_flow(date);
        CREATE INDEX IF NOT EXISTS idx_margin_date ON margin_balance(date);
        CREATE INDEX IF NOT EXISTS idx_warrant_date ON warrant_flow(date);
    """)
    conn.commit()
    conn.close()
    print(f"[DB] 已初始化：{DB_PATH}")


def import_all_csv():
    """一次性匯入所有 CSV 到 SQLite。"""
    conn = _get_conn()

    # institutional
    count = 0
    for csv in sorted(DATA_DIR.glob("institutional_*.csv")):
        code = csv.stem.replace("institutional_", "")
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
        if df.empty:
            continue
        df.index.name = "date"
        df = df.reset_index()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df["code"] = code
        df["market"] = "listed" if len(code) == 4 and code.isdigit() else "otc"
        df = df[["code", "date", "foreign_net", "trust_net", "dealer_net", "total_net", "market"]]
        df.to_sql("institutional_flow", conn, if_exists="append", index=False, method="multi")
        count += len(df)
    print(f"[DB] institutional_flow：匯入 {count} 筆")

    # margin
    count = 0
    for csv in sorted(DATA_DIR.glob("margin_*.csv")):
        code = csv.stem.replace("margin_", "")
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
        if df.empty:
            continue
        df.index.name = "date"
        df = df.reset_index()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df["code"] = code
        df = df[["code", "date", "margin_balance", "short_balance"]]
        df.to_sql("margin_balance", conn, if_exists="append", index=False, method="multi")
        count += len(df)
    print(f"[DB] margin_balance：匯入 {count} 筆")

    # warrant_flow
    count = 0
    for csv in sorted(DATA_DIR.glob("warrant_flow_*.csv")):
        code = csv.stem.replace("warrant_flow_", "")
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
        if df.empty:
            continue
        df.index.name = "date"
        df = df.reset_index()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df["code"] = code
        df = df.rename(columns={
            "warrant_call_volume": "call_volume",
            "warrant_put_volume": "put_volume",
            "warrant_call_value": "call_value",
            "warrant_put_value": "put_value",
            "warrant_vol_pc_ratio": "pc_ratio",
        })
        df = df[["code", "date", "call_volume", "put_volume", "call_value", "put_value", "pc_ratio"]]
        df.to_sql("warrant_flow", conn, if_exists="append", index=False, method="multi")
        count += len(df)
    print(f"[DB] warrant_flow：匯入 {count} 筆")

    # warrant_large_trade_count
    count = 0
    for csv in sorted(DATA_DIR.glob("warrant_large_trade_count_*.csv")):
        # 解析：warrant_large_trade_count_priceup_0050_500000.csv
        parts = csv.stem.split("_")
        code = parts[-2]
        threshold = int(parts[-1])
        df = pd.read_csv(csv, index_col=0, parse_dates=True)
        if df.empty:
            continue
        df.index.name = "date"
        df = df.reset_index()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df["code"] = code
        df["threshold"] = threshold
        df = df.rename(columns={"count": "count"})
        df = df[["code", "date", "count", "threshold"]]
        df.to_sql("warrant_large_trade", conn, if_exists="append", index=False, method="multi")
        count += len(df)
    print(f"[DB] warrant_large_trade：匯入 {count} 筆")

    conn.commit()
    conn.close()


# ===== 查詢介面 =====

def get_institutional_flow(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """從 SQLite 查三大法人。沒有資料回傳空 DataFrame。"""
    conn = _get_conn()
    query = """
        SELECT date, foreign_net, trust_net, dealer_net, total_net
        FROM institutional_flow
        WHERE code = ? AND date >= ? AND date <= ?
        ORDER BY date
    """
    df = pd.read_sql_query(query, conn, params=(code, start_date, end_date), parse_dates=["date"])
    conn.close()
    if df.empty:
        return df
    df = df.set_index("date")
    df.index.name = "date"
    return df


def get_margin(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """從 SQLite 查融資融券。"""
    conn = _get_conn()
    query = """
        SELECT date, margin_balance, short_balance
        FROM margin_balance
        WHERE code = ? AND date >= ? AND date <= ?
        ORDER BY date
    """
    df = pd.read_sql_query(query, conn, params=(code, start_date, end_date), parse_dates=["date"])
    conn.close()
    if df.empty:
        return df
    df = df.set_index("date")
    df.index.name = "date"
    return df


def get_warrant_flow(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """從 SQLite 查權證。"""
    conn = _get_conn()
    query = """
        SELECT date, call_volume, put_volume, call_value, put_value, pc_ratio
        FROM warrant_flow
        WHERE code = ? AND date >= ? AND date <= ?
        ORDER BY date
    """
    df = pd.read_sql_query(query, conn, params=(code, start_date, end_date), parse_dates=["date"])
    conn.close()
    if df.empty:
        return df
    df = df.set_index("date")
    df.index.name = "date"
    return df


def get_warrant_large_trade(code: str, start_date: str, end_date: str, threshold: int = 500000) -> pd.DataFrame:
    """從 SQLite 查權證大額交易筆數。"""
    conn = _get_conn()
    query = """
        SELECT date, count
        FROM warrant_large_trade
        WHERE code = ? AND date >= ? AND date <= ? AND threshold = ?
        ORDER BY date
    """
    df = pd.read_sql_query(query, conn, params=(code, start_date, end_date, threshold), parse_dates=["date"])
    conn.close()
    if df.empty:
        return df
    df = df.set_index("date")
    df.index.name = "date"
    return df


# ===== 寫入介面 =====

def upsert_institutional(df: pd.DataFrame, code: str, market: str = "listed"):
    """寫入三大法人資料。df 欄位：foreign_net, trust_net, dealer_net, total_net。"""
    if df.empty:
        return
    conn = _get_conn()
    rows = []
    for date, row in df.iterrows():
        rows.append({
            "code": code,
            "date": date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date),
            "foreign_net": int(row.get("foreign_net", 0)),
            "trust_net": int(row.get("trust_net", 0)),
            "dealer_net": int(row.get("dealer_net", 0)),
            "total_net": int(row.get("total_net", 0)),
            "market": market,
        })
    conn.executemany("""
        INSERT OR REPLACE INTO institutional_flow (code, date, foreign_net, trust_net, dealer_net, total_net, market)
        VALUES (:code, :date, :foreign_net, :trust_net, :dealer_net, :total_net, :market)
    """, rows)
    conn.commit()
    conn.close()


def upsert_margin(df: pd.DataFrame, code: str):
    """寫入融資融券。df 欄位：margin_balance, short_balance。"""
    if df.empty:
        return
    conn = _get_conn()
    rows = []
    for date, row in df.iterrows():
        rows.append({
            "code": code,
            "date": date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date),
            "margin_balance": int(row.get("margin_balance", 0)),
            "short_balance": int(row.get("short_balance", 0)),
        })
    conn.executemany("""
        INSERT OR REPLACE INTO margin_balance (code, date, margin_balance, short_balance)
        VALUES (:code, :date, :margin_balance, :short_balance)
    """, rows)
    conn.commit()
    conn.close()


def upsert_warrant_flow(df: pd.DataFrame, code: str):
    """寫入權證。df 欄位：warrant_call_volume, warrant_put_volume, warrant_call_value, warrant_put_value, warrant_vol_pc_ratio。"""
    if df.empty:
        return
    conn = _get_conn()
    rows = []
    for date, row in df.iterrows():
        rows.append({
            "code": code,
            "date": date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date),
            "call_volume": float(row.get("warrant_call_volume", 0)),
            "put_volume": float(row.get("warrant_put_volume", 0)),
            "call_value": float(row.get("warrant_call_value", 0)),
            "put_value": float(row.get("warrant_put_value", 0)),
            "pc_ratio": float(row.get("warrant_vol_pc_ratio", 0)),
        })
    conn.executemany("""
        INSERT OR REPLACE INTO warrant_flow (code, date, call_volume, put_volume, call_value, put_value, pc_ratio)
        VALUES (:code, :date, :call_volume, :put_volume, :call_value, :put_value, :pc_ratio)
    """, rows)
    conn.commit()
    conn.close()


def upsert_mis_quote(code: str, quote: dict):
    """快取 TWSE MIS 即時報價。"""
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO twse_mis_cache (code, last_price, previous_close, day_high, day_low, volume, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        code,
        quote.get("last_price"),
        quote.get("previous_close"),
        quote.get("day_high"),
        quote.get("day_low"),
        quote.get("volume"),
        time.time(),
    ))
    conn.commit()
    conn.close()


def get_mis_quote(code: str, max_age_seconds: int = 60) -> dict | None:
    """取得快取的 TWSE MIS 報價。超過 max_age_seconds 視為過期，回傳 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT last_price, previous_close, day_high, day_low, volume, fetched_at FROM twse_mis_cache WHERE code = ?",
        (code,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    last_price, prev_close, high, low, volume, fetched_at = row
    if time.time() - fetched_at > max_age_seconds:
        return None
    return {
        "symbol": f"{code}.TW",
        "last_price": last_price,
        "previous_close": prev_close,
        "day_high": high,
        "day_low": low,
        "volume": volume,
        "source": "twse_mis_cache",
    }


def vacuum():
    """清理資料庫（移除過期快取、重建索引）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM twse_mis_cache WHERE fetched_at < ?", (time.time() - 3600,))
    conn.execute("REINDEX")
    conn.execute("VACUUM")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    import_all_csv()
    print("\n=== 查詢測試 ===")
    df = get_institutional_flow("2330", "2026-08-01", "2026-09-18")
    print(f"2330 三大法人：{len(df)} 筆")
    print(df.tail(3))
    print()
    df = get_margin("2330", "2026-09-01", "2026-09-18")
    print(f"2330 融資融券：{len(df)} 筆")
    print(df.tail(3))
