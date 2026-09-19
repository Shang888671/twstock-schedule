"""風險控管模組 — 部位大小計算、停損停利判斷、資金配置。

設計：
- 資金水位存在 SQLite risk_config 表，重開 Streamlit 也會保留
- 單檔部位上限 10%（risk_per_trade_pct）
- 單日最大虧損 2%（max_daily_loss_pct）
- 總部位上限 80%（max_total_position_pct）

用法：
    from risk import RiskManager
    rm = RiskManager(total_capital=1_000_000, risk_per_trade_pct=0.1)
    qty = rm.calc_position_size(price=2460, atr=30)
"""

import sqlite3
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data_cache"
DB_PATH = DATA_DIR / "stock.db"

# 預設值
DEFAULT_TOTAL_CAPITAL = 1_000_000
DEFAULT_RISK_PER_TRADE_PCT = 0.10
DEFAULT_MAX_DAILY_LOSS_PCT = 0.02
DEFAULT_MAX_TOTAL_POSITION_PCT = 0.80


def _get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_risk_config() -> dict:
    """從 DB 讀取風控設定，若無則回傳預設值 + 寫入 DB。"""
    conn = _get_db_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    row = conn.execute("SELECT value FROM risk_config WHERE key = 'total_capital'").fetchone()
    if row is None:
        # 首次使用：寫入預設值
        defaults = {
            "total_capital": str(DEFAULT_TOTAL_CAPITAL),
            "risk_per_trade_pct": str(DEFAULT_RISK_PER_TRADE_PCT),
            "max_daily_loss_pct": str(DEFAULT_MAX_DAILY_LOSS_PCT),
            "max_total_position_pct": str(DEFAULT_MAX_TOTAL_POSITION_PCT),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO risk_config (key, value) VALUES (?, ?)",
            list(defaults.items()),
        )
        conn.commit()
        conn.close()
        return {k: float(v) for k, v in defaults.items()}
    # 已有設定：讀出所有
    rows = conn.execute("SELECT key, value FROM risk_config").fetchall()
    conn.close()
    return {k: float(v) for k, v in rows}


def set_risk_config(total_capital: float | None = None, risk_per_trade_pct: float | None = None,
                   max_daily_loss_pct: float | None = None, max_total_position_pct: float | None = None):
    """更新風控設定到 DB。"""
    conn = _get_db_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    updates = {}
    if total_capital is not None:
        updates["total_capital"] = str(total_capital)
    if risk_per_trade_pct is not None:
        updates["risk_per_trade_pct"] = str(risk_per_trade_pct)
    if max_daily_loss_pct is not None:
        updates["max_daily_loss_pct"] = str(max_daily_loss_pct)
    if max_total_position_pct is not None:
        updates["max_total_position_pct"] = str(max_total_position_pct)
    conn.executemany(
        "INSERT OR REPLACE INTO risk_config (key, value) VALUES (?, ?)",
        list(updates.items()),
    )
    conn.commit()
    conn.close()


