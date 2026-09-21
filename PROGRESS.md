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

### 權證分點確認訊號改成「已驗證有效分點」(已完成,2026-09-21)

**決定**:`evaluate_long_signal()` 第三個條件(權證分點佐證)原本是「命中使用者手動指名的
`xq_branch.KNOWN_OVERNIGHT_FLIP_BRANCHES` 固定清單,沒命中就退回看買超金額排名第1的分點
(不管是哪個分點)」——使用者認為這樣不對:應該鎖定 `branch_win_rate.get_significant_broker_names(code)`
(用歷史買超後20日超額報酬勝率、對「這檔股票本身」驗證過的分點,不是全市場口徑、也不是主觀認定),
如果這些已驗證分點裡有任何一個同時在權證上買超,才算有意義的疊加確認訊號。不加總全部分點的買超金額。

**已完成的修改**:
- `branch_win_rate.py` 新增 `get_branch_win_rates_for_code(code)`(勝率只用這檔股票自己的歷史算,
  跟原本 `get_branch_win_rates()` 的全市場口徑分開)跟 `get_significant_broker_names(code)`
  (勝率門檻 `SIGNIFICANT_WIN_RATE_THRESHOLD = 0.6`,樣本數門檻沿用 `MIN_SAMPLES_FOR_WIN_RATE`)。
- `xq_branch.py` 新增 `match_significant_branches(code, ranking=None)`,回傳每個已驗證分點
  「各自」的權證淨買超(list of dict,不加總)。`_match_known_branch` 抽出共用的
  `_match_branch_in_list()`,兩套名單(舊的 KNOWN_OVERNIGHT_FLIP_BRANCHES / 新的已驗證分點)
  共用同一個名稱比對邏輯。
- `signals.py`:`evaluate_long_signal()` 移除 `BRANCH_TOP1_NET_BUY_THRESHOLD` 排名第1退回邏輯,
  改成 `branch_signal = bool(significant_buying)`;回傳欄位 `branch_known_hits` 改名
  `branch_significant_hits`。`branch_top1_broker`/`branch_top1_net_buy_wan` 保留在回傳值裡,
  純供顯示參考,不影響訊號判定。
- `app.py`(做多訊號分頁 sc3 區塊)改顯示「已驗證有效分點加碼權證」,沒有命中時顯示排名第1
  分點僅供參考、不計入條件。

**刻意沒動的部分**:`xq_branch.scan_known_branch_buying()`(🔎 分點掃描分頁,跨股票批次掃描)
還是用舊的固定清單 `KNOWN_OVERNIGHT_FLIP_BRANCHES`,沒有改成每檔股票各自查
`get_significant_broker_names()`——這個是「掃描哪些股票有已知大戶在買」的獨立功能,跟
`evaluate_long_signal()` 的單股訊號判定是分開的用途,還沒跟使用者確認要不要一起改。

**待辦**:
- [ ] 確認 🔎 分點掃描分頁(`scan_known_branch_buying`)要不要也改成用
      `get_significant_broker_names()`(每檔股票各自查,而不是共用一份固定清單)
- [ ] `SIGNIFICANT_WIN_RATE_THRESHOLD = 0.6` 是隨手定的門檻,還沒讓使用者確認合理性
- [ ] 目前沒有自動化測試覆蓋 `get_significant_broker_names`/`match_significant_branches`/
      `evaluate_long_signal` 的新邏輯,只在這次對話裡手動跑過驗證,沒有寫進 `tests/`

## 待補充的主題

(尚無其他主題討論記錄)
