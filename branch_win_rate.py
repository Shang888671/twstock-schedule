"""分點勝率化——用 snowyowl 抓券商分點的逐日買賣超,累積歷史,統計「這個分點買超一檔
股票之後,股票後續表現有沒有贏大盤」的歷史命中率,取代主觀認定的「已知隔日沖大戶分點」。

**資料來源**:snowyowl(`C:\\Users\\Ryzen USER\\Documents\\snowyowl`,已由使用者提供帳密),
`api.Data.get()` 的兩張分點籌碼表:
- `查詢近100日買賣張數加總_高至低排序(symbol)`——這檔股票近100日「所有」交易過的分點清單
  (不是只有TOP15/TOP20,使用者明確要求全抓)。實測發現回傳的 `分點代號` 欄位本身就是
  `'代號 分點名'` 的組合格式(例如 `'9200 凱基'`),不用自己拼接 `分點代號`+`分點名稱`
  兩欄——這點文件沒寫清楚,是實測 `df.head()` 才發現的。
- `查詢近100日該分點資料(symbol, brokerId)`——指定股票+分點的逐日買/賣/買賣超(張)、
  買賣均價,近100個交易日(實測 shape 是 (120, 6),比100多一些)。

**歷史深度限制**:只有「近100日」,不像海外資料源能回溯好幾年,但因為是逐日資料、一次
backfill就能拿到,90天前的買超馬上就能算出20日後表現如何,不用像純累積式設計等好幾週。

**勝率定義**:相對大盤(^TWII加權指數)超額報酬,不是絕對報酬轉正——避免多頭行情時
所有分點都顯得「很準」,其實只是跟著大盤漲,失去分辨力(使用者已確認這個定義)。

**規模**:對249.dsl的全部247檔股票、每檔股票的「所有」分點(可能幾十到將近900個)都抓,
不設TOP N上限——這是使用者明確要求的規模,還沒實測過完整跑一輪要多久、會不會撞到
snowyowl帳號的速率限制,第一次執行要如實記錄花費時間跟有沒有出錯回報給使用者。
"""

import os
import time
import sqlite3
import traceback
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, cast

import pandas as pd

import fetch_data

WINDOWS = (5, 10, 20)  # 交易日
MIN_SAMPLES_FOR_WIN_RATE = 10  # 樣本數不足這個門檻,勝率不顯示(呼叫端顯示「累積中」)
BENCHMARK_CODE = "^TWII"

# 這兩個檔案刻意放在 data_cache/ 之外——data_cache/ 整個資料夾被 .gitignore 排除
# (本機快取,執行時自動重建,不用進版控),但這兩個檔案的用途完全相反:要被 git
# commit+push 回 repo,讓 Streamlit Cloud 能讀到,所以獨立一個會進版控的資料夾。
HISTORY_DIR = Path(__file__).parent / "branch_history"
HISTORY_DIR.mkdir(exist_ok=True)
HISTORY_PATH = HISTORY_DIR / "branch_buying_history.csv"
SUMMARY_PATH = HISTORY_DIR / "branch_win_rates_summary.csv"

# 這個檔案只是「這次一次性全量backfill」的進度標記,跟branch_history/裡要進版控的正式
# 資料完全不同性質——放data_cache/(已gitignore,本機執行期產物)。247檔規模、每檔
# 要跑好幾分鐘,整個過程動輒20~30小時,中途任何原因中斷(當機、網路斷線、電腦重開)
# 都要能接著跑、不用從第1檔重來——一檔股票的「所有分點都處理完、沒有中途例外往外拋」
# 才算完成、才會被記進這個檔案,重跑時已完成的股票直接跳過。
DATA_CACHE_DIR = Path(__file__).parent / "data_cache"
DATA_CACHE_DIR.mkdir(exist_ok=True)
BACKFILL_PROGRESS_PATH = DATA_CACHE_DIR / "branch_backfill_completed_codes.txt"

RETRY_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = (3, 8, 20)  # 第1次失敗後等3秒重試,第2次等8秒,第3次等20秒
RETRY_EXPONENTIAL_BASE = 2.0  # 指數退避基底,可外部環境變數覆寫

