# sushiro-calculator - Work Plan

## TL;DR (For humans)

**What you'll get:** 一個自包含嘅 HTML 檔，打開就係壽司郎香港價錢計算器。主頁直接顯示四色碟（紅$12/銀$17/金$22/黑$27）嘅「− 數量 +」掣，即時出碟數同金額；底部有 sticky 結算欄顯示 小計 → 加成調整 → 服務費10% → 總計。其他非碟色堂食單品（拉麵、茶碗蒸、甜品、飲品等 55 款）撳「加入其他項目」開小分頁揀。仲有一欄俾你輸入折扣或小加成（例如 −$20 優惠券 / +$30 加購），可選 $ 或 %，支援加同減。全部自動儲存喺瀏覽器，洗手機 refresh 都唔怕。

**Why this approach:** 單一 HTML 檔 = 雙擊即開、唔使裝任何嘢、可放 GitHub Pages、可嵌入其他網站。Vanilla JS、無框架、無 build step——最短最懶嘅路徑，而且改碟價或餐單項目只要改檔案頂幾行 config。

**What it will NOT do:**
- 唔會幫你夾錢（多人分帳）、計預算、記印花、匯出單據
- 唔會上網/用 backend
- 唔會有自由輸入其他項目（只用預設清單——跟返你嘅選擇）
- 唔會俾你喺 UI 改碟價（改 config 就得，唔使另建編輯器）

**Effort:** Quick (single file ~350 lines)
**Risk:** Low — well-scoped, verified data, no external deps
**Decisions to sanity-check:**
- 折扣/加成係喺服務費之前計（HK 慣例）；如果你想要相反次序，可以改
- 預設其他項目有 55 款，由官方餐單 2026-07-07 抽出；你鍾意可以刪走啲唔常用嘅

Your next move: 睇完計劃可以 `$start-work` 執行，或者叫個 high-accuracy review 先。全部執行細節喺下面。

---

> **TL;DR (machine):** Quick effort, Low risk. Single self-contained `index.html`. One-page calculator: plate steppers + sticky bill + 其他 modal + 加成 panel. localStorage. 繁中. ~350 lines, no deps.

## Scope
### Must have
- One `index.html` file: HTML + inline `<style>` + inline `<script>`, zero external deps, zero build step
- Config objects at file top: `PLATES` (4 items) and `OTHER_ITEMS` (55 items in 4 groups), editable by editing the file
- Main page layout (near full-screen, single-column):
  - Title bar: "壽司郎 HK 結算" + 重置 button
  - 4 plate-color rows: color chip + name ("紅碟 $12") + `− [count] +` stepper + inline subtotal (`$0`)
  - Compact 其他 summary row: "其他項目 (N) [加入其他項目] ⇾" + selected items shown as compact chip summary
  - 加成/折扣 panel: label input + +/− toggle + $/% toggle + value input + 加入 button; removable line items listed below
  - Sticky bill summary at bottom: 小計, 加成調整, 加成後小計, 服務費 10%, 總計 (all 2-decimal rounded)
- 其他 modal overlay:
  - 4 section headers (麵類・湯類 / 副餐類 / 甜品 / 飲料) as collapsible chip grids
  - Click chip → selects that item (qty=1), chip shows styled ✓; click again → increments qty
  - Bottom panel inside modal: list of selected items with qty stepper + remove button per item
  - "完成" button closes modal; "取消" button (or Escape key, or click backdrop) closes without changes
  - Main page summary updates after modal closes
- 加成/折扣 compute order:
  1. `小計 = Σ(plate count × plate price) + Σ(other item qty × price)`
  2. `加成調整 = Σ of all mods` (flat $: +/−value; percent: +/−小計×value/100)
  3. `加成後小計 = max(0, 小計 + 加成調整)` — clamp at 0 with inline "⚠️ 加成後小計已調整至 $0" warning
  4. `服務費 = round(加成後小計 × 0.10, 2)`
  5. `總計 = round(加成後小計 + 服務費, 2)`
- localStorage: auto-save on every state change (debounced 500ms), auto-restore on load; 重置 button clears storage and resets all to 0
- Built-in self-test: console-accessible `window.__selftest()` — runs fixed inputs, asserts bill values, logs pass/fail
- 繁中(HK) UI, mobile-first CSS (responsive, usable on phone)

