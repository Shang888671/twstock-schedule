"""讀取使用者從 XQ 全球贏家手動匯出的「權證券商(分點)進出」CSV。

這份資料**不是自動抓取的**——TWSE 官方的分點進出查詢系統(bsr.twse.com.tw)每次查詢都要輸入
圖形驗證碼,無法用程式自動繞過(也不該繞過)。所以改成:使用者自己在 XQ 裡匯出
「個股進階籌碼 → 權證券商 → 買方 TOP 15 → 類型:認購 → 排序:買賣超金額」的 CSV,
存到 `xq_branch_data/` 資料夾,檔名開頭是股票代號(例如「6770權證分點.csv」),
這個模組負責讀取、解析成 DataFrame。**這是使用者手動維護的參考資料,不會即時更新**,
呼叫端應該顯示資料的日期區間,讓使用者自己判斷是否過期。

已知檔案格式(XQ 匯出的 CSV 是 Big5 編碼,不是 UTF-8):
第1行:標題,含日期區間,例如
  「個股進階籌碼-權證券商-2026/09/11~2026/09/11-篩選:買賣超金額-類型:認購-買方 TOP 15-單位: 萬元」
第2行:欄位名稱「序,券商名稱,買賣超金額,買進金額,賣出金額,損益」
第3行起:資料列。買賣超金額/買進金額/賣出金額單位是「萬元」,損益是百分比(不是金額)。

已知「隔日沖大戶」分點(使用者自己的經驗判斷,TWSE/XQ 都沒有官方標籤,
是使用者根據自己的交易經驗指名的分點清單——之後使用者可能會再增減這份名單):
凱基-城中、永豐金-市政、富邦-台南、凱基-岡山、凱基-三重、凱基-高雄。
"""

import csv
import re
from pathlib import Path

import pandas as pd

XQ_BRANCH_DIR = Path(__file__).parent / "xq_branch_data"
XQ_BRANCH_DIR.mkdir(exist_ok=True)

KNOWN_OVERNIGHT_FLIP_BRANCHES = [
    "凱基-城中",
    "永豐金-市政",
    "富邦-台南",
    "凱基-岡山",
    "凱基-三重",
    "凱基-高雄",
]


def _normalize_branch_name(name: str) -> str:
    """把分點名稱裡常見的破折號(—/–/－)統一成半形「-」,並去掉空白,方便比對。

    XQ 匯出的名稱可能是「富邦-敦南      (9663)」(全形空白對齊+半形連字號),
    使用者手動輸入的名稱可能用不同的破折號字元,所以查詢前都先正規化。
    """
    name = name.strip()
    for dash in ("—", "–", "－", "ー"):
        name = name.replace(dash, "-")
    return re.sub(r"\s+", "", name)


def _match_known_branch(broker_name: str) -> str | None:
    normalized = _normalize_branch_name(broker_name)
    for known in KNOWN_OVERNIGHT_FLIP_BRANCHES:
        if _normalize_branch_name(known) in normalized:
            return known
    return None


def _parse_date_range(title: str):
    m = re.search(r"(\d{4}/\d{2}/\d{2})~(\d{4}/\d{2}/\d{2})", title)
    return (m.group(1), m.group(2)) if m else (None, None)


