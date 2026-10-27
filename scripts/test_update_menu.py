import hashlib
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
