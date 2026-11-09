#!/usr/bin/env python3
"""Fetch and validate the Sushiro HK menu used by the static PWA."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

REPO_URL = "https://github.com/Ed-Lam-11111010010/sushiro-counter"
MENU_PATH = Path(__file__).resolve().parents[1] / "menu.json"
INDEX_PATH = MENU_PATH.parent / "index.html"
SOURCE_URLS = {
    "麵類・湯類": "https://sushirohk.com.hk/tc/menu.php?cid=11&wid=5",
    "副餐類": "https://sushirohk.com.hk/tc/menu.php?cid=13&wid=5",
    "甜品・飲料": "https://sushirohk.com.hk/tc/menu.php?cid=15&wid=5",
}

FETCH_ATTEMPTS = 3
HONG_KONG_TZ = timezone(timedelta(hours=8), "Asia/Hong_Kong")
SPECIAL_SOURCE_URLS = (
    "https://www.instagram.com/sushiro.hk/",
    "https://sushirohk.com.hk/tc/index.php",
)
CURATED_SPECIAL_CATEGORIES: dict[str, list[dict[str, int | str]]] = {
    # The official posts describe this campaign as available from 3 November
    # until sold out. The month in the category name gives it a safe automatic
    # expiry at the end of November if no newer campaign replaces it.
    "期間限定・赤醋飯祭／京都伊根生黑鮪（2026年11月3日起）": [
        {"name": "京都伊根生黑鮪吞拿魚二貫盛", "price": 32},
        {"name": "京都伊根生黑鮪大吞拿魚腩", "price": 25},
        {"name": "山葵莖Wasabi", "price": 6},
        {"name": "蟹肉雙味山", "price": 27},
        {"name": "鳥取產醋漬小肌", "price": 12},
        {"name": "炙燒日本產伊佐木", "price": 12},
        {"name": "京都伊根生黑鮪吞拿魚蓉海苔包", "price": 17},
        {"name": "脆脆蝦手卷配蟹柳沙律", "price": 17},
        {"name": "特大赤貝", "price": 22},
        {"name": "南蠻蝦配三文魚子", "price": 22},
        {"name": "百香果慕絲蛋糕", "price": 22},
        {"name": "菇菇羹茶碗蒸", "price": 22},
        {"name": "炸蕃薯條配蜂蜜&牛油", "price": 22},
        {"name": "汁煮鮑魚與鮑魚肝天婦羅盛(大)", "price": 88},
    ],
    "期間限定・赤醋飯祭同場加映（2026年11月24日至2026年12月1日）": [
        {"name": "鹽燒黑毛和牛肩胛小排", "price": 27},
        {"name": "喜之次雙味（炙燒・醬烤）", "price": 27},
        {"name": "生三文魚配蟹柳沙律", "price": 22},
    ],
}

NAV_LABELS = {
    "Sushiro HK", "菜單", "握壽司", "軍艦・卷物", "麵類・湯類",
    "副餐類", "甜品・飲料", "外賣自取", "嚴選二貫", "店舖一覽",
    "香港", "九龍", "新界", "人才招募", "立即訂位",
}
PRICE_RE = re.compile(r"^(?:HK\s*\$|\$)\s*(\d+(?:\.\d+)?)$", re.IGNORECASE)
DRINK_KEYWORDS = (
    "可樂", "美年達", "七喜", "檸檬茶", "蘋果汁", "可爾必思",
    "纖解茶", "菊正宗", "Horoyoi", "啤酒", "梅酒", "威士忌", "梳打",
)
KNOWN_IDS = {
    "出汁蒸蜆": "steamed_clams",
    "蜆肉海苔麵豉湯": "clam_miso",
    "腐皮烏冬": "tofu_udon",
    "釜玉烏冬": "kamadama_udon",
    "炸蝦天婦羅烏冬": "tempura_udon",
    "豚骨拉麵": "tonkotsu_ramen",
    "辣豚骨拉麵": "spicy_ramen",
    "牛肉烏冬": "beef_udon",
    "明太子沙律醬": "mentaiko",
    "溫泉蛋": "onsen_tamago",
    "脆脆炸雞塊 1塊": "chicken_karaage_1",
    "黃金脆薯": "golden_fries",
    "南瓜天婦羅": "pumpkin_tempura",
    "炸蝦天婦羅拼盤(半份)": "shrimp_tempura_half",
    "炸蝦天婦羅": "shrimp_tempura",
    "炸雞軟骨": "fried_cartilage",
    "茶碗蒸": "chawanmushi",
    "炸蝦天婦羅拼盤": "shrimp_tempura_full",
    "脆脆炸雞塊 2塊": "chicken_karaage_2",
    "店內特製鰻魚茶碗蒸": "unagi_chawanmushi",
    "北寄貝刺身": "akagai_sashimi",
    "甜蝦刺身": "sweet_shrimp_sashimi",
    "海螺刺身": "conch_sashimi",
    "吞拿魚2種刺身": "tuna_sashimi_set",
    "生三文魚刺身盛": "salmon_sashimi_set",
    "赤蝦刺身": "akaebi_sashimi",
    "日本產帆立貝刺身": "hotate_sashimi",
    "生三文魚腩刺身": "salmon_belly_sashimi",
    "生三文魚腩刺身・油甘魚腩刺身": "salmon_hamachi_sashimi",
    "蜜瓜味雪葩": "melon_sorbet",
    "冷凍芒果": "frozen_mango",
    "黃豆粉蕨餅": "soy_warabimochi",
    "朱古力蒙布朗蛋糕": "choco_montblanc",
    "牛奶味軟雪糕(有曲奇)": "milk_soft_cream_cookie",
    "牛奶味軟雪糕": "milk_soft_cream",
    "卡達拉娜": "crema_catalana",
    "壽司郎經典布甸": "sushiro_pudding",
    "千層蛋糕": "crepe_cake",
    "黃豆粉蕨餅芭菲": "soy_warabi_parfait",
    "朱古力香莓芭菲": "choco_berry_parfait",
    "百事可樂(無糖)": "pepsi_zero",
    "百事可樂": "pepsi",
    "美年達橙汁": "mirinda_orange",
    "七喜": "7up",
    "維他氣泡檸檬茶": "vita_lemon_tea",
    "100％蘋果汁": "apple_juice",
    "可爾必思": "calpico",
    "纖解茶": "sunsay_tea",
    "菊正宗": "kikumasamune",
    "Horoyoi(葡萄味)": "horoyoi_grape",
    "Horoyoi(檸檬蜜柑味)": "horoyoi_yuzu",
    "三得利頂級啤酒": "suntory_beer",
    "梅酒(含梅子)": "umeshu",
    "威士忌梳打": "whisky_soda",
    "菊正宗(大吟釀)": "kikumasamune_daiginjo",
    "京都伊根生黑鮪吞拿魚二貫盛": "kyoto_ine_bluefin_two",
    "京都伊根生黑鮪大吞拿魚腩": "kyoto_ine_bluefin_otoro",
    "山葵莖Wasabi": "wasabi_stem",
    "蟹肉雙味山": "crab_duo_mountain",
    "鳥取產醋漬小肌": "tottori_kohada",
    "炙燒日本產伊佐木": "aburi_isaki",
    "京都伊根生黑鮪吞拿魚蓉海苔包": "bluefin_toro_nori_wrap",
    "脆脆蝦手卷配蟹柳沙律": "crispy_shrimp_handroll",
    "特大赤貝": "large_akagai",
    "南蠻蝦配三文魚子": "nanban_shrimp_salmon_roe",
    "百香果慕絲蛋糕": "passionfruit_mousse_cake",
    "菇菇羹茶碗蒸": "mushroom_chawanmushi",
    "炸蕃薯條配蜂蜜&牛油": "sweet_potato_honey_butter",
    "汁煮鮑魚與鮑魚肝天婦羅盛(大)": "abalone_liver_tempura_platter",
    "鹽燒黑毛和牛肩胛小排": "salt_grilled_wagyu_short_rib",
    "喜之次雙味（炙燒・醬烤）": "kinki_two_flavors",
    "生三文魚配蟹柳沙律": "salmon_crab_stick_salad",
}


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", html.unescape(data)).strip()
        if text:
            self.tokens.append(text)


def normalize(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def parse_items(page: str) -> list[dict[str, int | str]]:
    parser = TextCollector()
    parser.feed(page)
    items: list[dict[str, int | str]] = []
    previous: str | None = None
    pending_currency = False

    def add_item(name: str | None, price_text: str) -> None:
        if not name:
            return
        name = normalize(name)
        if not name or name.lower() == "image" or name in NAV_LABELS:
            return
        if name.startswith("菜單 >") or "各分店所提供" in name:
            return
        price = float(price_text)
        item = {"name": name, "price": int(price) if price.is_integer() else price}
        if item not in items:
            items.append(item)

    for raw_token in parser.tokens:
        token = normalize(raw_token)
        compact = token.replace(" ", "")
        match = PRICE_RE.match(compact)
        if match:
            add_item(previous, match.group(1))
            previous = None
            pending_currency = False
            continue
        if compact in {"HK$", "$"}:
            pending_currency = True
            continue
        if pending_currency and re.fullmatch(r"\d+(?:\.\d+)?", compact):
            add_item(previous, compact)
            previous = None
            pending_currency = False
            continue
        if token.lower() == "image" or token in NAV_LABELS:
            continue
        previous = token

    if len(items) < 3:
        raise ValueError(f"Only found {len(items)} priced menu items")
    return items


def fetch(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; sushiro-counter-menu-updater/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="strict")


def fetch_items(url: str) -> list[dict[str, int | str]]:
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return parse_items(fetch(url))
        except (OSError, UnicodeError, ValueError) as exc:
            last_error = exc
            if attempt < FETCH_ATTEMPTS:
                time.sleep(2 ** (attempt - 1))
    raise ValueError(
        f"Could not parse {url} after {FETCH_ATTEMPTS} attempts: {last_error}"
    )


def split_desserts_and_drinks(items: list[dict[str, int | str]]) -> dict[str, list[dict[str, int | str]]]:
    desserts: list[dict[str, int | str]] = []
    drinks: list[dict[str, int | str]] = []
    for item in items:
        if any(keyword in str(item["name"]) for keyword in DRINK_KEYWORDS):
            drinks.append(item)
        else:
            desserts.append(item)
    if not desserts or not drinks:
        raise ValueError("Could not split dessert and drink menu into two non-empty categories")
    return {"甜品": desserts, "飲料": drinks}


def load_existing_data() -> dict[str, object]:
    if not MENU_PATH.exists():
        return {}
    try:
        data = json.loads(MENU_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_existing_ids(data: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for items in data.get("categories", {}).values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("name") and item.get("id"):
                result[str(item["name"])] = str(item["id"])
    return result


def stable_id(name: str, existing_ids: dict[str, str]) -> str:
    return (
        existing_ids.get(name)
        or KNOWN_IDS.get(name)
        or "item_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    )


def seasonal_is_active(category: str, today: date) -> bool:
    range_match = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日至(\d{4})年(\d{1,2})月(\d{1,2})日",
        category,
    )
    if range_match:
        start = date(*(int(range_match.group(index)) for index in (1, 2, 3)))
        end = date(*(int(range_match.group(index)) for index in (4, 5, 6)))
        return start <= today <= end

    match = re.search(r"(\d{4})年(\d{1,2})月", category)
    if not match:
        return True
    year, month = int(match.group(1)), int(match.group(2))
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return today < next_month


def make_menu(existing: dict[str, object]) -> dict[str, object]:
    existing_ids = load_existing_ids(existing)
    today = datetime.now(HONG_KONG_TZ).date()
    noodles = fetch_items(SOURCE_URLS["麵類・湯類"])
    sides = fetch_items(SOURCE_URLS["副餐類"])
    dessert_drink = fetch_items(SOURCE_URLS["甜品・飲料"])

    raw_categories = {
        "麵類・湯類": noodles,
        "副餐類": sides,
        **split_desserts_and_drinks(dessert_drink),
        **{
            category: items
            for category, items in CURATED_SPECIAL_CATEGORIES.items()
            if seasonal_is_active(category, today)
        },
    }
    categories: dict[str, list[dict[str, int | str]]] = {}
    for category, items in raw_categories.items():
        categories[category] = [
            {
                "id": stable_id(str(item["name"]), existing_ids),
                "name": str(item["name"]),
                "price": item["price"],
            }
            for item in items
        ]

    for category, items in existing.get("categories", {}).items():
        if (
            category not in CURATED_SPECIAL_CATEGORIES
            and category.startswith("期間限定")
            and isinstance(items, list)
            and seasonal_is_active(category, today)
        ):
            categories[category] = items

    return {
        "schemaVersion": 1,
        "region": "hk",
        "currency": "HKD",
        "updatedAt": datetime.now(HONG_KONG_TZ).strftime("%Y-%m-%d"),
        "source": list(SOURCE_URLS.values()) + list(SPECIAL_SOURCE_URLS),
        "categories": categories,
    }


def format_fallback_menu(categories: dict[str, object]) -> str:
    lines = ["{"]
    category_items = list(categories.items())
    for category_index, (category, items) in enumerate(category_items):
        if not isinstance(items, list):
            raise ValueError(f"Invalid fallback category: {category}")
        category_json = json.dumps(category, ensure_ascii=False)
        lines.append(f"  {category_json}: [")
        for item_index, item in enumerate(items):
            item_json = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            comma = "," if item_index < len(items) - 1 else ""
            lines.append(f"    {item_json}{comma}")
        category_comma = "," if category_index < len(category_items) - 1 else ""
        lines.append(f"  ]{category_comma}")
    lines.append("}")
    return "\n".join(lines)


def sync_fallback_menu(data: dict[str, object]) -> bool:
    """Keep the offline menu in index.html aligned with menu.json."""
    if not INDEX_PATH.exists():
        return False

    text = INDEX_PATH.read_text(encoding="utf-8")
    end_match = re.search(r"\n};\n+let OTHER_ITEMS", text)
    if not end_match:
        raise ValueError("Could not locate the offline menu fallback in index.html")
    content_end = end_match.start()

    categories = data.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("Cannot sync fallback without menu categories")

    special_categories = {
        category: items
        for category, items in categories.items()
        if category.startswith("期間限定")
    }
    special_match = re.search(r"\n  ['\"]期間限定", text[:content_end])
    content_start = special_match.start() if special_match else content_end
    fallback = format_fallback_menu(special_categories)
    special_block = f"\n{fallback[2:-1]}" if fallback != "{}" else ""
    fallback_tail = "\n};\n\nlet OTHER_ITEMS" + text[end_match.end():]
    updated = text[:content_start] + special_block + fallback_tail
    if updated == text:
        return False

    INDEX_PATH.write_text(updated, encoding="utf-8")
    return True


def validate_menu(data: dict[str, object]) -> None:
    if data.get("schemaVersion") != 1 or not isinstance(data.get("categories"), dict):
        raise ValueError("Invalid menu schema")
    ids: set[str] = set()
    for category, items in data["categories"].items():
        if not isinstance(category, str) or not isinstance(items, list) or not items:
            raise ValueError(f"Invalid category: {category}")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Menu item is not an object")
            item_id = item.get("id")
            name = item.get("name")
            price = item.get("price")
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("Menu item has no id")
            if item_id in ids:
                raise ValueError(f"Duplicate item id: {item_id}")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Menu item has no name")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                raise ValueError(f"Invalid price for {item_id}")
            if isinstance(price, float) and not math.isfinite(price):
                raise ValueError(f"Invalid price for {item_id}")
            ids.add(item_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate the existing menu.json without fetching")
    args = parser.parse_args()
    existing = load_existing_data()

    if args.check:
        try:
            validate_menu(existing)
        except ValueError as exc:
            print(f"menu.json validation failed: {exc}", file=sys.stderr)
            return 1
        print("menu.json validation passed")
        return 0

    try:
        new_menu = make_menu(existing)
        validate_menu(new_menu)
        fallback_changed = sync_fallback_menu(new_menu)
    except Exception as exc:
        print(f"Menu update failed: {exc}", file=sys.stderr)
        return 1

    menu_changed = existing.get("categories") != new_menu.get("categories")
    if not menu_changed and not fallback_changed:
        print("No menu item, price, or fallback changes found")
        return 0

    if menu_changed:
        MENU_PATH.write_text(
            json.dumps(new_menu, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    total_items = sum(len(items) for items in new_menu["categories"].values())
    changed_files = [
        path
        for path, changed in (("menu.json", menu_changed), ("index.html", fallback_changed))
        if changed
    ]
    print(
        f"Updated {', '.join(changed_files)} "
        f"({total_items} items across {len(new_menu['categories'])} categories)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
