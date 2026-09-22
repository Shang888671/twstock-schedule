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

## 聰明錢資金流訊號(外資選擇權 / 大額交易人)

**背景**:這個主題原本是在另一個私有 repo `stock-model`(使用者本機的另一個 clone)
討論並完成驗證的——2026-09-21 使用者問「還有什麼方法抓聰明錢資金流」,討論出兩個新方向
並依序完成:①外資臺指選擇權合成多空部位(`foreign_option_position.py`)②台指期貨大額
交易人未沖銷部位結構(`large_trader_position.py`),兩個都已經用真實資料 backtest 過、
證明對加權指數後續報酬有顯著預測力,也接進了 `stock-model` 的 `daily_update.py`/`app.py`。

2026-09-22 在 `twstock-schedule`(這個 repo,雲端 session 綁定的分支)開新 session 繼續同一個
話題時,發現 `twstock-schedule` 跟 `stock-model` 是兩個真實存在的不同 GitHub repo(不是同一個
東西的兩個名字),`twstock-schedule` 完全沒有這兩個模組——使用者確認後選擇「把已驗證的模組
搬進 twstock-schedule」,而不是切換到 stock-model 繼續。這裡記錄搬移的內容跟決定,不重複
`stock-model` 那邊已經做過的驗證過程(那邊的回測方法論、p-value、穩健性檢驗結果照抄過來,
不重新推導,避免浪費 token)。

**已完成的搬移(2026-09-22)**:
- 新增 `foreign_option_position.py`、`large_trader_position.py`(從 `stock-model` 原封不動複製,
  兩者都只依賴 `database.py` 的 upsert/get helper,不需要改動邏輯)。
- 新增 `backtest_foreign_option_position.py`、`backtest_large_trader_position.py`(回測腳本,
  同樣原封不動複製,保留可重現性——如果之後要用更長歷史重新驗證,不用重寫)。
- `database.py`:新增 `foreign_option_position`/`large_trader_position` 兩個表 + 對應的
  `upsert_*`/`get_*` 函式(照抄 stock-model 的定義)。**順手修了一個這個 repo 也有的舊 bug**:
  `_get_conn()` 原本沒有 `DATA_DIR.mkdir(exist_ok=True)`,全新環境(沒人先建過 `data_cache/`)
  第一次呼叫會直接 `sqlite3.OperationalError: unable to open database file`——這個 bug 跟
  `stock-model` 那邊修過的是同一個問題,這裡也補上。
- `daily_update.py`:新增 `update_foreign_option_position()`/`update_large_trader_position()`
  (retry-decorated,跟其他 `update_*` 函式同一套模式),`main()` 裡在 `update_margin(today)`
  之後呼叫。**注意這兩個資料源的 `update_latest()` 行為不對稱**(照抄自 stock-model 的檔頭說明):
  `foreign_option_position` 的官方查詢工具支援日期範圍回溯,SQLite 是空的話會直接退回
  `EARLIEST_AVAILABLE_DATE`(有查詢工具本身約3年的回溯限制)重跑;`large_trader_position`
  是逐日迴圈,SQLite 空的話只印警告、跳過(回傳0),不會自動觸發整段 backfill——初次要人工
  執行 `python large_trader_position.py backfill <start>`。
- `requirements.txt`:加 `scipy`(backtest 腳本用 `scipy.stats.binomtest`;production 的兩個
  模組本身不需要)。
- `Dockerfile`/`.dockerignore`:白名單模式,補上 `foreign_option_position.py`/
  `large_trader_position.py`(`daily_update.py` 現在會 import 這兩個,fly.io 的容器也要有)。
- `app.py`:新增兩個分頁「📐 外資選擇權」「🐘 大額交易人」(跟 stock-model 的做法一致,顯示
  0軸多空分類 + 歷史百分位 + 完整回測數字/但書說明 + lightweight-charts 折線+柱狀圖),
  共用既有的 `CHART_THEME`/`components`/`json` 基礎設施,只新增 `_OPTION_POSITION_CHART_TEMPLATE`
  跟 `_build_option_position_chart_html()`(這個 helper 兩個分頁共用,原本 stock-model 版本的
  圖例文字寫死「合成多単/合成空単」,這裡順手改成通用的「多方/空方」,避免大額交易人分頁
  借用時文字對不上)。**這個訊號目前是獨立顯示,沒有接進 `signals.py` 的做多訊號 tier
  系統**——跟 stock-model 那邊同一個決定,先觀察一段時間。
  IS_INDEX 模式(加權指數/櫃買指數)一樣在 `st.stop()` 之後不會顯示這兩個分頁,跟
  stock-model 的行為一致。