class RiskManager:
    def __init__(
        self,
        total_capital: float | None = None,
        risk_per_trade_pct: float | None = None,
        max_daily_loss_pct: float | None = None,
        max_total_position_pct: float | None = None,
    ):
        # 優先用传入的參數，否则从 DB 读取，否则用預設
        db_config = get_risk_config()
        self.total_capital = total_capital if total_capital is not None else db_config.get("total_capital", DEFAULT_TOTAL_CAPITAL)
        self.risk_per_trade_pct = risk_per_trade_pct if risk_per_trade_pct is not None else db_config.get("risk_per_trade_pct", DEFAULT_RISK_PER_TRADE_PCT)
        self.max_daily_loss_pct = max_daily_loss_pct if max_daily_loss_pct is not None else db_config.get("max_daily_loss_pct", DEFAULT_MAX_DAILY_LOSS_PCT)
        self.max_total_position_pct = max_total_position_pct if max_total_position_pct is not None else db_config.get("max_total_position_pct", DEFAULT_MAX_TOTAL_POSITION_PCT)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                code TEXT NOT NULL,
                shares INTEGER NOT NULL,
                avg_price REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                opened_at TEXT NOT NULL,
                PRIMARY KEY (code)
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                action TEXT NOT NULL,
                shares INTEGER NOT NULL,
                price REAL NOT NULL,
                pnl REAL,
                traded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_pnl (
                date TEXT PRIMARY KEY,
                pnl REAL DEFAULT 0.0,
                num_trades INTEGER DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def calc_position_size(self, price: float, atr: float | None = None) -> int:
        """用 ATR 或固定% 計算建議股數。回傳張數（1張=1000股）。"""
        max_capital = self.total_capital * self.risk_per_trade_pct
        if atr and atr > 0:
            shares = int(max_capital / (atr * 1000)) * 1000
        else:
            shares = int(max_capital / price) // 1000 * 1000
        return max(shares, 1000)

    def should_stop_loss(self, code: str, current_price: float) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT stop_loss FROM positions WHERE code = ?", (code,)).fetchone()
        conn.close()
        if row and row[0] and current_price <= row[0]:
            return True
        return False

    def should_take_profit(self, code: str, current_price: float) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT take_profit FROM positions WHERE code = ?", (code,)).fetchone()
        conn.close()
        if row and row[0] and current_price >= row[0]:
            return True
        return False

    def add_position(self, code: str, shares: int, avg_price: float, stop_loss: float = None, take_profit: float = None):
        conn = self._get_conn()
        now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = conn.execute("SELECT shares, avg_price FROM positions WHERE code = ?", (code,)).fetchone()
        if existing:
            old_shares, old_price = existing
            new_shares = old_shares + shares
            new_avg = (old_shares * old_price + shares * avg_price) / new_shares
            conn.execute(
                "UPDATE positions SET shares=?, avg_price=?, stop_loss=?, take_profit=? WHERE code=?",
                (new_shares, new_avg, stop_loss, take_profit, code),
            )
        else:
            conn.execute(
                "INSERT INTO positions (code, shares, avg_price, stop_loss, take_profit, opened_at) VALUES (?,?,?,?,?,?)",
                (code, shares, avg_price, stop_loss, take_profit, now),
            )
        conn.commit()
        conn.close()

    def close_position(self, code: str, exit_price: float) -> float:
        conn = self._get_conn()
        row = conn.execute("SELECT shares, avg_price FROM positions WHERE code = ?", (code,)).fetchone()
        if not row:
            conn.close()
            return 0.0
        shares, avg_price = row
        pnl = (exit_price - avg_price) * shares
        now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM positions WHERE code = ?", (code,))
        conn.execute(
            "INSERT INTO trades (code, action, shares, price, pnl, traded_at) VALUES (?,?,?,?,?,?)",
            (code, "close", shares, exit_price, pnl, now),
        )
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO daily_pnl (date, pnl, num_trades)
            VALUES (?, ?, 1)
            ON CONFLICT(date) DO UPDATE SET
                pnl = daily_pnl.pnl + excluded.pnl,
                num_trades = daily_pnl.num_trades + excluded.num_trades
        """, (today, pnl))
        conn.commit()
        conn.close()
        return pnl

    def get_total_position_value(self) -> float:
        conn = self._get_conn()
        rows = conn.execute("SELECT code, shares FROM positions").fetchall()
        total = 0.0
        for code, shares in rows:
            quote = conn.execute("SELECT last_price FROM twse_mis_cache WHERE code = ?", (code,)).fetchone()
            if quote and quote[0]:
                total += quote[0] * shares
        conn.close()
        return total

    def can_open_new_position(self) -> tuple:
        total_value = self.get_total_position_value()
        max_value = self.total_capital * self.max_total_position_pct
        if total_value >= max_value:
            return False, f"總部位已達上限 {total_value:,.0f} / {max_value:,.0f}"
        conn = self._get_conn()
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        row = conn.execute("SELECT pnl FROM daily_pnl WHERE date = ?", (today,)).fetchone()
        conn.close()
        if row and row[0] < -self.total_capital * self.max_daily_loss_pct:
            return False, f"今日已虧 {row[0]:,.0f}，已達上限"
        return True, ""

    def get_portfolio_summary(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute("SELECT code, shares, avg_price, stop_loss, take_profit FROM positions").fetchall()
        positions = []
        total_value = 0.0
        for code, shares, avg_price, stop_loss, take_profit in rows:
            quote = conn.execute("SELECT last_price FROM twse_mis_cache WHERE code = ?", (code,)).fetchone()
            current_price = quote[0] if quote and quote[0] else avg_price
            value = current_price * shares
            pnl = (current_price - avg_price) * shares
            total_value += value
            positions.append({
                "code": code, "shares": shares, "avg_price": avg_price,
                "current_price": current_price, "value": value, "pnl": pnl,
                "stop_loss": stop_loss, "take_profit": take_profit,
            })
        conn.close()
        return {
            "total_capital": self.total_capital,
            "total_position_value": total_value,
            "cash": self.total_capital - total_value,
            "positions": positions,
        }


if __name__ == "__main__":
    rm = RiskManager()
    print(f"目前資金：{rm.total_capital:,.0f}")
    print(f"建議台積電部位：{rm.calc_position_size(2460)} 股")
