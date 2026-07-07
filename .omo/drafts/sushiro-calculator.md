---
slug: sushiro-calculator
status: approved 2026-07-07 (plan generation in progress)
intent: clear
pending-action: append todos + fill TL;DR in .omo/plans/sushiro-calculator.md
approach: single self-contained index.html, vanilla JS, no build, no deps. One-page main view (4 plate steppers + sticky bill + compact 其他 summary + 加成/折扣 panel) + 其他 modal overlay + localStorage. 10% service on 加成後小計. 繁中(HK).
---

# Draft: sushiro-calculator

## Components (topology ledger)

| id | outcome | status | evidence |
|----|---------|--------|----------|
| C1 | Main one-page: 4 plate steppers (紅12/銀17/金22/黑27), +/-, live per-tier count+subtotal, always visible | active | official menu cid=7 + 3 independent sources (websearch 2026-01) |
| C2 | Sticky live bill summary always on main page | active | user req "即時金額" |
| C3 | [其他] MODAL overlay: 4 chip groups (麵類・湯類8/副餐類21/甜品11/飲料15=55 items), click to add, qty stepper, remove, 完成 closes | active | official menu cid=11,13,15 verified 2026-07-07; user chose "預設常用清單" + "小分頁選擇" |
| C4 | Price-modification panel on main page: +/−, $/%, label?, removable; applies to 小計 before service; negative clamp at 0 | active | user req "加多一欄 for discount/小加成" |
| C5 | localStorage persistence + 重置 | active | adopted default (restaurant refresh safety) |

## Open assumptions (announced defaults)

| assumption | adopted default | rationale | reversible? |
|----|----|----|----|
| UI language | 繁體中文(HK) | user writes Cantonese/Traditional; Sushiro is HK | yes — edit strings |
| Plate prices editable in-UI? | NO — config object at file top | prices change rarely; YAGNI | yes — add editor later |
| 10% service always charged? | YES, always on (toggle not exposed) | matches HK restaurant convention + all reference calculators | yes — add toggle later if needed |
| Discount before or after service? | BEFORE service charge | HK convention (coupons apply to food subtotal, service on discounted) | yes — flip order in compute() |
| Negative 加成後小計? | clamp at 0 + inline warning | prevents nonsensical bills | no (safety) |
| Persistence | localStorage auto-save + 重置 button | accidental refresh safety at restaurant | yes |
| 其他 entry = predefined list only (no free-form)? | YES, predefined only | user explicitly chose 預設常用清單 over 自由輸入 | yes — add custom adder later |
| Styling | minimal inline <style>, mobile-first, no framework | single-file constraint, YAGNI | no (kept lazy) |

## Findings (cited - path:lines)

- Sushiro HK official menu, nigiri (cid=7): items at $12/$17/$22/$27. https://sushirohk.com.hk/tc/menu.php?wid=11&cid=7 (fetched 2026-07-07)
- 麵類・湯類 (cid=11): 8 items $18–$33. https://sushirohk.com.hk/tc/menu.php?wid=11&cid=11
- 副餐類 (cid=13): 21 items $3–$48. https://sushirohk.com.hk/tc/menu.php?wid=11&cid=13
- 甜品・飲料 (cid=15): 11 desserts $13–$27 + 15 drinks $8–$59. https://sushirohk.com.hk/tc/menu.php?wid=11&cid=15
- Competitor calculators (Dim Jeng, zhtoolbox, kansbestpick) confirm 紅$12/銀$17/金$22/黑$27 + 10% service. Reference only — NOT copied.
- Working dir C:\Users\HP\Documents\sushiro-counter is empty (only .codegraph/, .omo/). Greenfield, no existing patterns to match.

## Decisions (with rationale)

1. Single index.html, vanilla, no build — user chose; embeddable; shortest path.
2. 其他 = predefined list (not free-form) — user chose.
3. 其他 picker = modal overlay (not inline) — user chose "打開小分頁選擇".
4. 加成/折扣 panel on main page (not modal) — user said "加多一欄", implies always-visible column.
5. Discount before service charge — HK convention; defensible default.
6. 55 其他 items in 4 groups as editable config — verified data, user trims as wished.
7. No multi-person/budget/stamps/export — YAGNI.

## Scope IN

- One index.html: HTML + inline <style> + inline <script>.
- PLATES config (4) + OTHER_ITEMS config (55, 4 groups) at file top.
- Main page: 4 plate steppers, sticky bill, compact 其他 summary + 加入 button, 加成/折扣 panel, 重置.
- 其他 modal: 4 chip groups, click-add, qty steppers, remove, 完成.
- Bill compute: 小計→加成調整→加成後小計(clamp≥0)→服務費10%→總計, 2-decimal rounding.
- localStorage save/restore.
- Built-in self-test (console or hidden block) with fixed-input assertions.
- 繁中(HK) UI, mobile-first.

## Scope OUT (Must NOT have)

- NO build step, NO npm, NO framework, NO external CSS/JS files, NO dependencies.
- NO multi-person split, NO budget mode, NO stamp reminder, NO receipt/print/export.
- NO free-form 其他 entry (predefined list only — user chose this; do not re-add).
- NO in-UI plate price editor (config at file top only).
- NO service-charge toggle (always on — unless user later asks).
- NO backend, NO network calls, NO analytics.
- NO splitting index.html into multiple files.

## Open questions

None — all forks resolved, user approved 2026-07-07.

## Approval gate
status: approved 2026-07-07. User said "approve". Now generating plan file (Metis gap analysis running in background bg_7e2c7bcf; then append todos + fill TL;DR).
