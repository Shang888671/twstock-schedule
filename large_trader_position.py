"""期交所「大額交易人未沖銷部位結構表」——揭露台指期貨(TX,含TX/MTX/TMF折算)
「前5大交易人」、「前10大交易人」未沖銷部位(買方/賣方)合計數,同一份資料裡另外拆
一組「交易人類別=1(特定法人)」的子集,是`foreign_option_position.py`(外資臺指
選擇權合成多空部位,已驗證net_ratio對後續報酬有顯著預測力)方法論的延伸,同一類
「大玩家倉位集中度」訊號,但換一個商品面向。

**2026-09-21:端點/欄位/TX實際列形狀都已經用使用者本機實測確認過,不是猜測**:

    POST https://www.taifex.com.tw/cht/3/largeTraderFutDown
    form-data: queryStartDate=YYYY/MM/DD, queryEndDate=YYYY/MM/DD
    (commodityId參數帶不帶結果一樣,這個端點回傳全市場所有商品當天的資料,不會用
    commodityId篩選,要自己在拿到CSV之後篩;還沒測過queryStartDate!=queryEndDate
    的多日範圍查詢會不會一次回傳多天,`backfill_history()`先用逐日迴圈,穩妥優先)

回傳的是MS950(等同big5)編碼的CSV純文字,欄位(逐字照抄真實回應的表頭):

    日期, 商品(契約), 商品名稱(契約名稱), 到期月份(週別), 交易人類別,
    前五大交易人買方, 前五大交易人賣方, 前十大交易人買方, 前十大交易人賣方,
    全市場未沖銷部位數

CSV最後常附一行不是資料、是圖例說明(位置不固定),`_parse_raw()`用`date`欄位是否
符合`YYYY/MM/DD`格式過濾掉,不是靠行數/位置判斷。「到期月份(週別)」編碼規則
(圖例文字本身確認的):`666666`=所有週到期契約合計、`yyyymm`=近月契約、`999999`=
所有契約(含各週到期契約與各到期月份契約)合計。

**TX實際列的形狀(2026-09-18實測,`商品名稱`是"臺股期貨(TX+MTX/4+TMF/20)")**:
`999999`(全部契約)這一列,`交易人類別=1`(特定法人)跟`=0`(全部大戶)的
top5_buy/top5_sell/top10_buy/top10_sell/total_oi**完全相同**——代表台指期貨
前5大/10大大戶部位100%都是特定法人持有,沒有非法人擠進前十大。`666666`(週契約)
那一列反過來,特定法人全部是0、全部大戶部位是個小數字——代表週契約的前5/10大反而
100%是非法人持有。這個「近月+總計=法人主導、週約=非法人主導」的對比本身就是這份
資料的觀察重點之一,不是解析錯誤,`_compute_tx_position()`把這兩個層級都保留
(用`999999`當跟`foreign_option_position.py`同等級的「整體部位」指標,`666666`
另外存但不當主指標)。

**這份資料能分辨什麼(使用者2026-09-21問「還有什麼方法抓聰明錢」時討論的方向)**:
不是看外資身分別的期貨/選擇權淨額(那是`foreign_option_position.py`已經驗證的),
是看「前5大/前10大交易人佔全市場未沖銷量的比例」,量的是集中度本身,不是方向。
用法通常是:集中度越高+大額交易人是站多方,代表少數大戶主導盤面、方向訊號更可信;
集中度高但多空交易人互相對咬,代表籌碼對峙,方向訊號要打折。這是獨立的假說,
還沒驗證,不能直接假設跟`foreign_option_position.py`已經證明有效的net_ratio一樣
有效——要backfill累積歷史、寫`backtest_large_trader_position.py`驗證過才能接進
`signals.py`/`app.py`。

**還沒確認的**:這份查詢工具本身能回溯到多早(`foreign_option_position.py`的
`callsAndPutsDateDown`實測發現查詢工具本身另外限制約3年,不是官方資料庫真正的
極限,這份報表有沒有類似限制還沒測過)。`backfill_history()`遇到查詢失敗只會跳過
該日繼續,不會整個中斷,累積過程中如果連續跳過一大段,代表可能踩到某個回溯上限,
要回報實際觀察到的邊界,不要事先假設一個日期常數。

用法:
    python large_trader_position.py probe 2026/09/18        # 看全市場原始CSV前幾行
    python large_trader_position.py show_tx 2026/09/18      # 只篩TX,確認實際列形狀
    python large_trader_position.py backfill 2024-01-02      # 從指定日期開始逐日backfill
    python large_trader_position.py update                  # 每日排程用,只補最新缺的幾天
"""

import sys
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from database import init_db, upsert_large_trader_position, get_large_trader_position

LARGE_TRADER_URL = "https://www.taifex.com.tw/cht/3/largeTraderFutDown"

