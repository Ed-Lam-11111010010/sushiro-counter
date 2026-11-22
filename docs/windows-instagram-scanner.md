# Windows 本地 Instagram 候選掃描 → GitHub Draft PR（實驗）

**僅實驗，需用戶自行在 Windows 執行一次安裝。** 不會修改正式 master、menu.json、promotions.json 或 Pages；不會登入 Instagram、使用私人 API 或繞過平台限制。

## 流程

Windows 使用公開 Instagram HTML 執行 Python detector → 保留本地 checkpoint → 由 experimental auto-publish verifier 產生 dry-run preview → 若來源完整且候選有新內容，才使用 GitHub CLI 把 data/instagram_candidates.json 單獨推到 automation/instagram-local-<hash> → 對 experiment/instagram-auto-publish-plan-b 建立 Draft PR → GitHub Actions 再產生獨立 preview/diff artifact。

本地輸出保存在 %LOCALAPPDATA%\SushiroCounterInstagram。repo 會在每次執行時建立獨立的暫存 clone，不會修改你的原有 working tree。提交只包含候選 JSON，不包含私有登入資訊、價格正式設定或程式碼。使用 hash 命名的分支使同一批候選重試不會反覆開 PR；遠端分支資料不符時停止，不會強制覆寫。

## Windows 一次性設定

1. 安裝 Python 3、Git for Windows 和 GitHub CLI (gh)，確保三者都已加入 PATH。
2. 在個人 Windows 帳號執行 gh auth login，再執行 gh auth setup-git。需要 GitHub repository 建立分支及 PR 的權限。不要把 token 或 cookies 複製到聊天或檔案。
3. 在已取得實驗分支的 repo 內執行以下 PowerShell 命令：

       git fetch origin experiment/instagram-auto-publish-plan-b
       git switch experiment/instagram-auto-publish-plan-b
       python .\scripts\local_instagram_bridge.py

   第一個掃描為本地 dry-run；輸出 preview-ready 才代表找到可進行核證預覽的公開候選。若顯示 source-incomplete，代表 Instagram 公開來源不完整，不應假設沒有新菜式。

4. 確認本機來源可用後，手動試行提交 Draft PR：

       python .\scripts\local_instagram_bridge.py --submit-pr

   只有新候選才會開 PR。所有 PR 保持 Draft，不會自動合併，亦不能觸發正式餐單更新。

5. 如要每天自動執行，使用下列命令自己註冊 Windows Task Scheduler：

       powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\install_windows_instagram_task.ps1 -Time 09:00

   -Time 是 Windows 電腦所在地的本地時間，不一定是香港時間。安裝器把 runner 複製到 %LOCALAPPDATA%\SushiroCounterInstagram，建立 SushiroInstagramReviewCandidates 任務。電腦必須開機、帳號登入並能連線；錯過時間會盡量於可用時補跑，但不保證準時。

6. 可在 Windows PowerShell 停用／移除排程：

       Unregister-ScheduledTask -TaskName SushiroInstagramReviewCandidates -Confirm:$false

   如需完全移除本地報告，先停止排程後再手動清理 %LOCALAPPDATA%\SushiroCounterInstagram。

## 安全規則與可觀察性

- 來源 profile/post 無法取得、掃描 partial、沒有 post anchors 或 fallback 報錯時，不開 PR，不推進「成功」的掃描認定。
- 可見的公開貼文不一定是完整帳戶歷史。Workflow 綠色不能代表新菜已找到。首次掃描也可能產生已經上架的候選，核證器必須重新檢查重複和分店限制。
- gh 的 token 由用戶在自己電腦上登入，從不儲存到 repo。Task 用 Interactive logon，無需把密碼或 token 明文放入排程參數。
- 核證預覽只是建議；不能自動把 HK$ 或 $ 推定價格變成正式菜單。實驗 policy 仍 productionEnabled=false、killSwitch=true；PR #13 仍未批准 merge。
- 本地檔案：latest_scan_report.json、latest_autopublish_preview.json、latest_autopublish_diff.json、instagram_candidates.json、instagram_scan_state.json 及 last_submitted.json。上傳到 GitHub 的 Draft PR **只有候選 JSON**。
- 若本地提交因網絡中斷失敗，未成功建立 PR 不會寫入提交 receipt，下一次可用相同 hash 分支重試。
- 可執行 python -m unittest discover -s scripts -p 'test_local_instagram_bridge.py' 進行完全離線測試。

## 正式自動上架仍需解決

GitHub-hosted runner 先前多次發現 0 篇公開 Instagram 貼文；即使 Windows 能掃描，也不保證以後穩定。價格 OCR、活動售完即止的下架機制、PR required checks/auto-merge 權限及 source 身分驗證都需獨立審查；在此之前不會自動更新正式主頁。