def read_call_warrant_broker_ranking(code: str):
    """讀取 `xq_branch_data/` 底下檔名開頭是 code 的 CSV,回傳解析結果的 dict。

    找不到檔案回傳 None——呼叫端應該把這個情況當作「使用者還沒提供這份資料,略過此條件」,
    不是錯誤。
    """
    candidates = sorted(XQ_BRANCH_DIR.glob(f"{code}*.csv"))
    if not candidates:
        return None
    path = candidates[-1]

    text = path.read_bytes().decode("big5", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return None

    date_start, date_end = _parse_date_range(lines[0])

    rows = []
    for line in lines[2:]:
        try:
            parts = next(csv.reader([line]))
        except StopIteration:
            continue
        if len(parts) < 6:
            continue
        rank, broker, net_buy, buy_amt, sell_amt, pnl = parts[:6]
        try:
            rows.append(
                {
                    "rank": int(rank),
                    "broker": broker.strip(),
                    "net_buy_wan": float(net_buy),
                    "buy_wan": float(buy_amt),
                    "sell_wan": float(sell_amt),
                    "pnl_pct": float(pnl),
                }
            )
        except ValueError:
            continue

    if not rows:
        return None

    df = pd.DataFrame(rows).sort_values("rank").reset_index(drop=True)
    df["known_overnight_flip_branch"] = df["broker"].map(_match_known_branch)
    top1 = df.iloc[0]

    known_hits = df[df["known_overnight_flip_branch"].notna()]
    known_branch_matches = [
        {
            "known_name": row["known_overnight_flip_branch"],
            "broker": row["broker"],
            "net_buy_wan": float(row["net_buy_wan"]),
        }
        for _, row in known_hits.iterrows()
    ]

    return {
        "file": path.name,
        "date_start": date_start,
        "date_end": date_end,
        "table": df,
        "top1_broker": top1["broker"],
        "top1_net_buy_wan": float(top1["net_buy_wan"]),
        "top1_net_buy_twd": float(top1["net_buy_wan"]) * 10_000,
        "known_branch_matches": known_branch_matches,
    }


def scan_known_branch_buying(stock_futures_only: bool = False):
    """掃描 `xq_branch_data/` 裡「所有」已匯出的分點檔案,找出目前有已知隔日沖大戶分點
    在買超認購權證的股票。

    只回傳「有命中且買超為正」的股票——沒有分點檔案、或有檔案但沒命中已知分點的股票
    不會出現在結果裡。預設(`stock_futures_only=False`)不限制股票範圍,資料夾裡只要有
    分點 CSV 檔的股票都會納入掃描——使用者確認過不想被「個股期貨標的」自選清單(.dsl)
    限制,匯出了哪些股票的分點資料就掃哪些。`stock_futures_only=True` 保留給之後如果
    想改回只看個股期貨標的時使用(`xq_branch_data/*.dsl`),目前沒有呼叫端在用。
    """
    codes = set()
    for path in XQ_BRANCH_DIR.glob("*.csv"):
        m = re.match(r"(\d{4,6})", path.stem)
        if m:
            codes.add(m.group(1))

    excluded_non_futures = []
    if stock_futures_only:
        from xq_watchlist import get_stock_futures_codes_from_watchlists

        futures_codes = get_stock_futures_codes_from_watchlists()
        excluded_non_futures = sorted(codes - futures_codes)
        codes = codes & futures_codes

    hits = []
    for code in sorted(codes):
        result = read_call_warrant_broker_ranking(code)
        if result is None:
            continue
        buying_hits = [h for h in result["known_branch_matches"] if h["net_buy_wan"] > 0]
        if buying_hits:
            hits.append(
                {
                    "code": code,
                    "known_branch_hits": buying_hits,
                    "asof": result["date_end"],
                    "file": result["file"],
                }
            )
    return hits, excluded_non_futures


if __name__ == "__main__":
    result = read_call_warrant_broker_ranking("6770")
    if result is None:
        print("沒有找到對應的分點資料檔案")
    else:
        print(f"檔案:{result['file']},資料日期:{result['date_start']}~{result['date_end']}")
        print(f"第1名分點:{result['top1_broker']},買超金額:{result['top1_net_buy_wan']} 萬元")
        print(result["table"])
        print("\n已知隔日沖大戶分點命中:")
        print(result["known_branch_matches"] or "(這次資料裡沒有出現)")

    print("\n=== 掃描所有分點檔案(只看個股期貨標的)===")
    hits, excluded = scan_known_branch_buying()
    if not hits:
        print("目前沒有任何股票命中已知隔日沖大戶分點買超")
    for h in hits:
        print(h)
    if excluded:
        print(f"\n(已略過 {len(excluded)} 檔沒有個股期貨標的的股票:{excluded})")
