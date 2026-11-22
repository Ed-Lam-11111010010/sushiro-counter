# 壽司郎香港價錢計算器

一個自包含嘅 HTML 檔，幫你喺食壽司郎嗰陣即時計碟數同結算金額。打開即用，唔使裝任何嘢。

**🌐 線上版：** https://ed-lam-11111010010.github.io/sushiro-counter/

---

## 功能

- **四色碟計數** — 紅碟 $12 / 銀碟 $17 / 金碟 $22 / 黑碟 $27，每行 `− 數量 +` stepper，即時顯示每色小計
- **其他堂食單品** — 預設項目按類別整理（麵類・湯類 / 副餐類 / 甜品 / 飲料 / 期間限定），打開選擇器後可在頂部小分頁切換，每項以整行加減器呈現，可調數量、可刪除
- **期間限定** — 由 `data/promotions.json` 管理名稱、價格、來源及供應期，唔需要每次改 Python hardcoded 清單
- **加成 / 折扣** — 支援 `+` 同 `−`、`$` 同 `%`，可加多行、可刪除（例如 −$20 優惠券、+10% 附加費）
- **即時結算** — sticky 結算欄顯示 小計 → 加成調整 → 加成後小計 → 服務費 10% → 總計
- **自動儲存** — localStorage 記住你嘅選擇，refresh 唔會清空；附「重置」掣一鍵清零
- **保留已選項目價格** — 離線或餐單移除項目時，使用上次儲存的名稱及價格；最新餐單有該項目時則使用新價格。舊訂單若無可還原價格，會提示「待確認價格」，避免少計
- **離線可用** — 純靜態檔，雙擊即開，唔使上網
- **手機優先** — responsive 設計，手機餐廳現場用都得
- **可安裝 PWA** — 可加入手機主畫面，離線時仍可用
- **餐單自動同步** — `menu.json` 由排程工作流對照香港壽司郎官方餐單，有變更時才開 PR
- **Instagram 候選偵測** — 每日嘗試讀取壽司郎香港公開 Instagram 貼文，將新菜式、明確 HK$ 價格及活動日期寫入 review-only 候選報告
- **繁體中文（HK）** 介面

---

## Instagram 官方香港標價試行（實驗分支）

- 試行只在 `experiment/instagram-official-hkd-pilot` 上使用，與正式 `master` 無關。政策 `allowOfficialHkDollarContext=true`，但 **`productionEnabled=false`、`killSwitch=true`**；不會改動主頁 `menu.json`、`data/promotions.json` 或自動合併。
- `@sushiro.hk` 的公開貼文出現 `$17` 等標價時，只有再次讀取 Instagram 原始貼文、確認結構化作者資訊、相同菜名及原始數值、內容 fingerprint 未改，才**推定**幣別為 HKD。輸出標記 `official-hk-context` 和 `currencyInference`，**不會聲稱原文有 HK$**。
- 仍必須有個別品項開始及結束日期、可靠供應說明、全港適用且沒有「除外」分店、非加購精品／套餐、無重複品項及已核實的價錢衝突；存疑一律隔離。因無售完即止自動下架能力，這項 `$→HKD` 試行要求有固定結束日期。
- GitHub-hosted runner 過往經常無法發現公開 Instagram 貼文，因此 scanner/審核工作流程成功 **不保證發現新商品**。如需真正無人值守，必須再解決可用公開來源、官方核實及安全合併／回退。
- 實驗分支的 CI 回歸測試包含正例（已驗證的 `$22`、日期及分店）與負例（錯誤作者、篡改價錢、分店除外）。正式自動發布仍需另行審查及批准。

## Windows 本地 Instagram 候選掃描（實驗）

當 GitHub-hosted runner 未能讀取 Instagram 的公開貼文時，可以由用戶自己的 Windows 電腦按排程執行 detector，保留本地 checkpoint，產生 dry-run 核證預覽，並只把新增候選提交至實驗分支 Draft PR。**不會自動改價、合併、更新 master 或部署**。首次使用必須由用戶自行安裝 Python、Git、gh 並授權，詳見 [Windows 掃描器設定說明](docs/windows-instagram-scanner.md)。

## 點用

### 方法一：直接開線上版
去 https://ed-lam-11111010010.github.io/sushiro-counter/ 即用。

### 方法二：本地開
下載 `index.html`，雙擊即開（任何瀏覽器都得）。

### 方法三：嵌入其他網站
```html
<iframe src="https://ed-lam-11111010010.github.io/sushiro-counter/"
        width="480" height="800" frameborder="0"></iframe>
```

---

## 點計

計算順序（HK 餐廳慣例，折扣喺服務費之前計）：

