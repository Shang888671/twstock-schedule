"""讀取 XQ 全球贏家匯出的「自選股清單」(.dsl)檔案。

.dsl 是微軟 OLE 複合文件格式(跟舊版 .doc/.xls 同一種容器格式,開頭是
`D0 CF 11 E0 A1 B1 1A E1` 的簽章),不是純文字檔,要用 `olefile` 讀取裡面的
`FileContentSymbolList_0` 串流,內容是 Big5 編碼的文字,格式大致是:

    1,<GUID>;<清單名稱>,<代號1>.TW,<代號2>.TW,...

代號可能是股票代號(數字),也可能夾雜非股票的分類標記(例如「OTC」),
所以只保留看起來像股票代號的項目(純數字,4~6 碼)。
"""

import re
from pathlib import Path

import olefile

_SYMBOL_RE = re.compile(r"^\d{4,6}$")


def get_stock_futures_codes_from_watchlists(directory=None) -> set:
    """從指定資料夾(預設 `xq_branch_data/`)底下所有 `.dsl` 自選股清單檔讀取股票代號,合併成一個集合。

    使用者用這份自己維護的清單當「個股期貨標的」範圍,取代 `stock_futures.py`
    抓 TAIFEX 官方清單的方式——比較貼近使用者實際交易的股票範圍,不是全市場的個股期貨標的。
    有多個 .dsl 檔案時會全部合併(聯集)。
    """
    from xq_branch import XQ_BRANCH_DIR

    directory = Path(directory) if directory else XQ_BRANCH_DIR
    codes = set()
    for path in directory.glob("*.dsl"):
        result = read_watchlist_codes(str(path))
        codes.update(result["codes"])
    return codes


def read_watchlist_codes(path: str) -> dict:
    """讀取一個 .dsl 自選股清單檔,回傳 {"name": 清單名稱, "codes": [股票代號, ...]}。"""
    ole = olefile.OleFileIO(path)
    try:
        stream_name = next((s for s in ole.listdir() if s[0].startswith("FileContentSymbolList")), None)
        if stream_name is None:
            return {"name": None, "codes": []}
        raw = ole.openstream(stream_name).read()
    finally:
        ole.close()

    text = raw.decode("big5", errors="replace")
    header, _, rest = text.partition(";")
    list_name, _, symbols_part = rest.partition(",")

    codes = []
    for token in symbols_part.split(","):
        token = token.strip()
        code = token.split(".")[0] if "." in token else token
        if _SYMBOL_RE.match(code):
            codes.append(code)

    return {"name": list_name.strip() or None, "codes": codes}


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "xq_branch_data/期貨總檔1數.dsl"
    result = read_watchlist_codes(path)
    print(f"清單名稱:{result['name']}")
    print(f"共 {len(result['codes'])} 檔:{result['codes']}")