### Must NOT have (guardrails, anti-slop, scope boundaries)
- NO build step, NO npm/package.json, NO node_modules, NO framework (no React/Vue/Svelte)
- NO external CSS or JS files; NO CDN links; NO web fonts
- NO multi-person split, NO budget mode, NO stamp/印花 reminder, NO receipt export/print
- NO free-form 其他 item entry (predefined list only — user chose this)
- NO in-UI price editor (config at file top only)
- NO service-charge toggle (10% always applied)
- NO backend, NO network requests, NO analytics/tracking
- NO splitting into multiple files (one `index.html` only)
- NO AI-slop patterns: no unrequested abstractions, no config-for-one, no "for future expansion" comments

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- **Test decision:** Tests-after (self-test housed inside the file itself)
- **Evidence:** `.omo/evidence/sushiro-calculator-selftest.txt` (console output of `window.__selftest()`)
- **How:** agent opens `index.html` in a real browser (Playwright/node), runs `window.__selftest()` in console, captures output, verifies all assertions pass. Also manually tests: +/− steppers on all 4 plates, opening modal and selecting items, 加成/折扣 with $ and %, 重置 clears all, refresh restores from localStorage, negative 加成 results in clamped $0 with warning.
- **Failure path:** commit a known-bad input, verify the self-test catches it, then revert.

