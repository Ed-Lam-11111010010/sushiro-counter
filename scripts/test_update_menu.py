import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_menu


def menu_with(*categories):
    return {"schemaVersion": 1, "categories": dict(categories)}


class MenuIdentityTests(unittest.TestCase):
    def test_future_ids_are_deterministic_and_category_independent(self):
        name = "未來柚子胡椒烏冬"
        expected = "item_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        self.assertEqual(update_menu.stable_id(name, {}), expected)
        self.assertEqual(update_menu.stable_id(name, {}), update_menu.stable_id(name, {}))

    def test_existing_id_survives_category_moves_and_price_changes(self):
        name = "未來蟹肉烏冬"
        existing = menu_with(("舊分類", [{"id": "retained-id", "name": name, "price": 26}]))
        existing_ids = update_menu.load_existing_ids(existing)
        moved_item_id = update_menu.stable_id(name, existing_ids)
        repriced_item_id = update_menu.stable_id(name, existing_ids)
        self.assertEqual(moved_item_id, "retained-id")
        self.assertEqual(repriced_item_id, moved_item_id)

    def test_duplicate_ids_across_categories_are_rejected(self):
        data = menu_with(
            ("新分類 A", [{"id": "same-id", "name": "菜色 A", "price": 10}]),
            ("新分類 B", [{"id": "same-id", "name": "菜色 B", "price": 12}]),
        )
        with self.assertRaisesRegex(ValueError, "Duplicate item id: same-id"):
            update_menu.validate_menu(data)

    def test_invalid_prices_are_rejected(self):
        for price in (-1, float("nan"), float("inf"), True, "18"):
            with self.subTest(price=price):
                data = menu_with(("測試分類", [{"id": "item-id", "name": "測試菜式", "price": price}]))
                with self.assertRaisesRegex(ValueError, "Invalid price"):
                    update_menu.validate_menu(data)

    def test_blank_item_ids_and_names_are_rejected(self):
        invalid_items = (
            {"id": "  ", "name": "測試菜式", "price": 18},
            {"id": "item-id", "name": "  ", "price": 18},
        )
        for item in invalid_items:
            with self.subTest(item=item):
                data = menu_with(("測試分類", [item]))
                with self.assertRaises(ValueError):
                    update_menu.validate_menu(data)