# SQLite token health tracking database path
TOKEN_DB_PATH = Path(__file__).parent / "data_cache" / "token_health.db"
TOKEN_LIFETIME_SECONDS = 61 * 60  # snowyowl token 實測壽命約61分鐘

_T = TypeVar("_T")

_HISTORY_COLUMNS = ["code", "date", "broker", "net_buy_lots"] + [
    f"excess_return_{w}d" for w in WINDOWS
]

_api = None


def _init_token_db() -> None:
    """建立 SQLite 資料庫和 token_health 表格(不存在才建)。

    這個表格追蹤每次 token 登入的時間、存活狀態、最後健康檢查時間,
    用來在批次執行前判斷是否需要提前 reauth,避免跑到一半才過期。
    """
    TOKEN_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(TOKEN_DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS token_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                last_login_time REAL NOT NULL,
                last_check_time REAL,
                is_valid INTEGER NOT NULL DEFAULT 1,
                error_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


def _record_login() -> None:
    """記錄一次成功的 login 時間戳到 SQLite。"""
    _init_token_db()
    with sqlite3.connect(str(TOKEN_DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO token_health (last_login_time, is_valid, created_at) VALUES (?, 1, ?)",
            (time.time(), time.time()),
        )
        conn.commit()


def _record_check(is_valid: bool) -> None:
    """記錄一次健康檢查結果,更新最新一筆 login 記錄的檢查時間跟狀態。"""
    _init_token_db()
    with sqlite3.connect(str(TOKEN_DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE token_health
            SET last_check_time = ?, is_valid = ?
            WHERE id = (SELECT MAX(id) FROM token_health)
            """,
            (time.time(), 1 if is_valid else 0),
        )
        conn.commit()


def _increment_error_count() -> None:
    """對最新一筆 login 記錄累加錯誤次數(用於 retry 全部失敗後)。"""
    _init_token_db()
    with sqlite3.connect(str(TOKEN_DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE token_health
            SET error_count = error_count + 1
            WHERE id = (SELECT MAX(id) FROM token_health)
            """
        )
        conn.commit()


def _get_last_login_time() -> Optional[float]:
    """回傳最近一次 login 的時間戳,沒有記錄回傳 None。"""
    if not TOKEN_DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(str(TOKEN_DB_PATH)) as conn:
            row = conn.execute(
                "SELECT last_login_time FROM token_health ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def _token_age_seconds() -> Optional[float]:
    """回傳目前 token 已經存活了幾秒,無法判斷回傳 None。"""
    last = _get_last_login_time()
    if last is None:
        return None
    return time.time() - last


def login(force: bool = False) -> Any:
    """回傳已登入的 snowyowl api 物件,模組內快取,不用每個函式各自重新登入
    (登入本身要10~15秒,還會下載/更新MSMP本地快取,重複登入很浪費)。

    **實測發現登入的 token 大約 61 分鐘就會過期**(247檔全量backfill第一次實跑,
    第13檔開始每一次呼叫都收到「未通過身份驗證或驗證Token已過期失效」,回頭查時間戳記
    正好是登入後61分鐘)——247檔全跑要20小時以上,勢必會跨過這個時效,呼叫端要用
    `_call_with_reauth()` 包住每一次 `api.Data.get()`,不能只在程式一開始登入一次
    就假設整個過程都有效。`force=True` 強制清掉快取重新登入,給偵測到token過期時用。

    帳密來源比照 alert_monitor.py 的既有慣例:本機優先讀 alert_config.py,
    雲端主機(alert_config.py 不會被打包上去)改讀環境變數
    SNOWYOWL_PERSON_ID/SNOWYOWL_PERSON_PWD。
    """
    global _api
    if _api is not None and not force:
        return _api

    person_id = os.environ.get("SNOWYOWL_PERSON_ID")
    person_pwd = os.environ.get("SNOWYOWL_PERSON_PWD")
    if not person_id or not person_pwd:
        try:
            import alert_config as _config
        except ImportError:
            import alert_config_cloud as _config
        person_id = getattr(_config, "SNOWYOWL_PERSON_ID", None)
        person_pwd = getattr(_config, "SNOWYOWL_PERSON_PWD", None)

    if not person_id or not person_pwd:
        raise SystemExit(
            "找不到 snowyowl 登入帳密——在 alert_config.py 加上 SNOWYOWL_PERSON_ID/"
            "SNOWYOWL_PERSON_PWD,或設定同名的環境變數。"
        )

    import snowyowl as so

    api = so.login(person_id, person_pwd)
    if not hasattr(api, "Data"):
        raise SystemExit(
            "snowyowl 登入成功但 api.Data 沒有掛上去(通常是帳號的 Token 無效/過期),"
            "分點資料抓不到。"
        )
    _api = api
    _record_login()
    return api


def _call_with_reauth(fn: Callable[[], _T]) -> _T:
    """呼叫一次api方法,遇到token過期(GetDataError code=UNAUTHORIZED)就強制重新登入
    後重試一次;其他例外原樣往外拋,交給外層的 _call_with_retry 處理。"""
    from snowyowl.data._response import GetDataCode, GetDataError

    try:
        return fn()
    except GetDataError as e:
        if e.code != GetDataCode.UNAUTHORIZED:
            raise
        print("snowyowl token 已過期,重新登入後重試一次...")
        login(force=True)
        return fn()


def _call_with_retry(fn: Callable[[], _T], label: str) -> _T:
    """在 _call_with_reauth 外面再包一層一般性重試——247檔全量backfill規模大、要跑
    20~30小時,使用者明確要求「全部都要抓下來、不能中斷」,單純的網路瞬斷/伺服器
    暫時錯誤不該直接放棄一個分點或一檔股票的資料。最多重試RETRY_ATTEMPTS次,
    每次之間用漸增的等待時間,全部試完還失敗才真的放棄(往外拋例外,由呼叫端決定
    要不要跳過)。

    退避策略:優先使用指數退避(RETRY_EXPONENTIAL_BASE ** attempt 秒),
    上限由 RETRY_BACKOFF_SECONDS 最大值決定。若 RETRY_BACKOFF_SECONDS 有設定
    (非空 tuple),仍保留原本的手動值作為 fallback。
    """
    last_exc: Optional[Exception] = None
    max_backoff = RETRY_BACKOFF_SECONDS[-1] if RETRY_BACKOFF_SECONDS else 30

    for attempt in range(RETRY_ATTEMPTS):
        try:
            return _call_with_reauth(fn)
        except Exception as e:
            last_exc = e
            if attempt < RETRY_ATTEMPTS - 1:
                # 指數退避: 2^attempt * base 秒,但不超過 max_backoff
                backoff_exp = min(
                    RETRY_EXPONENTIAL_BASE ** attempt,
                    max_backoff,
                )
                # 若在手動 tuple 範圍內,保留原值作為 fallback
                if attempt < len(RETRY_BACKOFF_SECONDS):
                    wait = RETRY_BACKOFF_SECONDS[attempt]
                else:
                    wait = backoff_exp
                print(f"{label} 第{attempt + 1}次失敗({e!r}),{wait:.1f}秒後重試...")
                time.sleep(wait)

    _increment_error_count()
    raise cast(Exception, last_exc)


def health_check(force_reauth_threshold: float = 0.8) -> bool:
    """測試目前 token 是否仍然有效,如果已過有效期的 force_reauth_threshold (預設 80%)
    就主動重新登入,避免在批次執行中途才過期。

    force_reauth_threshold=0.8 表示:當 token 已經活了超過 80% 的 TOKEN_LIFETIME_SECONDS
    時就提前 reauth,不用等到真的過期才重來。

    回傳 True 表示 token 可用(本來就有效或已經重新登入),False 表示即使重新登入也失敗。
    """
    age = _token_age_seconds()

    # 無法判斷 token 年齡(第一次執行、沒有 SQLite 記錄),做一次輕量 API 呼叫測試
    if age is None:
        try:
            api = login()
            if hasattr(api, "Data") and hasattr(api.Data, "get"):
                _record_check(True)
                return True
            else:
                _record_check(False)
                return False
        except Exception as e:
            print(f"health_check 失敗(無法判斷 token 年齡): {e!r}")
            _record_check(False)
            return False

    threshold = TOKEN_LIFETIME_SECONDS * force_reauth_threshold

    if age > threshold:
        print(
            f"token 已存活 {age / 60:.1f} 分鐘,超過 {force_reauth_threshold * 100:.0f}% 門檻"
            f"({threshold / 60:.1f} 分鐘),主動重新登入..."
        )
        try:
            login(force=True)
            _record_check(True)
            return True
        except Exception as e:
            print(f"health_check 重新登入失敗: {e!r}")
            _record_check(False)
            return False

    # token 還在安全範圍內,做一次輕量 ping 測試
    try:
        api = login()
        if hasattr(api, "Data"):
            _record_check(True)
            return True
        else:
            # api 物件存在但 Data 掛不上去,強制 reauth
            login(force=True)
            _record_check(True)
            return True
    except Exception as e:
        print(f"health_check ping 失敗: {e!r},嘗試重新登入...")
        try:
            login(force=True)
            _record_check(True)
            return True
        except Exception as e2:
            print(f"health_check 重新登入也失敗: {e2!r}")
            _record_check(False)
            return False


def _load_completed_codes() -> set[str]:
    if BACKFILL_PROGRESS_PATH.exists():
        return set(line.strip() for line in BACKFILL_PROGRESS_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
    return set()


def _mark_code_completed(code: str) -> None:
    with open(BACKFILL_PROGRESS_PATH, "a", encoding="utf-8") as f:
        f.write(code + "\n")


def _load_history() -> pd.DataFrame:
    if HISTORY_PATH.exists():
        # code欄一定要強制讀成字串——"2330"這種純數字代號,pandas預設會自動推斷成int64,
        # 之後拿它跟字串比對(如 history["code"] == code)或傳進fetch_data.to_yf_symbol()
        # (裡面呼叫code.startswith("^"))會静默失敗(int沒有startswith,被外層try/except
        # 吃掉變成None),導致整批report都算不出東西卻不報錯——實測全部n=0才抓到這個坑。
        return pd.read_csv(HISTORY_PATH, parse_dates=["date"], dtype={"code": str, "broker": str})
    return pd.DataFrame(columns=_HISTORY_COLUMNS)


def _save_history(df: pd.DataFrame) -> None:
    df = df.sort_values(["code", "date", "broker"]).reset_index(drop=True)
    df.to_csv(HISTORY_PATH, index=False)


def _discover_brokers(code: str) -> list[str]:
    """回傳這檔股票近100日「所有」交易過的分點 brokerId 清單(不限筆數)。

    `分點代號` 欄位實測本身就是 `'代號 分點名'` 格式,直接拿來當 brokerId 用,
    不用再跟 `分點名稱` 欄拼接。

    不直接接收api參數、而是每次呼叫login()現拿——login()內部有快取,平常這行幾乎
    零成本,但_call_with_reauth重新登入之後,下一次呼叫就能自動拿到新的api物件,
    不會卡在呼叫端傳進來的舊參照上(這是247檔全量backfill實測token過期後修的坑)。
    """
    df = _call_with_retry(
        lambda: login().Data.get("查詢近100日買賣張數加總_高至低排序", code), f"{code}分點清單"
    )
    if df is None or df.empty:
        return []
    return df["分點代號"].astype(str).str.strip().tolist()


def _fetch_broker_daily(code: str, broker_id: str) -> pd.DataFrame:
    df = _call_with_retry(
        lambda: login().Data.get("查詢近100日該分點資料", code, broker_id), f"{code}/{broker_id}逐日資料"
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "broker", "code", "net_buy_lots"])
    broker_name = broker_id.split(" ", 1)[-1] if " " in broker_id else broker_id
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["日期"]).dt.normalize(),
            "broker": broker_name,
            "code": code,
            "net_buy_lots": pd.to_numeric(df["買賣超(張)"], errors="coerce"),
        }
    )
    return out.dropna(subset=["net_buy_lots"])


def ingest_watchlist_snapshot(codes: list[str], sleep_seconds: float = 0.05, skip_completed: bool = True) -> int:
    """對每一檔code:抓全部分點清單,對每個分點抓逐日買賣超,篩出淨買超為正的列,
    用(code, date, broker)當唯一鍵去重後寫進歷史檔。

    **每處理完一檔股票就存一次檔**,而且完整處理完(沒有中途例外往外拋)才會被記進
    `BACKFILL_PROGRESS_PATH`——247檔×每檔可能幾十到將近900個分點,規模很大,一次
    全量backfill實測要20小時以上,使用者明確要求「不能中斷、全部都要抓下來」。
    `skip_completed=True`(預設)時,已經記錄完成的股票這次直接跳過,讓這支腳本可以
    被反覆重新啟動(不管是我自己手動重跑,還是外層supervisor偵測到腳本意外結束
    自動重啟)都能從中斷點接著跑,不用每次都從第1檔重來。

    每一檔股票的處理過程額外包一層最外層try/except——即使真的踩到目前沒預期到的
    例外(不是_discover_brokers/_fetch_broker_daily這兩個已經有重試機制的呼叫,
    而是其他任何原因),也只跳過這一檔,不會讓整個247檔的行程被單一檔股票的意外
    情況拖垮。單一分點查詢重試全部失敗後只跳過那個分點,不影響同一檔股票其他分點。
    回傳這次新增的筆數。
    """
    # 執行前先做健康檢查,確保 token 可用
    if not health_check():
        raise SystemExit("health_check 失敗,token 無法使用,停止批次執行")

    login()  # 先登入一次確認帳密有效,失敗就整批直接停(SystemExit),不要空轉
    history = _load_history()
    existing_keys = set(zip(history["code"], history["date"], history["broker"]))
    completed = _load_completed_codes() if skip_completed else set()
    total_new = 0

    for i, code in enumerate(codes, 1):
        if code in completed:
            print(f"[{i}/{len(codes)}] {code} 已於先前執行完成,跳過")
            continue

        t0 = time.time()
        try:
            brokers = _discover_brokers(code)

            rows = []
            n_broker_errors = 0
            for broker_id in brokers:
                try:
                    daily = _fetch_broker_daily(code, broker_id)
                except Exception:
                    n_broker_errors += 1
                    continue
                if sleep_seconds:
                    time.sleep(sleep_seconds)
                positive = daily[daily["net_buy_lots"] > 0]
                for _, row in positive.iterrows():
                    key = (row["code"], row["date"], row["broker"])
                    if key not in existing_keys:
                        rows.append(row)
                        existing_keys.add(key)

            if rows:
                new_df = pd.DataFrame(rows)
                for w in WINDOWS:
                    new_df[f"excess_return_{w}d"] = pd.NA
                history = pd.concat([history, new_df], ignore_index=True)
                _save_history(history)
                total_new += len(rows)

            _mark_code_completed(code)
            elapsed = time.time() - t0
            print(
                f"[{i}/{len(codes)}] {code}:{len(brokers)} 個分點"
                + (f"(其中{n_broker_errors}個查詢失敗)" if n_broker_errors else "")
                + f",新增{len(rows)}筆買超記錄,耗時{elapsed:.1f}秒"
            )
        except Exception:
            print(f"[{i}/{len(codes)}] {code} 處理失敗(重試已用盡),跳過,下次重跑會再試:\n{traceback.format_exc()}")
            continue

    return total_new


def _get_price_series(code: str) -> Optional[pd.Series]:
    """抓收盤價序列(index是日期),上市/上櫃未知就先當上市抓、抓不到再當上櫃重試——
    分點資料表不記錄上市/上櫃,只能用這種方式判斷。抓不到回傳None。"""
    code = str(code)
    for otc in (False, True):
        try:
            df = fetch_data.get_history(code, period="6mo", otc=otc)
        except Exception:
            continue
        if df is not None and not df.empty:
            s = df["Close"].copy()
            # yfinance回傳的index是tz-aware(Asia/Taipei),歷史檔裡的date欄是tz-naive
            # (snowyowl的日期沒有時區資訊)——兩者不統一的話,searchsorted比對tz-aware
            # 跟tz-naive的Timestamp不會報錯但會比對失真,導致每一列都算不出報酬(實測
            # 全部n=0才發現這個坑)。這裡統一轉成tz-naive。
            idx = pd.to_datetime(s.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            s.index = idx.normalize()
            return s
    return None


def _forward_return(price_series: pd.Series, date: pd.Timestamp, window: int) -> Optional[float]:
    """price_series在date那天之後第window個交易日的報酬率,還沒過那麼多天回傳None。"""
    idx = price_series.index
    pos = idx.searchsorted(date)
    if pos >= len(idx) or idx[pos] != date:
        # 買超當天不在價格序列裡(理論上不該發生,防禦性處理)——找下一個有資料的交易日
        if pos >= len(idx):
            return None
    if pos + window >= len(idx):
        return None
    return float(price_series.iloc[pos + window] / price_series.iloc[pos] - 1)


def _resolve_pending_returns(history: pd.DataFrame) -> pd.DataFrame:
    """對 excess_return_* 還是 NaN 的列,檢查是否已經過了對應交易天數、可以結算了——
    過了才抓價格算超額報酬,還沒過的維持NaN(不是bug,是還沒到能結算的時間點)。
    同一支股票的價格/大盤報酬各自只抓一次,不逐列重抓。
    """
    pending_mask = history[[f"excess_return_{w}d" for w in WINDOWS]].isna().any(axis=1)
    if not pending_mask.any():
        return history

    benchmark = _get_price_series(BENCHMARK_CODE)
    price_cache: dict[str, Optional[pd.Series]] = {}

    for code in history.loc[pending_mask, "code"].unique():
        if code not in price_cache:
            price_cache[code] = _get_price_series(code)
        stock_prices = price_cache[code]
        if stock_prices is None or benchmark is None:
            continue

        code_mask = pending_mask & (history["code"] == code)
        for row_idx in history.index[code_mask]:
            date = history.at[row_idx, "date"]
            for w in WINDOWS:
                col = f"excess_return_{w}d"
                if pd.notna(history.at[row_idx, col]):
                    continue
                stock_ret = _forward_return(stock_prices, date, w)
                bench_ret = _forward_return(benchmark, date, w)
                if stock_ret is not None and bench_ret is not None:
                    history.at[row_idx, col] = stock_ret - bench_ret

    _save_history(history)
    return history


def get_branch_win_rates(min_samples: int = MIN_SAMPLES_FOR_WIN_RATE) -> pd.DataFrame:
    """按broker分組,算每個時間窗已結算的樣本數n跟勝率(excess_return>0的比例)。
    樣本數不足min_samples的窗格,win_rate回傳None(呼叫端顯示「資料累積中」)。"""
    history = _resolve_pending_returns(_load_history())
    if history.empty:
        return pd.DataFrame(columns=["broker"] + [f"{c}_{w}d" for w in WINDOWS for c in ("n", "win_rate")])

    rows = []
    for broker, group in history.groupby("broker"):
        row: dict[str, Any] = {"broker": broker}
        for w in WINDOWS:
            resolved = group[f"excess_return_{w}d"].dropna()
            n = len(resolved)
            row[f"n_{w}d"] = n
            row[f"win_rate_{w}d"] = float((resolved > 0).mean()) if n >= min_samples else None
        rows.append(row)

    result = pd.DataFrame(rows)
    sort_col = "win_rate_20d"
    return result.sort_values(sort_col, ascending=False, na_position="last").reset_index(drop=True)


def get_branch_win_rate(broker: str, min_samples: int = MIN_SAMPLES_FOR_WIN_RATE) -> Optional[dict[str, Any]]:
    """單一分點查詢版本,給app.py顯示用。查無記錄回傳None。"""
    rates = get_branch_win_rates(min_samples)
    match = rates[rates["broker"] == broker]
    return match.iloc[0].to_dict() if not match.empty else None


def get_branch_win_rates_for_code(code: str, min_samples: int = MIN_SAMPLES_FOR_WIN_RATE) -> pd.DataFrame:
    """跟 get_branch_win_rates() 同一套勝率定義,但只用「這一檔股票」自己的買超歷史算——
    分點的資訊優勢不一定對所有股票都一致(可能只對特定股票準),「這個分點整體準不準」
    跟「這個分點對這檔股票本身準不準」是兩個不同問題,後者才是 get_significant_broker_names()
    要回答的。"""
    history = _resolve_pending_returns(_load_history())
    history = history[history["code"] == str(code)]
    if history.empty:
        return pd.DataFrame(columns=["broker"] + [f"{c}_{w}d" for w in WINDOWS for c in ("n", "win_rate")])

    rows = []
    for broker, group in history.groupby("broker"):
        row: dict[str, Any] = {"broker": broker}
        for w in WINDOWS:
            resolved = group[f"excess_return_{w}d"].dropna()
            n = len(resolved)
            row[f"n_{w}d"] = n
            row[f"win_rate_{w}d"] = float((resolved > 0).mean()) if n >= min_samples else None
        rows.append(row)

    result = pd.DataFrame(rows)
    return result.sort_values("win_rate_20d", ascending=False, na_position="last").reset_index(drop=True)


SIGNIFICANT_WIN_RATE_THRESHOLD = 0.6  # 高於這個勝率才算「對這檔股票驗證有效」,不是單純贏過擲硬幣(50%)


def get_significant_broker_names(
    code: str,
    min_samples: int = MIN_SAMPLES_FOR_WIN_RATE,
    win_rate_threshold: float = SIGNIFICANT_WIN_RATE_THRESHOLD,
) -> list[str]:
    """回傳「對這檔股票本身」買超後20日超額報酬勝率 >= win_rate_threshold、且樣本數已達
    min_samples 的分點名稱清單——這是用歷史資料驗證過、對這檔股票有實際優勢的分點,
    給其他訊號模組(例如權證分點加碼確認,見 xq_branch.match_significant_branches())當
    「已知準分點」的資料來源,取代主觀認定的清單(xq_branch.KNOWN_OVERNIGHT_FLIP_BRANCHES
    的舊做法)。

    樣本不足(win_rate_20d 是 None)的分點不會出現在這裡——是「還沒累積夠樣本判斷」,
    不是「驗證過沒用」,呼叫端不該把兩者混為一談。
    """
    rates = get_branch_win_rates_for_code(code, min_samples)
    if rates.empty or "win_rate_20d" not in rates.columns:
        return []
    qualified = rates[rates["win_rate_20d"].notna() & (rates["win_rate_20d"] >= win_rate_threshold)]
    return qualified["broker"].tolist()


def save_win_rate_summary(min_samples: int = MIN_SAMPLES_FOR_WIN_RATE) -> pd.DataFrame:
    """算一次完整的分點勝率排行,存成表格檔(`branch_history/branch_win_rates_summary.csv`)
    留下記錄——app.py以後可以直接讀這個檔案,不用每次重算。"""
    rates = get_branch_win_rates(min_samples)
    rates.to_csv(SUMMARY_PATH, index=False)
    return rates


if __name__ == "__main__":
    import sys

    codes = sys.argv[1:] or ["2330"]
    print(f"=== 對 {len(codes)} 檔股票執行分點快照累積:{codes} ===")
    new_count = ingest_watchlist_snapshot(codes)
    print(f"\n本次新增 {new_count} 筆買超記錄,歷史檔目前共 {len(_load_history())} 筆")

    print("\n=== 分點歷史勝率排行(需要達到最低樣本數才會顯示數字)===")
    print(get_branch_win_rates().head(20))
