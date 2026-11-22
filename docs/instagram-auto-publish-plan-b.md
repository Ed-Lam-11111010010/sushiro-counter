# Instagram 新品自動上架：方案 B 實驗規格

狀態：**PoC / dry-run only**。本文件規劃自動上架，**不代表已在正式計數器啟用**。在獨立 Code Review 及 GitHub CI 通過之前，禁止 merge、push 到 master、變更正式 menu.json 或發布至 Pages。

## 當前架構與已知問題

- scripts/detect_instagram_menu.py 自動或手動發現公開貼文，輸出 data/instagram_candidates.json，全部 requiresReview=true。
- scripts/update_menu.py 只會把 data/promotions.json 中 promotion 與 item 均設 verificationStatus=verified，且已進入日期範圍的項目放進 menu.json。
- 官方餐單 updater 每日 06:00 HKT 運行；Instagram detector 也每日 06:00 HKT 運行。
- 本地 Windows 曾發現 12 篇 Instagram 貼文、34 個經清理的候選，但無一個可直接從 caption 確認 numeric HKD price。GitHub-hosted Ubuntu / Windows / macOS runners 曾發現 0 篇。因此「Workflow 成功」不是「已發現所有新貼文」。
- 既有 PR #10 是未合併的 JJK draft；不要改、合併或讓新自動化偷渡其項目。

## 目標

在來源、菜名、價錢、開始日期、類別及分店限制有**機器可核實證據**時，系統無需日常人工操作即可從官方 @sushiro.hk 公開貼文產生 promotions、通過 CI、自動合併受控資料 PR，然後經既有 update-menu workflow 刷新網站。缺乏證據的項目必須自動隔離，絕不可假設已核實；因此無法保證每篇 Instagram 貼文都能自動上架。

## 不使用 Meta API 的來源

1. 優先使用此前有實際成功記錄的本地 Windows 排程掃描（或用戶自行控制的 self-hosted runner）讀取無需登入的公開 HTML。GitHub-hosted runner 保留為 best-effort fallback，結果為 0 時不視作成功。
2. 不使用登入、私有 API、截取 cookies、繞過 CAPTCHA、代理封鎖或付費不明 scraper。搜尋索引找到的 URL 必須從原始公開貼文核實作者為 @sushiro.hk。
3. 掃描器只可把經驗證的 post ID、公開來源 URL、publication date、caption/media fingerprint、可引用的公開證據及候選資料送進 GitHub。任何密鑰存於安全 secrets；最小權限，不納入 log/artifact/PR。
4. 沒有來源、抓取失敗、只有 HTML login shell、只抽到部分 carousel、超出掃描上限、部分 post 不能存取：保留 checkpoint、標示 partial/unavailable、保存現有候選；不可把 0 當成沒有新品。

## 抽取與證據 schema

為每個 item 保存：canonical sourcePostUrl、postId、author proof、fingerprint、itemName、itemType、price HKD、priceEvidenceText、priceEvidenceSource (caption/OCR/other official source)、priceConfidence、startDate、endDate、dateEvidence、branchExclusions、availability / stock limitations、purchaseCondition、matchedExistingMenuId、reviewReasons、confidence flags、observedAt 及 extractionVersion。只保存必要的引用、文字證據、URL/hash，不把整份受版權保護的圖片當成長期 repository 資料。

- 圖片文字 OCR 屬可選獨立模組；carousel 所有可用頁面均需檢查，失敗不可假設沒有其他限制。
- 店鋪限定價格、商品加購門檻及贈品不屬一般可選壽司；merchandise、conditional-add-on 不得作為普通 dish 匯入。
- 明確的 HK$ 或 HKD 價格可作強證據；只有 $17 時不可單靠符號換成 HKD，除非從同一官方貼文其他可驗證圖文或另一官方來源交叉確認幣別與數值。
- 每項商品使用獨立日期。不要以貼文發布日期推斷上架日期，不要將另一商品的活動結束日錯配至當前商品。對「售完即止」保留狀態及可供應性不確定性。
- 使用 deterministic canonical ID 與 fuzzy match + 人工已核實 item ID 對照，但不能因 fuzzy match 相似便直接覆寫既有 verified 價錢。

## 全自動發佈 gate（deny by default）

新增獨立 candidate-to-promotion pipeline（不可改成在 detector 中直接設 verified）。只有同時滿足以下條件才自動標成 verified 並進入發佈 PR：

