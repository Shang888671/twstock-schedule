# PROGRESS.md

給 Claude Code 用的「討論進度」記錄——跟 `CLAUDE.md`(記錄專案架構,幾乎不變)不同,
這份檔案記錄每個主題目前討論到哪、做了什麼決定、還有什麼待辦。每次工作階段結束前,
請 Claude 把新的結論/待辦補進對應主題(或新增主題),下次開新 session 時先讀這份檔案。

## 權證資金流訊號

**背景**:`chip_data.py` 裡權證資料抓取有兩種模式——
- 逐股(`get_warrant_flow`):每天都重新打一次全市場權證表,再過濾單一股票,適合單股查詢。
- 批次(`get_warrant_large_trade_counts_multi`):每天只打一次全市場表,一次切給多檔股票,適合掃描多檔。

`signals.py` 的長訊號規則(三大法人買超趨勢 + 認購權證單筆大額成交 + 選用的 XQ 分點佐證)
已經在用批次路徑算多股訊號,但 `get_warrant_flow`(算 put/call 比率用的)還是逐股版本。

**目前狀態**:2026-09-21 討論時只確認到這個效能落差,還沒決定要不要動手改、或改的優先度。

**待辦(尚未決定/尚未動工)**:
- [ ] 決定 `get_warrant_flow` 是否也要改成批次抓取(如果之後要對多檔股票算 put/call 比率,才有必要)
- [ ] 如果要改,注意 `warrant_flow` 表是逐股 upsert(`upsert_warrant_flow` 一次只收一檔的 DataFrame),批次版要嘛照 `warrant_large_trade` 的做法直接組 SQL 一次寫多檔,要嘛保留現有 upsert 介面、外層迴圈呼叫

## 待補充的主題

(尚無其他主題討論記錄)