**回測結論(照抄自 stock-model,同一段真實資料算出來的,沒有重跑)**:
- `foreign_option_position` 的 `net_ratio`(0軸二分法):20日窗格多89.6% vs 空64.0%(p=7e-10)、
  10日77.6% vs 61.2%(p=2.9e-4)、5日70.8% vs 58.4%(p=6.6e-3)。
- `large_trader_position` 的 `top10_net_ratio`(0軸二分法,2024-01-02~2026-09-21共654個交易日):
  20日窗格高組p=1.2e-6、低組p=3.3e-4,最強最一致;`top5_net_ratio`較弱;`inst_net_ratio`
  跟`top5_net_ratio`高度共線;`concentration_top10`證據最弱不建議用。額外做過穩健性檢驗
  (用^TWII近20日走勢切三分位控制趨勢,分位內部重測),20日窗格在低/中/高動能三分位都仍顯著
  (p=0.028/0.016/0.0000)。
- **共同但書**:兩者的回測期間是同一段強多頭期間(母體基準勝率本身63~74%),測的是^TWII
  自己的原始報酬(不是超額報酬vs大盤),沒辦法排除「這個關係只是跟這段多頭走勢同步」的可能——
  穩健性檢驗只排除了「單純是近期走勢的偽裝」,不能排除跨越空頭/盤整循環後是否依然成立。

**順手修的兩個既有 bug(跟這個主題無關,但擋住了在這個雲端 sandbox 裡驗證新分頁能不能正常
渲染)**:這個 sandbox 對外網路整個被擋(TWSE/TAIFEX/yfinance 都連不到),用 Playwright 打開
本機跑起來的 `streamlit run app.py` 想確認兩個新分頁會不會正常顯示時,先後撞到兩個舊的
`app.py` bug(跟 `stock-model` 那邊之前修過的是同一個問題,這個 repo 之前沒修過):
1. 個股/加權指數報價卡(~1805行):`quote["last_price"]`/`quote["previous_close"]` 沒用
   `.get()`,MIS 跟 yfinance 都抓不到時整個腳本崩潰(`KeyError`),連帶讓後面所有分頁都無法
   渲染。改成 `last_price is None` 時顯示 `st.warning(...)`,其他欄位一律用 `.get()` + `——`
   後備值。
2. `_render_chart_section()`(~1997行):`df["Close"].iloc[-1]` 在歷史價格完全抓不到(df為空)
   時崩潰(`IndexError`)。加上 `if df.empty: st.warning(...); return` 提早離開。
   兩個修完後用 Playwright 實測確認:報價卡優雅顯示警告文字、K線區塊優雅顯示警告文字、
   新的「外資選擇權」「大額交易人」兩個分頁都正常渲染(空資料庫時顯示「尚未累積...」的
   提示訊息跟正確的 caption 文字,沒有 crash)。

**待辦**:
- [ ] 本機/正式環境需要手動跑一次 backfill 才會有資料:
      `python foreign_option_position.py --backfill 2023-01-01`、
      `python large_trader_position.py backfill 2024-01-02`
- [ ] `block_trade_flow.py`(盤後大宗交易/鉅額交易,stock-model 那邊討論的方向①)完全沒有
      驗證過(端點/欄位都沒有實測成功過),這次沒有搬——如果之後要做,要先在有網路的環境
      重新探測,不能直接假設跟 `large_trader_position.py` 一樣的 TAIFEX 端點慣例套得上。
- [ ] `app.py` 裡還有其他幾處跟上面「順手修的bug」同一個模式的未防護 `quote[...]`/
      `otc_quote[...]`/`current_quote[...]` 存取(上櫃股票報價卡、台指期夜盤報價卡、
      櫃買指數報價卡等),這次只碰到「個股/加權指數」這條路徑,其他路徑沒有逐一檢查修過,
      是否要一次補齊還沒問過使用者。

## 待補充的主題

(尚無其他主題討論記錄)