## Execution strategy
### Parallel execution waves
One single wave (single file, single todo — implementation + test in one shot).

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. Write `index.html` | nothing | nothing | n/a (only todo) |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Write `index.html` — full Sushiro HK price calculator
  **What to do / Must NOT do:**
  Create ONE self-contained `index.html` at project root with all HTML, CSS, and JS inline.
  NO external deps, NO build, NO npm, NO framework.
  Follow the approved design exactly (see Scope Must have).
  Must NOT add multi-person, budget, stamps, export, free-form entry, service toggle, or file splitting.
  Must NOT add any feature the user didn't ask for (YAGNI).

  **Config section (top of `<script>`):**
  ```js
  const PLATES = [
    { id:'red',    label:'紅碟', price:12, color:'#e53935', bg:'#ffebee' },
    { id:'silver', label:'銀碟', price:17, color:'#757575', bg:'#f5f5f5' },
    { id:'gold',   label:'金碟', price:22, color:'#f4b41a', bg:'#fff8e1' },
    { id:'black',  label:'黑碟', price:27, color:'#1a1a1a', bg:'#e0e0e0' },
  ];
  const OTHER_ITEMS = {
    '麵類・湯類': [
      { id:'steamed_clams', name:'出汁蒸蜆', price:18 },
      { id:'clam_miso',     name:'蜆肉海苔麵豉湯', price:20 },
      { id:'tofu_udon',     name:'腐皮烏冬', price:27 },
      { id:'kamadama_udon', name:'釜玉烏冬', price:28 },
      { id:'tempura_udon',  name:'炸蝦天婦羅烏冬', price:28 },
      { id:'tonkotsu_ramen', name:'豚骨拉麵', price:32 },
      { id:'spicy_ramen',   name:'辣豚骨拉麵', price:32 },
      { id:'beef_udon',     name:'牛肉烏冬', price:33 },
    ],
    '副餐類': [
      { id:'mentaiko',  name:'明太子沙律醬', price:3 },
      { id:'onsen_tamago', name:'溫泉蛋', price:6 },
      { id:'chicken_karaage_1', name:'脆脆炸雞塊 1塊', price:12 },
      { id:'golden_fries', name:'黃金脆薯', price:13 },
      { id:'pumpkin_tempura', name:'南瓜天婦羅', price:13 },
      { id:'shrimp_tempura_half', name:'炸蝦天婦羅拼盤(半份)', price:18 },
      { id:'shrimp_tempura', name:'炸蝦天婦羅', price:18 },
      { id:'fried_cartilage', name:'炸雞軟骨', price:19 },
      { id:'chawanmushi', name:'茶碗蒸', price:19 },
      { id:'chicken_karaage_2', name:'脆脆炸雞塊 2塊', price:22 },
      { id:'unagi_chawanmushi', name:'店內特製鰻魚茶碗蒸', price:22 },
      { id:'akagai_sashimi', name:'北寄貝刺身', price:22 },
      { id:'sweet_shrimp_sashimi', name:'甜蝦刺身', price:22 },
      { id:'conch_sashimi', name:'海螺刺身', price:22 },
      { id:'tuna_sashimi_set', name:'吞拿魚2種刺身', price:22 },
      { id:'shrimp_tempura_full', name:'炸蝦天婦羅拼盤', price:28 },
      { id:'salmon_sashimi_set', name:'生三文魚刺身盛', price:39 },
      { id:'akaebi_sashimi', name:'赤蝦刺身', price:39 },
      { id:'hotate_sashimi', name:'日本產帆立貝刺身', price:42 },
      { id:'salmon_belly_sashimi', name:'生三文魚腩刺身', price:48 },
      { id:'salmon_hamachi_sashimi', name:'生三文魚腩刺身・油甘魚腩刺身', price:48 },
    ],
    '甜品': [
      { id:'melon_sorbet', name:'蜜瓜味雪葩', price:13 },
      { id:'frozen_mango', name:'冷凍芒果', price:13 },
      { id:'soy_warabimochi', name:'黃豆粉蕨餅', price:13 },
      { id:'choco_montblanc', name:'朱古力蒙布朗蛋糕', price:17 },
      { id:'milk_soft_cream_cookie', name:'牛奶味軟雪糕(有曲奇)', price:18 },
      { id:'milk_soft_cream', name:'牛奶味軟雪糕', price:18 },
      { id:'crema_catalana', name:'卡達拉娜', price:22 },
      { id:'sushiro_pudding', name:'壽司郎經典布甸', price:22 },
      { id:'crepe_cake', name:'千層蛋糕', price:22 },
      { id:'soy_warabi_parfait', name:'黃豆粉蕨餅芭菲', price:27 },
      { id:'choco_berry_parfait', name:'朱古力香莓芭菲', price:27 },
    ],
    '飲料': [
      { id:'pepsi_zero', name:'百事可樂(無糖)', price:8 },
      { id:'pepsi', name:'百事可樂', price:8 },
      { id:'mirinda_orange', name:'美年達橙汁', price:8 },
      { id:'7up', name:'七喜', price:8 },
      { id:'vita_lemon_tea', name:'維他氣泡檸檬茶', price:8 },
      { id:'apple_juice', name:'100％蘋果汁', price:8 },
      { id:'calpico', name:'可爾必思', price:8 },
      { id:'sunsay_tea', name:'纖解茶', price:9 },
      { id:'kikumasamune', name:'菊正宗', price:17 },
      { id:'horoyoi_grape', name:'Horoyoi(葡萄味)', price:19 },
      { id:'horoyoi_yuzu', name:'Horoyoi(柚子鹽味)', price:19 },
      { id:'suntory_beer', name:'三得利頂級啤酒', price:24 },
      { id:'umeshu', name:'梅酒(含梅子)', price:27 },
      { id:'whisky_soda', name:'威士忌梳打', price:27 },
      { id:'kikumasamune_daiginjo', name:'菊正宗(大吟釀)', price:59 },
    ],
  };
  ```
  (Note to executor: prices verified 2026-07-07 from official menu. Leave a comment at the top with the date and source URL.)

  **Main page structure:**
  ```
  ┌─ header: "壽司郎 HK 結算" + [重置] ─────────────────┐
  │                                                       │
  │  4 plate rows (one per color):                       │
  │  [color chip] 紅碟 $12  [−] [0] [+]  $0              │
  │  [color chip] 銀碟 $17  [−] [0] [+]  $0              │
  │  [color chip] 金碟 $22  [−] [0] [+]  $0              │
  │  [color chip] 黑碟 $27  [−] [0] [+]  $0              │
  │  ────────────────────────────────────────────         │
  │  其他項目 (0)  [加入其他項目 ▶]                        │
  │  已選: 茶碗蒸×1, 豚骨拉麵×1                            │
  │  ────────────────────────────────────────────         │
  │  加成 / 折扣                                          │
  │  [label input] [+/−] [$/%] [num input] [加入]         │
  │  • 優惠券  −$20                            [刪除]     │
  │  • 加購    +$30                            [刪除]     │
  │  ────────────────────────────────────────────         │
  │  小計              $70.00                             │
  │  加成調整          −$20.00                            │
  │  加成後小計        $50.00                             │
  │  服務費 (10%)      $5.00                              │
  │  ────────────────────────────────────────────         │
  │  總計              $55.00                             │
  └───────────────────────────────────────────────────────┘
  ```

  **Other modal structure:**
  - Full-screen overlay with semi-transparent backdrop
  - Centered white panel (max-width 480px, scrollable)
  - Title: "其他項目" + [取消] [完成]
  - 4 collapsible sections, each with category name header + chip grid
  - Each chip: item name + price; click to select (✓ overlay); selected chip styled differently
  - Clicking a selected chip increments its qty
  - Bottom section: "已選項目" — list of (item name × qty) with [-][qty][+] stepper + [刪除]
  - 完成 saves selection (updates main-page summary) and closes
  - 取消 or Escape or backdrop-click closes without saving changes

  **Interaction rules:**
  - All state changes auto-save to localStorage (debounced 500ms, key: `sushiro-counter-state`)
  - Plate steppers: clicking [+] increments count; clicking [−] decrements (min 0)
  - Other selection in modal: max per-item not limited (people can order 10茶碗蒸)
  - 加成/折扣: [加入] validates that value > 0; label is optional (defaults to "折扣" or "加成" based on sign); added to list below each with [刪除]
  - 重置 button: shows confirm("確定要重置所有資料？") before clearing localStorage and resetting all to 0
  - All monetary values: `toFixed(2)` with HK$ prefix; 0 values shown as "$0.00"
  - Negative 加成後小計: clamp at 0, show warning "⚠️ 加成後小計已調整至 $0"

  **Data model (in-memory state):**
  ```js
  const state = {
    plates: { red: 0, silver: 0, gold: 0, black: 0 },
    other: { 'mentaiko': 2, 'chawanmushi': 1, 'tonkotsu_ramen': 1, ... },  // id → qty
    modifications: [
      { id: 'm1', label: '優惠券', sign: '-', unit: '$', value: 20 },
      { id: 'm2', label: '加購', sign: '+', unit: '$', value: 30 },
    ],
  };
  ```

  **Self-test (`window.__selftest`):**
  Set known state: plates = {red:2, silver:0, gold:0, black:1}, other = {chawanmushi:1, tonkotsu_ramen:1}, modifications = [{sign:'-',unit:'$',value:20}].
  Expected: 小計=12*2+27+19+32=90, 加成調整=-20, 加成後小計=70, 服務費=7.00, 總計=77.00.
  Assert each computed value, log ✅/❌ per assertion, return {pass: true|false, details: [...]}.
  Also test negative-case: set mod to −$999 → verify 加成後小計 clamps to 0, warning shown.

  **Edge case rules (explicit instructions for worker):**
  - Number inputs for 加成 value: `type="number"` with `min="0"`, reject empty/NaN/negative. Show inline "請輸入有效正數" if invalid.
  - localStorage: check for corrupt data on load (JSON parse try/catch). If corrupt, silently reset and do NOT crash page. Version in key: `sushiro-counter-state-v1`.
  - State with all zeros: display "$0.00" everywhere. 服務費 shows "$0.00". No confusing NaN/undefined.
  - Modal backdrop: `click -> close`. Prevent scroll propagation (`overflow: hidden` on `<body>` when modal open).
  - XSS prevention: ALL user-visible labels come from config (PLATES, OTHER_ITEMS) or from the 加成 label input. The label input: use `.textContent` or `innerText`, NEVER `innerHTML`, to set the display. For chip labels from config, also use `textContent` (they are trusted data but defense-in-depth).
  - 重置 confirmation: yes/no native `confirm()` dialog.

  **Mobile-first CSS notes:**
  - Single-column layout, `max-width: 480px`, centered, `padding: 12px`
  - Plate rows: `display: flex`, `align-items: center`, `gap: 8px`
  - Stepper buttons: minimal 36×36 tap targets, `font-size: 1.2rem`
  - Chip grid: `display: flex; flex-wrap: wrap; gap: 4px`
  - Each chip: `padding: 6px 12px`, `border-radius: 16px`, `font-size: 0.85rem`
  - Sticky bill: `position: sticky; bottom: 0; background: white; box-shadow: 0 -2px 8px rgba(0,0,0,0.1);`
  - Colors: light gray bg for both modal + main page; accent color for 總計 row
  - DO NOT use any CSS framework; all styles inline in `<style>`

  **Acceptance criteria (agent-executable):**
  1. File `index.html` exists at project root, is < 2500 lines (target ~350).
  2. Open in browser via `start index.html` or `npx playwright open index.html` — page renders without errors (check console).
  3. Run `window.__selftest()` in console — all assertions pass (capture output to `.omo/evidence/sushiro-calculator-selftest.txt`).
  4. Click [+] on 紅碟 3 times → shows 3, subtotal $36. Click [−] once → shows 2, subtotal $24.
  5. Click [加入其他項目] → modal opens → click 茶碗蒸 chip → shows ✓ → click 完成 → main page shows "已選: 茶碗蒸×1".
  6. Open modal again → click 茶碗蒸 chip again → qty 2 → click 豚骨拉麵 chip → 完成 → main page shows "已選: 茶碗蒸×2, 豚骨拉麵×1".
  7. 加成/折扣: enter label="測試", sign="+", unit="$", value=10, click 加入 → list shows "測試  +$10".
  8. Total reads correct: 紅碟2($24) + 茶碗蒸2($38) + 豚骨拉麵1($32) + test mod(+$10) = 小計$104, 加成調整$10, 加成後小計$114, 服務費$11.40, 總計$125.40.
  9. Click [x] on test mod → it disappears; bill recalculates.
  10. Click 重置 → confirm dialog → confirm → all counts 0, other empty, mods empty, bill $0.
  11. Manually corrupt localStorage: `localStorage.setItem('sushiro-counter-state-v1','bad')`, reload → page loads with clean state (no crash).
  12. Add −$999 mod → verify 加成後小計 clamps to $0.00 with "⚠️" warning visible.

  **QA scenarios (happy + failure):**
  - Happy: Standard order — 3 red, 1 shrimptempura, 1 chawanmushi, 1 pepsi. Verify bill.
  - Happy: Discount coupon −$50. Verify 加成後小計 before/after.
  - Failure: NaN in mod value → inline error, no addition.
  - Failure: Negative 加成 → clamp + warning.
  - Failure: corrupt localStorage → clean reset.
  - Failure: modal close via backdrop click → no state change.
  Evidence: `.omo/evidence/sushiro-calculator-selftest.txt` + QA screenshots (optional, one per scenario via Playwright).

  **Commit:** Y | `feat(sushiro-calculator): 壽司郎香港價錢計算器 — single-file index.html`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — does `index.html` match the plan's Scope? No missing/extra features?
- [x] F2. Code quality review — no AI slop, no dead code, no unnecessary abstraction, no framework / deps / build
- [x] F3. Real manual QA — open in real browser, run self-test, click through all flows (agent-executed with Playwright)
- [x] F4. Scope fidelity — double-check Must NOT have: no free-form other entry, no multi-person/budget/stamps, no service toggle, no in-UI price editor

## Commit strategy
- Single commit: `feat(sushiro-calculator): 壽司郎香港價錢計算器 — single-file index.html`
- One atomic commit, no WIP, no fixup. Clean `git status` before commit (only `index.html` + `.omo/` artifacts).

## Success criteria
1. `index.html` exists, opens in any browser, works offline (no network required).
2. Red/silver/gold/black plate steppers work correctly with live subtotals.
3. 其他 modal opens, allows selecting predefined items with qty adjustment, closes and updates main page.
4. 加成/折扣 panel supports +/- and $/%, updates bill correctly.
5. Compute order: 小計 → 加成調整 → 加成後小計(clamp≥0) → 服務費10% → 總計, all 2-decimal rounded.
6. localStorage persists state across refresh; 重置 clears it.
7. `window.__selftest()` passes all assertions.
8. File is single self-contained HTML, no deps, no build, no external requests.