_COLUMN_MAP = {
    "日期": "date",
    "商品(契約)": "commodity_id",
    "商品名稱(契約名稱)": "commodity_name",
    "到期月份(週別)": "contract_month",
    "交易人類別": "trader_category",
    "前五大交易人買方": "top5_buy",
    "前五大交易人賣方": "top5_sell",
    "前十大交易人買方": "top10_buy",
    "前十大交易人賣方": "top10_sell",
    "全市場未沖銷部位數": "total_oi",
}

ALL_CONTRACTS_MARKER = "999999"  # 「到期月份(週別)」裡代表「所有契約月份加總」的彙總列
WEEKLY_CONTRACTS_MARKER = "666666"
TX_COMMODITY_ID = "TX"
ALL_TRADERS = 0
SPECIFIC_INSTITUTIONAL = 1

CSV_SNAPSHOT_PATH = Path(__file__).parent / "branch_history" / "large_trader_position_history.csv"


class FetchFailed(Exception):
    """打端點失敗或回應不是預期的CSV格式(HTML錯誤頁、逾時等)——backfill_history()
    接住後跳過該日繼續,不整個中斷。"""


def _fetch_day_raw_text(date_str: str) -> str:
    """POST查一天,回傳解碼後的CSV純文字(見檔頭確認過的端點/方法/編碼)。"""
    resp = requests.post(
        LARGE_TRADER_URL,
        files={"queryStartDate": (None, date_str), "queryEndDate": (None, date_str)},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.content.decode("big5", errors="replace")
    if text.lstrip().startswith("<!DOCTYPE") or "<html" in text[:200].lower():
        raise FetchFailed(f"{date_str} 查詢失敗(回傳錯誤頁,可能超出查詢工具可查範圍或非交易日)")
    return text


def _parse_raw(text: str) -> pd.DataFrame:
    """把CSV文字轉成dataframe,欄位改成英文名稱、去除定寬空白,濾掉圖例說明行
    (見檔頭說明,用`date`欄位格式判斷,不是靠行數/位置)。"""
    df = pd.read_csv(StringIO(text), dtype=str)
    df = df.rename(columns=_COLUMN_MAP)
    df = df[df["date"].str.match(r"^\d{4}/\d{2}/\d{2}$", na=False)].copy()
    if df.empty:
        return df
    for col in ["commodity_id", "commodity_name", "contract_month"]:
        df[col] = df[col].str.strip()
    for col in ["trader_category", "top5_buy", "top5_sell", "top10_buy", "top10_sell", "total_oi"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%Y/%m/%d")
    return df


def fetch_day(date_str: str) -> pd.DataFrame:
    """查一天,回傳全市場所有商品的解析後dataframe(還沒篩TX)。"""
    return _parse_raw(_fetch_day_raw_text(date_str))


def _compute_tx_position(raw: pd.DataFrame) -> pd.DataFrame:
    """從一天的全市場dataframe篩出TX、算成一列(見檔頭「TX實際列的形狀」說明)。
    只用`999999`(全部契約)這個彙總層級當整體部位指標,跟`foreign_option_position.py`
    的「當天整體部位」概念對齊——`666666`(週契約)另外觀察到的「非法人主導」現象
    很有意思但不是這裡的主指標,先不用。輸入可能是多天的資料(如果之後真的驗證
    多日範圍查詢有效),依`date`分組各算一列。"""
    tx = raw[(raw["commodity_id"] == TX_COMMODITY_ID) & (raw["contract_month"] == ALL_CONTRACTS_MARKER)]
    if tx.empty:
        return pd.DataFrame(columns=[
            "date", "top5_buy", "top5_sell", "top10_buy", "top10_sell", "total_oi",
            "inst_top5_buy", "inst_top5_sell", "inst_top10_buy", "inst_top10_sell",
        ])

    rows = []
    for d, group in tx.groupby("date"):
        all_row = group[group["trader_category"] == ALL_TRADERS]
        inst_row = group[group["trader_category"] == SPECIFIC_INSTITUTIONAL]
        if all_row.empty:
            continue
        all_row = all_row.iloc[0]
        rows.append({
            "date": d,
            "top5_buy": all_row["top5_buy"], "top5_sell": all_row["top5_sell"],
            "top10_buy": all_row["top10_buy"], "top10_sell": all_row["top10_sell"],
            "total_oi": all_row["total_oi"],
            "inst_top5_buy": inst_row.iloc[0]["top5_buy"] if not inst_row.empty else None,
            "inst_top5_sell": inst_row.iloc[0]["top5_sell"] if not inst_row.empty else None,
            "inst_top10_buy": inst_row.iloc[0]["top10_buy"] if not inst_row.empty else None,
            "inst_top10_sell": inst_row.iloc[0]["top10_sell"] if not inst_row.empty else None,
        })
    return pd.DataFrame(rows)


def backfill_history(start: date, end: date | None = None, checkpoint_every: int = 20) -> pd.DataFrame:
    """逐日backfill(還沒驗證多日範圍查詢有沒有用,穩妥優先用逐日迴圈,見檔頭說明)。
    遇到單日查詢失敗(FetchFailed)只印訊息跳過,不中斷整段backfill——如果連續跳過
    一大段,可能代表踩到查詢工具本身的回溯上限,要把觀察到的實際邊界回報出來。"""
    end = end or date.today()
    init_db()
    frames = []
    d = start
    fetched_since_checkpoint = 0
    while d <= end:
        date_str = d.strftime("%Y/%m/%d")
        try:
            raw = fetch_day(date_str)
        except (FetchFailed, requests.RequestException) as e:
            print(f"[large_trader_position]   {date_str} 跳過({e})")
            d += timedelta(days=1)
            continue
        computed = _compute_tx_position(raw)
        if not computed.empty:
            print(f"[large_trader_position] {date_str} -> total_oi={computed.iloc[0]['total_oi']}")
            frames.append(computed)
            fetched_since_checkpoint += 1
            if fetched_since_checkpoint >= checkpoint_every:
                upsert_large_trader_position(pd.concat(frames, ignore_index=True))
                save_csv_snapshot(get_history())
                fetched_since_checkpoint = 0
        d += timedelta(days=1)

    if frames:
        upsert_large_trader_position(pd.concat(frames, ignore_index=True))
        save_csv_snapshot(get_history())
    return get_history(start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"))


def update_latest() -> int:
    """每日排程用:只補本機SQLite最後一筆之後到今天的新資料。回傳新增天數。

    **這裡跟`foreign_option_position.update_latest()`故意不一樣**:那份資料查詢
    工具支援多年範圍一次查完,SQLite是空的時候直接退回`EARLIEST_AVAILABLE_DATE`
    重跑整段backfill也還算輕量(分年請求,3年約幾次請求)。這份大額交易人報表是
    **逐日迴圈**(還沒驗證多日範圍查詢有效,見檔頭說明),完整backfill是幾百次
    請求——不適合在每日排程裡「悄悄」跑一次全歷史backfill(fly.io換過機器、
    本機SQLite是空的時候,不能自動觸發這麼重的操作,可能拖垮排程或洗爆TAIFEX)。
    SQLite是空的話這裡選擇**跳過、印警告**,不嘗試backfill,也不`raise`(不能讓
    這個可選訊號的缺失中斷`daily_update.py`裡其他不相關的步驟)——初次建立歷史
    要人工執行一次`python large_trader_position.py backfill <start>`。"""
    init_db()
    existing = get_large_trader_position()
    if existing.empty:
        print("[large_trader_position] 本機SQLite還沒有歷史資料(可能是新機器/新環境)"
              "——這個每日更新只補「最新缺的幾天」,不會自動觸發整段backfill(逐日迴圈"
              "太重,不適合塞進每日排程)。要建立歷史,手動跑一次`python "
              "large_trader_position.py backfill <start>`。")
        return 0
    last_date = existing["date"].max().date()
    start = last_date + timedelta(days=1)
    today = date.today()
    if start > today:
        return 0
    before = len(get_history())
    backfill_history(start=start, end=today)
    return len(get_history()) - before


def _add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """從原始的buy/sell/total_oi欄位算出`backtest_large_trader_position.py`
    驗證過的衍生指標,集中在這裡算一次,`get_history()`的呼叫端(app.py、
    classify_current_reading())都不用各自重複同一套公式。"""
    if df.empty:
        return df
    df = df.copy()
    df["top5_net_ratio"] = (df["top5_buy"] - df["top5_sell"]) / (df["top5_buy"] + df["top5_sell"])
    df["top10_net_ratio"] = (df["top10_buy"] - df["top10_sell"]) / (df["top10_buy"] + df["top10_sell"])
    df["top10_net"] = df["top10_buy"] - df["top10_sell"]
    df["concentration_top10"] = (df["top10_buy"] + df["top10_sell"]) / (2 * df["total_oi"])
    return df


def get_history(start_date: str = "2000-01-01", end_date: str | None = None) -> pd.DataFrame:
    """優先讀SQLite,查無資料退回git-tracked的CSV快照(跟foreign_option_position.py
    同一套備援邏輯),回傳前先套用`_add_derived_columns()`。"""
    try:
        init_db()
        df = get_large_trader_position(start_date, end_date or date.today().strftime("%Y-%m-%d"))
        if not df.empty:
            return _add_derived_columns(df)
    except Exception:
        pass
    return _add_derived_columns(load_csv_snapshot())


def classify_current_reading(history: pd.DataFrame | None = None) -> dict | None:
    """把最新一天的`top10_net_ratio`(前十大交易人多空淨傾向)分類成多/空,給app.py
    的判斷分析文案用。

    **依據**(2026-09-21,`backtest_large_trader_position.py`,2024-01-02~
    2026-09-21共654個交易日):四個候選指標(`top5_net_ratio`/`top10_net_ratio`/
    `concentration_top10`/`inst_net_ratio`)裡`top10_net_ratio`最強最一致,0軸
    二分法20日窗格高組p=1.2e-6、低組p=3.3e-4。額外做過穩健性檢驗
    (`evaluate_controlling_for_trend()`):先用^TWII自己近20日走勢切三分位控制掉
    「是不是已經在漲」,在低/中/高動能三個分位「內部」重新測,20日窗格的高組
    (net_ratio>=0)在三個分位裡全部仍顯著優於該分位自己的基準(p=0.028/0.016/
    0.0000)——不是純粹在追蹤大盤趨勢的偽裝。5日/10日窗格控制後證據較弱較零散,
    這裡的分類**只代表20日方向確認的可信度**,不代表5/10日窗格同等可靠。

    **這個訊號目前是獨立顯示,還沒接進`signals.py:evaluate_long_signal()`的
    tier系統**(2026-09-21使用者決定先獨立觀察,不急著疊加進既有確認邏輯)。

    **重要但書**(跟`foreign_option_position.classify_current_reading()`同一個
    問題,而且是同一段期間):這654個交易日整體基準勝率63%~74%,是一段強多頭期間,
    不能完全排除跟`foreign_option_position.py`一樣「這段關係只是跟多頭走勢同步」
    的可能——穩健性檢驗只能排除「單純是近20日走勢的偽裝」,不能排除跨越多個市場
    循環(含空頭/盤整)後這個關係是否依然成立,那需要更長的歷史才能驗證。"""
    if history is None:
        history = get_history()
    if history.empty:
        return None

    history = history.sort_values("date").copy()
    latest = history.iloc[-1]
    ratio = latest["top10_net_ratio"]
    percentile = float((history["top10_net_ratio"] < ratio).mean() * 100)

    if ratio >= 0:
        label, tone = "偏多", "bull"
    else:
        label, tone = "偏空", "bear"

    return {
        "date": latest["date"],
        "top10_net_ratio": float(ratio),
        "percentile": percentile,
        "label": label,
        "tone": tone,
        "top10_buy": float(latest["top10_buy"]),
        "top10_sell": float(latest["top10_sell"]),
        "top10_net": float(latest["top10_net"]),
    }


def load_csv_snapshot() -> pd.DataFrame:
    if not CSV_SNAPSHOT_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(CSV_SNAPSHOT_PATH, parse_dates=["date"])


def save_csv_snapshot(history: pd.DataFrame) -> None:
    CSV_SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
    history.to_csv(CSV_SNAPSHOT_PATH, index=False)
    print(f"[large_trader_position] 快照已存到 {CSV_SNAPSHOT_PATH}({len(history)} 筆)")


def probe_raw(date_str: str) -> None:
    """探測用:印出原始CSV文字前1500字,不解析。"""
    text = _fetch_day_raw_text(date_str)
    print(f"回應前1500字:\n{text[:1500]}")


def show_tx(date_str: str) -> None:
    """探測用:抓一天資料,只篩commodity_id=='TX'的列印出來。"""
    df = fetch_day(date_str)
    tx = df[df["commodity_id"] == TX_COMMODITY_ID]
    if tx.empty:
        print(f"{date_str}沒有找到commodity_id=='{TX_COMMODITY_ID}'的列,"
              f"實際出現的commodity_id有:{sorted(df['commodity_id'].unique())[:30]}...")
        return
    print(tx.to_string(index=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python large_trader_position.py probe <YYYY/MM/DD>")
        print("      python large_trader_position.py show_tx <YYYY/MM/DD>")
        print("      python large_trader_position.py backfill <YYYY-MM-DD> [YYYY-MM-DD]")
        print("      python large_trader_position.py update")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "probe":
        probe_raw(sys.argv[2])
    elif cmd == "show_tx":
        show_tx(sys.argv[2])
    elif cmd == "backfill":
        start_arg = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
        end_arg = datetime.strptime(sys.argv[3], "%Y-%m-%d").date() if len(sys.argv) > 3 else None
        history = backfill_history(start=start_arg, end=end_arg)
        print(f"\n共 {len(history)} 個交易日")
        print(history.tail(5).to_string(index=False))
    elif cmd == "update":
        n = update_latest()
        print(f"新增 {n} 個交易日")
    else:
        print(f"不認得的指令: {cmd}")
        sys.exit(1)
