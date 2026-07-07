# Sushiro Calculator — Verification Evidence

## Test environment
- File: `C:\Users\HP\Documents\sushiro-counter\index.html` (25,994 bytes)
- Browser: Playwright Chromium
- Server: python -m http.server 8765 (for file:// protocol workaround)
- Date: 2026-07-07

## Self-test results (window.__selftest())
All 8 assertions PASSED:
- ✅ 小計=102 (got 102)
- ✅ 加成調整=-20 (got -20)
- ✅ 加成後小計=82 (got 82)
- ✅ 服務費=8.20 (got 8.2)
- ✅ 總計=90.20 (got 90.2)
- ✅ clamp 加成後小計=0 (got 0)
- ✅ clamped flag=true
- ✅ zero display: $0.00 (got $0.00)

## Node logic test (5 cases)
All 5 PASSED (see .omo/evidence/test-compute.js):
- ✅ Standard bill (red×2 + black×1 + chawanmushi + tonkotsu_ramen − $20)
- ✅ Negative clamp (−$999 → adjusted $0, clamped=true)
- ✅ Percent discount (−10%)
- ✅ Markup (+$30)
- ✅ All zeros

## Browser interaction tests (Playwright)
1. ✅ Page loads without JS errors (only harmless favicon 404)
2. ✅ Red [+] ×3 → count=3, subtotal=$36.00, bill total=$39.60
3. ✅ Red [−] ×1 → count=2, subtotal=$24.00
4. ✅ Modal: open → click 茶碗蒸 → 完成 → summary shows "茶碗蒸×1"
5. ✅ Bill updates: subtotal=$43.00 (red×2=$24 + chawanmushi=$19), total=$47.30
6. ✅ Modification: label="優惠券", sign=−, unit=$, value=20 → list shows "優惠券  −$20"
7. ✅ Bill with mod: adjustment=−$20, adjusted=$23.00, total=$25.30
8. ✅ Negative clamp: −$999 → adjusted=$0.00, warning visible (display=block), total=$0.00
9. ✅ Reset: confirm dialog → all counts 0, other empty, mods empty, total=$0.00, warning hidden
10. ✅ localStorage persistence: set red=2, black=1 → reload → state restored (red=2, black=1, total=$56.10)
11. ✅ Corrupt localStorage: set "corrupted-data-not-json" → reload → clean state, no crash
12. ✅ Invalid mod value (empty): error message visible, nothing added to list

## Final state verification
Demo state: red=3, gold=1, 茶碗蒸×2, 壽司郎經典布甸×1, −$10 優惠券
- 小計 = $118 (36+22+38+22)
- 加成調整 = −$10
- 加成後小計 = $108
- 服務費 = $10.80
- 總計 = $118.80 ✅

## Conclusion
All acceptance criteria from the plan met. The calculator is fully functional.