```
小計       = Σ(碟數 × 碟價) + Σ(其他項目 數量 × 價錢)
加成調整   = Σ 所有加成/折扣行（$ 直接加減；% = 小計 × 百分比）
加成後小計 = max(0, 小計 + 加成調整)        ← 負數會 clamp 做 0
服務費     = 加成後小計 × 10%
總計       = 加成後小計 + 服務費
```

### 例子

紅碟 ×2、黑碟 ×1、茶碗蒸 ×1、豚骨拉麵 ×1，再用 −$20 優惠券：

| 項目 | 金額 |
|------|------|
| 紅碟 ×2 | $24 |
| 黑碟 ×1 | $27 |
| 茶碗蒸 ×1 | $19 |
| 豚骨拉麵 ×1 | $32 |
| **小計** | **$102** |
| 優惠券 −$20 | −$20 |
| **加成後小計** | **$82** |
| 服務費 10% | $8.20 |
| **總計** | **$90.20** |

---

## 自訂

餐單項目及價錢集中在 `menu.json`，格式如下：

```json
{
  "schemaVersion": 1,
  "updatedAt": "2026-09-25",
  "categories": {
    "麵類・湯類": [
      { "id": "steamed_clams", "name": "出汁蒸蜆", "price": 18 }
    ]
  }
}
```

如需調整四種碟價，才需要修改 `index.html` 頂部的 `PLATES`。改完 save 就得，唔使重新 build。

## 定期更新與安裝

- 本專案係可安裝 PWA：用手機瀏覽線上版，在瀏覽器選「加入主畫面／安裝 App」即可。
- `menu.json` 是餐單資料來源；頁面會優先讀取它，離線時使用瀏覽器快取及內置備份。
- 官方餐單同步工作流每日香港時間 06:00 讀取香港壽司郎官方餐單頁面。只有資料通過基本驗證且有差異時，才會建立 `chore: update Sushiro HK menu` PR；官方餐單 PR 可按現有流程自動合併，再觸發 GitHub Pages 部署。每日執行亦確保已核實的 future promotion 在開始日期及時加入，而不是等待下一個星期一或四。
- Instagram 候選偵測工作流每日香港時間 06:00 執行。它只使用公開頁面 HTML 及 `data/instagram_sources.json` 的人工補充資料，不使用登入、私有 API 或繞過限制的方法。
- Instagram detector 會將 runner 的 `.cache/instagram_scan_state.json` 存入 GitHub Actions cache，記錄 `lastAttemptAt`、`lastSuccessfulScanAt`、已發現的 post ID、內容 fingerprint 及最近看到的時間。每次成功 profile/post discovery 會以最近一次成功掃描為基準，加上 7 日 overlap；caption 或可見 media metadata 改變時會重新處理。profile 沒有 post links、被封鎖或有任何 post fetch 失敗時，只記錄 attempt，不推進 successful checkpoint。
- 如果 Instagram 個人頁未提供 post anchors，scanner 會 best-effort 檢查壽司郎官方網站上的公開 Instagram 連結，及公開搜尋索引提供的 RSS 連結。搜尋索引只是線索：相關貼文必須另有 `@sushiro.hk` 的公開作者 metadata 才接受；這兩種後備來源無法證明已找到所有新貼文，所以結果標示 `partial`，不會推進完整掃描 checkpoint。公開 RSS 服務及 Instagram 頁面可能受限制，失敗不影響正式餐單。
- 未發現任何新／編輯貼文時，scanner 不會只因 `discovery=none`、來源錯誤或掃描時間改變而修改 `data/instagram_candidates.json` 或建立新 PR；運作診斷仍保存在 Actions artifact 與 job summary，並清楚區分「workflow success」與「discovery success」。
- 每次 Instagram scan 亦會產生 compact `instagram_scan_report.json`，包括 post ID、publication date、item-level promotion dates、item type、price confidence 及 review status；GitHub Actions 會把它作為 artifact 保存。profile 暴露超過安全上限時，會按 canonical URL 穩定排序，只處理前 `MAX_DISCOVERED_POSTS` 篇並標記 `partial`，不會推進成功 checkpoint。
- 貼文有多個商品時，只會把明確的 `販售期間` 區段日期配到附近商品；日期段落無法可靠歸屬時保留空值，並設 item-level 日期審核標記。`data/instagram_scan_report.json` 保留前一版 12-post scan；`data/instagram_scan_report_review2.json` 保存本輪結果，方便比較。
- Instagram 候選只會寫入 `data/instagram_candidates.json` 並開 review-only PR，**不會自動合併、不會直接修改 `menu.json`，亦不會觸發部署**。人工核對貼文、價格、日期及分店供應後，先將確認項目加入 `data/promotions.json`，並為 promotion 及每個 item 都設為 `verified`，再由官方同步工作流更新頁面。
- 如果 Instagram 暫時無法存取，偵測器會記錄限制並保留既有候選；官方餐單同步及現有有效資料不受影響。
- 官方餐單可能因分店、供應及活動變更而不同，App 顯示的價格只供結帳前參考。