class PromotionDataTests(unittest.TestCase):
    def test_structured_promotions_are_valid_and_use_explicit_ids(self):
        data = json.loads(update_menu.PROMOTIONS_PATH.read_text(encoding="utf-8"))
        update_menu.validate_promotion_data(data)
        categories = update_menu.active_promotion_categories(data, update_menu.date(2026, 11, 25))
        self.assertIn("期間限定・赤醋飯祭／京都伊根生黑鮪（2026年11月3日起）", categories)
        self.assertIn("期間限定・赤醋飯祭同場加映（2026年11月24日至2026年12月1日）", categories)
        self.assertEqual(categories["期間限定・赤醋飯祭同場加映（2026年11月24日至2026年12月1日）"][0]["id"], "salt_grilled_wagyu_short_rib")

    def test_approved_jujutsu_offsite_promotions_follow_item_start_dates(self):
        data = json.loads(update_menu.PROMOTIONS_PATH.read_text(encoding="utf-8"))
        approved = [p for p in data["promotions"] if p["id"].startswith("jujutsu-")]
        self.assertEqual(len(approved), 2)
        self.assertTrue(all(p["verificationStatus"] == "verified" for p in approved))
        self.assertTrue(all(item["verificationStatus"] == "verified" for p in approved for item in p["items"]))
        self.assertTrue(all("instagram.com/p/" in p["sourceUrls"][0] for p in approved))
        today = update_menu.active_promotion_categories(data, update_menu.date(2026, 11, 22))
        future = update_menu.active_promotion_categories(data, update_menu.date(2026, 12, 1))
        first_category = "期間限定・咒術迴戰聯乘（2026年11月17日起）"
        second_category = "期間限定・咒術迴戰聯乘（2026年12月1日起）"
        self.assertEqual([(i["id"], i["price"]) for i in today[first_category]], [
            ("jujutsu_salt_marinated_albacore_202611", 17),
            ("jujutsu_black_sesame_mille_crepe_202611", 38),
            ("jujutsu_drink_set_202611", 59),
        ])
        self.assertNotIn(second_category, today)
        self.assertEqual(len(future[second_category]), 1)
        self.assertEqual(future[second_category][0]["price"], 17)
        self.assertFalse(any("襟章" in i["name"] or "立牌" in i["name"] for items in future.values() for i in items))

    def test_until_sold_out_promotion_does_not_expire_at_month_end(self):
        promotion = {
            "id": "until-sold-out",
            "category": "期間限定・售罄前",
            "verificationStatus": "verified",
            "sourceUrls": ["https://example.test/post"],
            "startDate": "2026-11-03",
            "endDate": None,
            "items": [{"id": "until-sold-out-item", "name": "限定品", "price": 22, "verificationStatus": "verified"}],
        }
        self.assertTrue(update_menu.promotion_is_active(promotion, update_menu.date(2026, 12, 31)))

    def test_future_and_review_only_promotions_are_not_active(self):
        base = {
            "id": "not-live",
            "category": "期間限定・未上架",
            "sourceUrls": ["https://example.test/post"],
            "startDate": "2026-12-01",
            "endDate": "2026-12-31",
            "items": [{"id": "not-live-item", "name": "未上架品", "price": 22, "verificationStatus": "verified"}],
        }
        future = {**base, "verificationStatus": "verified"}
        needs_review = {**base, "verificationStatus": "needs_review"}
        self.assertFalse(update_menu.promotion_is_active(future, update_menu.date(2026, 11, 30)))
        self.assertTrue(update_menu.promotion_is_active(future, update_menu.date(2026, 12, 1)))
        self.assertTrue(update_menu.promotion_is_active(future, update_menu.date(2026, 12, 2)))
        self.assertFalse(update_menu.promotion_is_active(needs_review, update_menu.date(2026, 12, 2)))

    def test_unverified_item_never_enters_menu_under_verified_promotion(self):
        data = {
            "schemaVersion": 1,
            "promotions": [{
                "id": "item-review-gate",
                "category": "期間限定・項目待審核",
                "verificationStatus": "verified",
                "sourceUrls": ["https://example.test/post"],
                "startDate": "2026-11-01",
                "endDate": None,
                "items": [{
                    "id": "item-review-only",
                    "name": "待審核品項",
                    "price": 22,
                    "verificationStatus": "needs_review",
                }],
            }],
        }
        update_menu.validate_promotion_data(data)
        self.assertFalse(update_menu.promotion_is_active(data["promotions"][0], update_menu.date(2026, 11, 17)))
        self.assertEqual(update_menu.active_promotion_categories(data, update_menu.date(2026, 11, 17)), {})

        missing_status = json.loads(json.dumps(data))
        del missing_status["promotions"][0]["items"][0]["verificationStatus"]
        with self.assertRaisesRegex(ValueError, "verification status"):
            update_menu.validate_promotion_data(missing_status)

    def test_invalid_promotion_file_does_not_remove_existing_seasonal_data(self):
        existing = menu_with(("期間限定・2026年10月舊活動", [{"id": "old-special", "name": "仍有效品項", "price": 18}]))
        invalid = {"schemaVersion": 1, "promotions": "not-a-list"}

        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing-promotions.json"
            invalid_path = Path(directory) / "invalid-promotions.json"
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
            def fake_fetch(url):
                if url == update_menu.SOURCE_URLS["甜品・飲料"]:
                    return [
                        {"name": "甜品", "price": 12},
                        {"name": "可樂", "price": 8},
                    ]
                return [{"name": "常設品項", "price": 18}]

            for promotions_path in (missing_path, invalid_path):
                with self.subTest(promotions_path=promotions_path.name), patch.object(
                    update_menu, "PROMOTIONS_PATH", promotions_path
                ), patch.object(update_menu, "fetch_items", side_effect=fake_fetch):
                    result = update_menu.make_menu(existing)
                self.assertIn("期間限定・2026年10月舊活動", result["categories"])

    def test_pending_same_category_does_not_remove_existing_live_data(self):
        category = "期間限定・2026年10月同名活動"
        existing = menu_with((category, [{"id": "old-special", "name": "原有有效品項", "price": 18}]))
        pending = {
            "schemaVersion": 1,
            "promotions": [{
                "id": "pending-same-category",
                "category": category,
                "verificationStatus": "needs_review",
                "sourceUrls": ["https://example.test/post"],
                "startDate": "2026-10-01",
                "endDate": "2026-10-31",
                "items": [{
                    "id": "pending-item",
                    "name": "待審核新項目",
                    "price": 22,
                    "verificationStatus": "needs_review",
                }],
            }],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "promotions.json"
            path.write_text(json.dumps(pending), encoding="utf-8")

            def fake_fetch(url):
                if url == update_menu.SOURCE_URLS["甜品・飲料"]:
                    return [{"name": "甜品", "price": 12}, {"name": "可樂", "price": 8}]
                return [{"name": "常設品項", "price": 18}]

            with patch.object(update_menu, "PROMOTIONS_PATH", path), patch.object(
                update_menu, "fetch_items", side_effect=fake_fetch
            ):
                result = update_menu.make_menu(existing)

        self.assertIn(category, result["categories"])
        self.assertEqual(result["categories"][category][0]["id"], "old-special")

    def test_future_same_category_preserves_old_data_until_start_date(self):
        category = "期間限定・2026年10月同名未來活動"
        existing = menu_with((category, [{"id": "old-special", "name": "現有品項", "price": 18}]))
        future = {
            "schemaVersion": 1,
            "promotions": [{
                "id": "future-same-category",
                "category": category,
                "verificationStatus": "verified",
                "sourceUrls": ["https://example.test/post"],
                "startDate": "2026-12-01",
                "endDate": "2026-12-31",
                "items": [{
                    "id": "future-item",
                    "name": "未來品項",
                    "price": 22,
                    "verificationStatus": "verified",
                }],
            }],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "promotions.json"
            path.write_text(json.dumps(future), encoding="utf-8")

            def fake_fetch(url):
                if url == update_menu.SOURCE_URLS["甜品・飲料"]:
                    return [{"name": "甜品", "price": 12}, {"name": "可樂", "price": 8}]
                return [{"name": "常設品項", "price": 18}]

            with patch.object(update_menu, "PROMOTIONS_PATH", path), patch.object(
                update_menu, "fetch_items", side_effect=fake_fetch
            ):
                result = update_menu.make_menu(existing)

        self.assertEqual(result["categories"][category][0]["id"], "old-special")

    def test_expired_and_rejected_promotions_do_not_preserve_old_month_data_forever(self):
        category = "期間限定・2026年10月已結束活動"
        existing = menu_with((category, [{"id": "old-special", "name": "已結束品項", "price": 18}]))

        for status in ("expired", "rejected"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "promotions.json"
                path.write_text(json.dumps({
                    "schemaVersion": 1,
                    "promotions": [{
                        "id": f"{status}-promotion",
                        "category": category,
                        "verificationStatus": status,
                        "sourceUrls": ["https://example.test/post"],
                        "startDate": "2026-10-01",
                        "endDate": "2026-10-31",
                        "items": [{
                            "id": f"{status}-item",
                            "name": "已結束品項",
                            "price": 18,
                            "verificationStatus": "verified",
                        }],
                    }],
                }), encoding="utf-8")

                def fake_fetch(url):
                    if url == update_menu.SOURCE_URLS["甜品・飲料"]:
                        return [{"name": "甜品", "price": 12}, {"name": "可樂", "price": 8}]
                    return [{"name": "常設品項", "price": 18}]

                with patch.object(update_menu, "PROMOTIONS_PATH", path), patch.object(
                    update_menu, "fetch_items", side_effect=fake_fetch
                ):
                    result = update_menu.make_menu(existing)

                self.assertNotIn(category, result["categories"])



class FallbackSyncRegressionTests(unittest.TestCase):
    def test_current_fallback_without_semicolon_is_replaced_and_idempotent(self):
        """Regression for the production failure on 2026-11-23."""
        original = update_menu.INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn("const FALLBACK_OTHER_ITEMS = ", original)
        self.assertIn("\n\nlet OTHER_ITEMS = FALLBACK_OTHER_ITEMS;", original)
        menu = json.loads(update_menu.MENU_PATH.read_text(encoding="utf-8"))
        categories = dict(menu["categories"])
        categories["期間限定・未來上架的測試菜式"] = [
            {"id": "new-seasonal-regression", "name": "測試新品", "price": 27}
        ]

        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.html"
            index.write_text(original, encoding="utf-8")
            with patch.object(update_menu, "INDEX_PATH", index):
                changed = update_menu.sync_fallback_menu({"categories": categories})
                self.assertTrue(changed)
                updated = index.read_text(encoding="utf-8")
                prefix, remainder = original.split("const FALLBACK_OTHER_ITEMS = ", 1)
                suffix = "let OTHER_ITEMS = FALLBACK_OTHER_ITEMS;" + remainder.split(
                    "let OTHER_ITEMS = FALLBACK_OTHER_ITEMS;", 1
                )[1]
                self.assertTrue(updated.startswith(prefix))
                self.assertTrue(updated.endswith(suffix))
                body = updated.split("const FALLBACK_OTHER_ITEMS = ", 1)[1].split(
                    ";\n\nlet OTHER_ITEMS = FALLBACK_OTHER_ITEMS;", 1
                )[0]
                self.assertEqual(json.loads(body), categories)
                self.assertFalse(update_menu.sync_fallback_menu({"categories": categories}))

    def test_old_fallback_semicolon_is_also_supported(self):
        old = (
            "const FALLBACK_OTHER_ITEMS = {\n"
            "  '舊分類': [{ id:'old-item', name:'舊菜', price:12 }]\n"
            "};\n\nlet OTHER_ITEMS = FALLBACK_OTHER_ITEMS;\n"
            "// important UI code remains unchanged\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.html"
            index.write_text(old, encoding="utf-8")
            categories = {"副餐類": [{"id": "new-item", "name": "新菜", "price": 22}]}
            with patch.object(update_menu, "INDEX_PATH", index):
                self.assertTrue(update_menu.sync_fallback_menu({"categories": categories}))
            self.assertIn("新菜", index.read_text(encoding="utf-8"))
            self.assertIn("// important UI code remains unchanged", index.read_text(encoding="utf-8"))

    def test_missing_or_duplicate_fallback_anchor_fails_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.html"
            text = "const FALLBACK_OTHER_ITEMS = {};\n\n// Missing second anchor\n"
            index.write_text(text, encoding="utf-8")
            with patch.object(update_menu, "INDEX_PATH", index):
                with self.assertRaisesRegex(ValueError, "Could not locate"):
                    update_menu.sync_fallback_menu({"categories": {"副餐類": [{"id":"x","name":"品項","price":1}]}})
            self.assertEqual(index.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
