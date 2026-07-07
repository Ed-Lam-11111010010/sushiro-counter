# sushiro-calculator — planning draft

- slug: sushiro-calculator
- intent routing: CLEAR (user knows the outcome: a Sushiro HK price calculator as a small web widget/page, plate colors as price tiers, an [其他] column for remaining dine-in items, +10% service charge)
- classify: Standard (1-3 files, clear feature)
- status: awaiting-approval (pending fork answers, then plan write)

## Verified facts (evidence)

Sushiro HK plate-color tiers — confirmed from the OFFICIAL menu page
(https://sushirohk.com.hk/tc/menu.php?wid=11&cid=7, nigiri category) which lists
items at exactly $12 / $17 / $22 / $27, and corroborated by 3 independent sources:
- 紅碟 Red   HK$12
- 銀碟 Silver HK$17
- 金碟 Gold   HK$22
- 黑碟 Black  HK$27

Dine-in NON-plate categories (these have varied prices, NOT color-tiered) — from
official menu nav (menu.php?wid=11&cid=23):
- 麵類・湯類 (cid=11)
- 副餐類 (cid=13)
- 甜品・飲料 (cid=15)
These are what the user means by "剩下的堂食單品" → the [其他] column.

Competitor feature landscape (web search, for reference only — NOT to copy):
- Dim Jeng, zhtoolbox, kansbestpick all exist. Common features: plate counters,
  side-item adder, 10% service toggle, some add multi-person split / budget / 印花.
- We will NOT build multi-person / budget / stamps (YAGNI — user did not ask).

## Topology (components ledger)

| id | outcome | status |
|----|---------|--------|
| C1 | Main one-page view: 4 plate-color steppers (red/silver/gold/black) with +/- , live per-tier count + subtotal, ALWAYS visible | designed |
| C2 | Sticky live total: 結算欄 always visible on main page (小計 / 服務費 / 加成後小計 / 總計), updates as you tap | designed |
| C3 | [其他] MODAL: opens on demand via "加入其他項目" button; shows 4 groups of chips; click chip to add, per-selected qty stepper, removable; close returns to main page. NOT inline. | designed |
| C4 | Price-modification section (NEW): one column on main page to add discount / 小型加成 line items; both $ and %, both + and -; applies to 小計 before service charge | designed |
| C5 | Persistence: localStorage save/restore + reset-all | designed |

## UI layout (revised per user 2026-07-07)

Main page = near one-page, always-visible:
  ┌───────────────────────────────┐
  │  壽司郎 HK 結算  [重置]        │
  │  ─────────────────────────    │
  │  紅碟 $12   − 0 +    $0        │  ← C1 plate steppers
  │  銀碟 $17   − 0 +    $0        │
  │  金碟 $22   − 0 +    $0        │
  │  黑碟 $27   − 0 +    $0        │
  │  ─────────────────────────    │
  │  其他項目 (0)  [加入其他項目]  │  ← C3 opens modal
  │   ↳ 已選: 茶碗蒸×1 ... (可刪)  │     (selected items shown compactly
  │  ─────────────────────────    │      on main page; full picker in modal)
  │  加成 / 折扣                    │  ← C4 price-modification section
  │   [label?] [+/-] [$ or %] [值] │
  │   [加入]                        │
  │   • 優惠券  −$20       [x]      │
  │   • 加購    +$30       [x]      │
  │  ─────────────────────────    │
  │  小計        $70.00             │  ← C2 sticky bill summary
  │  加成調整    −$20.00            │
  │  加成後小計  $50.00             │
  │  服務費 10%  $5.00              │
  │  總計        $55.00             │
  └───────────────────────────────┘

[其他] modal (overlay):
  - 4 sections (麵類・湯類 / 副餐類 / 甜品 / 飲料) as chip grids
  - chip click → adds to selection (qty=1), chip shows ✓
  - selected items list at bottom of modal with qty steppers + remove
  - "完成" button closes modal, returns to main page
  - main page shows compact "已選: 茶碗蒸×1, 豚骨拉麵×1, ..." summary

## Price-modification panel — adopted defaults (NEW, recorded)

- Supports BOTH directions: − (discount) and + (markup/小加成).
- Supports BOTH units: $ flat amount and % percent.
- Each modification = { label?, sign:'+'|'-', unit:'$'|'%', value:number }. Removable.
- Order of operations (HK convention, defensible default — recorded, NOT asked):
    1. platesSubtotal = sum(plate count × price)
    2. othersSubtotal = sum(other item qty × price)
    3. 小計 = platesSubtotal + othersSubtotal
    4. 加成調整 = apply all +/− modifications to 小計
       - $ mods: add/subtract flat
       - % mods: 小計 × (percent/100), summed
    5. 加成後小計 = 小計 + 加成調整  (floor at 0 — no negative bills)
    6. 服務費 = round(加成後小計 × 0.10, 2)
    7. 總計 = 加成後小計 + 服務費  (round to 2 decimals)
- Why before service charge: HK restaurant coupons/discounts apply to food
  subtotal, then service charge is computed on the discounted amount. This is
  the common convention. User can object here if they want mods applied after
  service charge instead — but defensible default adopted, no question asked.
- Negative-bill guard: if 加成調整 would push 加成後小計 below 0, clamp at 0
  and show a tiny inline warning. (prevents nonsensical output — not lazy-skip)

## Adopted defaults (ponytail / defensible — recorded, NOT asked)

- UI language: 繁體中文 (HK). User writes Cantonese/Traditional; Sushiro is HK.
- Plate prices: hardcoded as an editable config object at TOP of file
  (const PLATES = {red:{label:'紅碟',price:12,color:'#e53935'}, ...}).
  Editable by editing the file — NO in-UI price editor (YAGNI; prices change rarely).
- 10% service charge: service = round(subtotal * 0.10, 2); total = round(subtotal * 1.10, 2).
  Matches HK restaurant convention and all reference calculators.
- Persistence: localStorage (survives accidental refresh at the restaurant) + a 重置 button.
- NO extras: no multi-person split, no budget mode, no stamp reminder, no receipt export.
  Skipped — add when the user asks.
- Styling: minimal inline <style>, plate-colored chips, mobile-first, no CSS framework.
- Currency display: HK$ with 2 decimals where needed.

## Pending forks (to ask)

F1 (foggiest — most unblocking): tech stack / delivery form.
  - Why it forks: shapes the entire file structure and every todo.
  - Recommended default: single self-contained index.html (vanilla JS, no build).
  - Options: single HTML / Vite+vanilla small project / React+Vite.
F2: [其他] items entry UX.
  - Why it forks: shapes the Other component's data model + UI.
  - Recommended default: free-form (name + price, click 加入, removable list rows).
  - Options: free-form name+price / predefined common-items list / price-only adder.

## Fork answers (resolved)

- F1 = 單一 HTML 檔 (建議). Single self-contained index.html, vanilla JS, no build.
- F2 = 預設常用清單. Predefined common-items list (NOT free-form). Click an item
  chip to add; each selected item gets a quantity stepper; removable.
  - Design consequence: the [其他] panel is driven by a config object
    OTHER_ITEMS (grouped by category, each {name, price}), same editable-at-top
    pattern as PLATES. NO free-form name+price input (user did not pick it; do not
    re-ask). Add a custom-adder later only if user asks.
  - Honor the choice strictly (ponytail: no unrequested features).

## Compiled [其他] data — verified from official menu 2026-07-07

Source: https://sushirohk.com.hk/tc/menu.php?wid=11&cid={11,13,15}
NOTE to executor: prices verified on 2026-07-07; re-verify before any "refresh
prices" work. Store as editable config; user trims/edits as needed.

麵類・湯類 (cid=11, 8 items):
  出汁蒸蜆 18 | 蜆肉海苔麵豉湯 20 | 腐皮烏冬 27 | 釜玉烏冬 28 |
  炸蝦天婦羅烏冬 28 | 豚骨拉麵 32 | 辣豚骨拉麵 32 | 牛肉烏冬 33

副餐類 (cid=13, 21 items):
  明太子沙律醬 3 | 溫泉蛋 6 | 脆脆炸雞塊1塊 12 | 黃金脆薯 13 | 南瓜天婦羅 13 |
  炸蝦天婦羅拼盤(半份) 18 | 炸蝦天婦羅 18 | 炸雞軟骨 19 | 茶碗蒸 19 |
  脆脆炸雞塊2塊 22 | 店內特製鰻魚茶碗蒸 22 | 北寄貝刺身 22 | 甜蝦刺身 22 |
  海螺刺身 22 | 吞拿魚2種刺身 22 | 炸蝦天婦羅拼盤 28 | 生三文魚刺身盛 39 |
  赤蝦刺身 39 | 日本產帆立貝刺身 42 | 生三文魚腩刺身 48 |
  生三文魚腩刺身・油甘魚腩刺身 48

甜品 (cid=15 desserts, 11 items):
  蜜瓜味雪葩 13 | 冷凍芒果 13 | 黃豆粉蕨餅 13 | 朱古力蒙布朗蛋糕 17 |
  牛奶味軟雪糕(有曲奇) 18 | 牛奶味軟雪糕 18 | 卡達拉娜 22 | 壽司郎經典布甸 22 |
  千層蛋糕 22 | 黃豆粉蕨餅芭菲 27 | 朱古力香莓芭菲 27

飲料 (cid=15 drinks, 15 items):
  百事可樂(無糖) 8 | 百事可樂 8 | 美年達橙汁 8 | 七喜 8 | 維他氣泡檸檬茶 8 |
  100％蘋果汁 8 | 可爾必思 8 | 纖解茶 9 | 菊正宗 17 | Horoyoi(葡萄味) 19 |
  Horoyoi(柚子鹽味) 19 | 三得利頂級啤酒 24 | 梅酒(含梅子) 27 | 威士忌梳打 27 |
  菊正宗(大吟釀) 59

Total: 8 + 21 + 11 + 15 = 55 predefined 其他 items, grouped into 4 sections
(麵類・湯類 / 副餐類 / 甜品 / 飲料) for clean chip layout.

## Approval gate

- status: APPROVED 2026-07-07
- pending action: write .omo/plans/sushiro-calculator.md (scaffolding now)
- approach: ONE self-contained index.html (vanilla JS, no build, no deps).
  Top-of-file config: PLATES (4 colors) + OTHER_ITEMS (55 items, 4 groups).
  MAIN PAGE (one-page, always visible): 4 plate-color steppers (+/-, count,
  per-tier subtotal) + sticky bill summary (小計/加成調整/加成後小計/服務費10%/總計,
  2-decimal rounding) + compact "已選其他" summary row + "加入其他項目" button +
  加成/折扣 panel (label? + +/- + $/% + value + 加入; removable line items).
  [其他] MODAL (overlay, on demand): 4 chip groups, click to add, per-selected
  qty stepper, remove, 完成 to close.
  Modifications apply to 小計 BEFORE service charge (HK convention); negative
  加成後小計 clamped at 0 with inline warning.
  localStorage persistence + 重置 button. 繁體中文(HK) UI. Mobile-first inline
  CSS, no framework. No multi-person/budget/stamps/export (YAGNI).
- waiting on: explicit user approval to write the plan file.