### Instagram 候選的自動核證預覽

`scripts/instagram_autopublish.py` 是獨立於 review-only detector 的政策引擎。預設只產生 `data/autopublish_preview.json` 及 `data/autopublish_diff.json`；它不會改寫 `data/promotions.json`。只有公開作者 metadata、未變更的貼文 fingerprint、近 24 小時觀察紀錄、明確 HK$／HKD 價格、可對應的商品日期、售賣條件、全港分店證據及非重複食品／飲品全部通過時，候選才會出現在乾跑的 promotion preview。缺少或互相矛盾的證據會隔離並列出原因。

本地 2026-11-19 公開 HTML 掃描、候選核證結果及 promotion diff 分別保存在 [`data/instagram_autopublish_live_scan_2026-11-19.json`](data/instagram_autopublish_live_scan_2026-11-19.json)、[`data/autopublish_preview.json`](data/autopublish_preview.json) 及 [`data/autopublish_diff.json`](data/autopublish_diff.json)，供審查比較。

- `data/instagram_autopublish_policy.json` 是政策設定，包含 HKD 價格範圍、證據時效、每次變更上限、重複名稱相似度門檻及 PR #10 保護清單。`productionEnabled` 預設為 `false`，`killSwitch` 預設為 `true`，核准 policy version 預設為空；這些值目前不允許正式寫入。
- Policy 欄位、候選 evidence 及 OCR sidecar 的完整 schema 和範例見 [`docs/instagram-autopublish-evidence-schema.md`](docs/instagram-autopublish-evidence-schema.md)。
- 本機手動預覽：`python scripts/instagram_autopublish.py --candidates "C:\\path\\to\\instagram_candidates.json" --dry-run --as-of 2026-11-19`。Windows 工作排程器可先以 `scripts/detect_instagram_menu.py --output "C:\\path\\to\\instagram_candidates.json" --state "C:\\path\\to\\instagram_scan_state.json" --report "C:\\path\\to\\instagram_scan_report.json"` 產生輸入，再執行預覽命令。不同來源路徑不會改變正式餐單。
- 可選 `--ocr-evidence` JSON sidecar 必須列出每篇貼文的 fingerprint、OCR provider、所有公開圖片 URL、完整覆蓋狀態、圖片 SHA-256、項目文字／價格證據及信心值。引擎會只從 Instagram 公開頁暴露的 HTTPS Instagram／CDN 圖片來源下載圖片，重算 SHA-256 並比對 sidecar；來源錯誤、漏圖、低信心或價格衝突均會隔離。Sidecar 是外部 OCR 程序提供的證據；本 repository 沒有內建 OCR 模型，且 `trustedOcrProviders` 預設為空，因此本地或 GitHub 工作流不會自行讀取圖片文字，或自動接受未經核准的 OCR provider。
- GitHub Actions 在每日公開貼文掃描後產生乾跑 artifact。若以後啟用正式路徑，還需要明確核准同一 policy version、關閉 kill switch、設定 repository variables、配置 `instagram-autopublish` 環境的人工核准者，以及啟用 repository branch protection／必要檢查。正式工作流只會建立包含 `data/promotions.json` 的 PR；可選的 `gh pr merge --auto` 會服從 repository 的審核與檢查規則。這些 GitHub 設定不由此 repository 自動變更。
- 公開頁面不保證完整列出所有新貼文。`discoveryComplete` 只代表本次可存取的公開 HTML／後備來源已成功讀完；`discoveryCoverage` 明確標示它並非帳戶全部貼文的保證。來源不完整、HTTP 失敗、未驗證空結果或來源 fingerprint 改變時，候選不具正式上架資格。舊的 12-post／34-item report 中全部是 `$`，不是明確 HKD；它們會保持 review-only，不會自動補猜價格。

### 結構化期間限定資料

`data/promotions.json` 以 promotion 為單位保存：

- `verificationStatus`: promotion 及每個 item 都必須是 `verified` 才會進入 `menu.json`；候選及未核對資料不可直接上架。
- `sourceUrls`: 官方網站或官方 Instagram 貼文／帳戶來源。
- `startDate` / `endDate`: 使用 `YYYY-MM-DD`；`endDate: null` 代表未有確定終止日（例如售罄前），唔會因月份結束而自行刪除。
- `items`: 每個項目有穩定 `id`、名稱及明確價格。
- future promotion 會等到 `startDate` 才接管同名分類；`needs_review`／`unavailable` 會保留原有資料，`expired`／`rejected` 則不會無限期阻止舊活動清理。