1. 可驗證來源：@sushiro.hk 的公開貼文、canonical URL、穩定 post ID，且當次原始證據仍可讀；不能只信搜尋摘要。
2. 清楚的餐飲品名稱與類別，沒有純標點、宣傳長句、精品、條件式加購或未支援的分店／套餐限制。
3. 有正數有限 HKD 數值價格，且對應特定菜名；OCR / 文字價格若矛盾或多義，一律隔離。價格超出合理上下限或對既有品項價格變動顯著，隔離。
4. 該菜式的開始日期有明確可靠證據，不得自行用公告日代替；結束日期可為 null（明確不限期或售完即止），但要保留來源及供應警告。若 startDate/endDate 衝突，隔離。
5. 不會與既有 verified promotions、PR #10 或現行 menu.json 產生不明重複 ID、錯誤覆蓋或同名不同價格衝突。不得改動任何手動 verified 價格而無更強官方證據。
6. 抽取、驗證、變更比較及所有回歸測試通過；單次變動量異常時（例如大量新品、整個分類移除、大幅改價）自動停止，保留可檢查的報告。
7. 自動發佈只啟用在明確配置的 feature flag，預設 false，並有 kill switch、auditable approval policy version 與可回退 commit。不可讓來自 Instagram 的內容成為指令、路徑、workflow 或 shell 參數。

沒有通過 gate 的候選仍保存 review-only 和具體拒絕原因；不因方案 B 而強制上架 100% 貼文。

## 發佈架構

A. 本地掃描 → validated candidate artifact → GitHub 固定資料分支 / 專用 PR（最小權限） → deterministic verifier / promotion generator。

B. 優先 dry-run：產生 data/autopublish_preview.json 與差異報告，不動 promotions.json。正式模式才產生 promotions.json 的最小變更，並以獨立的自動發佈 PR 進 CI；僅當 hard gates 與 required checks 都成功才自動合併。不要修改目前 review-only scanner PR 自動合併策略。

C. 已合併 promotions 的 verified future items 由既有日更 updater 在正確日期發布；如需立即生效，可 dispatch 現有 updater，但必須同樣通過 CI；不得 bypass schema validation、fallback menu 或 Pages checks。

D. 每次記錄 source/fingerprint、舊/新 item diff、policy version、publish commit、validation run、rollback SHA。支援 dry-run、--check、--auto-publish flags，及緊急停用配置。出現 CI 失敗或資料來源品質下降時停止該次上架，不破壞舊資料。

## 無 API 及自動金額風險

- Instagram 公開 HTML 可能隨時改版、限流或僅提供少量貼文；沒有可保證完整的官方 post discovery API。GitHub runner 0-post 問題目前仍未解決。
- 只用公開貼文文案通常拿不到所有 carousel 圖中的價錢及限制；OCR 會將 $17、$77、分店、日期誤讀，且一篇 post 可以包含多款商品。
- 店鋪專享、分時段、售完即止、商品加購限制及 10% 服務費等可能導致計數器與實際付款不同；不可無證據顯示「全港所有分店都有貨」。
- 自動合併會把錯誤迅速傳到所有使用者，因此要有最小變更、資料 diff、kill switch、可回退記錄，不能以測試通過取代真實價格證據。
- 最佳策略是**高可信項目全自動、其餘自動隔離**，不聲稱能 100% 自動上架每一款新品。

## 第一輪 Codex 實作與驗收（只在實驗分支）

1. 先檢查 master 最新程式及 JJK PR #10；不得直接改 master，保留目前錄入資料與已核實 promotions。
2. 新增獨立驗證模組及 promotion generator、保守的 policy/feature flags、dry-run CLI、來源 evidence schema、可重跑測試 fixture。盡量重用 detector/update_menu 既有函數而非重新實作 entire pipeline。
3. 使用已提交的 12-post/34-item review2 report 作為**負向測試**：目前全為 ambiguous-currency，不得錯誤 auto-publish 或產生 0 元價格。
4. 新增正向 fixture：官方來源、明確 HK$、唯一菜名、可靠日期 → dry-run 中生成 schema-valid promotion；未達 gate 只輸出 review-only rejection reason。
5. 覆蓋 JJK 11/17、12/1、12/7 item-specific dates、加購門檻、不同分店、OCR 錯誤、重複、更新編輯、移除已驗證項目、缺少 checkpoint、公開來源 0-post、跨年、future/expired、離線刷新及 bill regression。
6. 全量 Python/Node/JSON/Service Worker/updater tests + git diff --check，詳細記錄執行結果。新增生成 preview、拒絕理由統計、候選和已發佈 diff。
7. 本輪**不得啟用正式 auto-merge / auto-deploy**，不得修改 PR #10。成功後回報 [C2C] EXECUTED 供 ChatGPT 獨立審核。

## Release gate

在本地 fake fixtures 和至少一份可驗證的真實公開貼文上證明正向自動上架，並經獨立 code review 後，才可提出正式啟用 PR。只靠 runner workflow success 或合成測試通過，不構成真實自動上架能力的證明。