如需手動補充官方貼文，將貼文 URL、caption、圖片 URL 或逐項轉錄資料放入 `data/instagram_sources.json`。這是偵測器的輸入，不代表已核實；所有結果仍要人工審核。

例如：

```json
{
  "schemaVersion": 1,
  "account": "sushiro.hk",
  "posts": [
    {
      "url": "https://www.instagram.com/p/POST_ID/",
      "caption": "新菜式，HK$22；2026年12月1日起",
      "publishedAt": "2026-11-30",
      "imageUrls": [],
      "items": [{ "name": "新菜式", "price": 22 }]
    }
  ]
}
```

公開 profile HTML 有時會暴露 owner-prefixed 的公開 post anchors（例如 `/sushiro.hk/p/.../`）；detector 會在不使用 API、登入或私有 endpoint 的前提下將它們 canonicalize，再直接讀取公開 post HTML。本次 live feasibility test 成功由 `https://www.instagram.com/sushiro.hk/` 自動發現及讀取 `https://www.instagram.com/p/Dfn7uyiCgAt/`；這與把 URL 放入 `data/instagram_sources.json` 後才讀取是兩條不同路徑。多個項目有不同活動日期時，日期會保留在各 item，candidate 的 global `startDate/endDate` 會留空。HTTP 200 但沒有 usable public metadata 的貼文也會被視為 incomplete。因為 Instagram 可能只回傳部分 client-rendered content，空 profile result 不會被當成成功掃描；若 profile 受限或改變格式，仍可用 source file 作 review-only 手動補充。

## 自我測試

在終端機執行（需要 Node.js 18 或以上，毋須安裝依賴）：

```sh
node --test scripts/test-menu-load.cjs
python -m unittest discover -s scripts -p 'test_update_menu.py'
python -m unittest discover -s scripts -p 'test_instagram_menu.py'
python scripts/update_menu.py --check
python scripts/detect_instagram_menu.py --check
```

測試會確認同步存檔、刷新後的新菜色及跨分類計算、離線／下架價格保留、未知價格提示、重設、多項加成折扣，以及 service worker 不會以無效回應覆蓋有效餐單快取；更新器測試會確認自動 ID 穩定、雙重審核、跨月份設定檔失效保留及 future promotion 日期；Instagram 測試會確認 owner-prefixed public anchors、profile 自動發現與手動 URL 讀取的區別、post ID/fingerprint、無新貼文、去重、caption 編輯、7 日 overlap、Jujutsu Kaisen 及 DfPQcXcCZCx 分段日期、飲品無日期、標點/宣傳段落名稱拒絕、HTTP 200 登入描述、超上限穩定排序、machine report、明確／模糊價格、商品分類及來源失效時保留資料。carousel 圖片內文字不會 OCR，未支援內容要人工補充。一般驗證及自動餐單更新工作流亦會執行。

喺瀏覽器 console 跑：

```js
window.__selftest()
```

會用固定輸入測試小計、加成、服務費、總計嘅計算是否正確，並 log ✅/❌ 結果。

---

## 技術

- PWA 外殼：`index.html` + `manifest.webmanifest` + `service-worker.js`
- Vanilla JavaScript，無框架、無 build step、無前端依賴
- `menu.json`：可快取、可版本化的餐單資料
- 無 backend、無 runtime analytics；官方餐單同步及 Instagram 候選偵測只在 GitHub Actions 排程中執行
- Instagram 偵測係 best-effort public HTML reader；report 會分開記錄 profile HTTP 狀態及 post discovery 狀態，checkpoint 則只在非空且完整、每篇 post 都有 usable metadata 的 public discovery 成功時前進。Instagram 可能提供登入頁、限制存取或改變頁面格式，因此不能保證每次發文都會被自動發現；carousel 圖片內容亦不會自動 OCR。Meta Business Discovery 等正式 API 需要授權 access token，本專案沒有把無 credentials 的 API 整合假裝成已啟用。
- 手機優先 CSS，無 CSS framework
- localStorage 持久化

---

## 授權

MIT License — 自由使用、修改、分發。

---

## 免責聲明

本工具係粉絲自製，**與壽司郎 / Sushiro / Food & Life Companies 無任何關係**。

碟價及常設餐單項目價錢係由[壽司郎香港官方餐單](https://sushirohk.com.hk/tc/menu.php?wid=5)抽取並由排程工作流更新；期間限定項目會按公開活動資料加入，供應分店、供應期及售罄情況可能不同。所有資料僅供參考，實際價格及供應情況請以店內及壽司郎香港官方公佈為準。

