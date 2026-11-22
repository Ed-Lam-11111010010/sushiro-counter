#!/usr/bin/env python3
"""Discover Sushiro HK Instagram menu candidates without changing the live menu.

This detector only uses publicly exposed HTML and an optional, human-maintained
source file. It never calls private Instagram endpoints, bypasses a login, or
promotes a candidate into menu.json. Every candidate remains review-only.
"""

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
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from xml.etree import ElementTree
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = REPO_ROOT / "data" / "instagram_sources.json"
OUTPUT_PATH = REPO_ROOT / "data" / "instagram_candidates.json"
STATE_PATH = REPO_ROOT / "data" / "instagram_scan_state.json"
MENU_PATH = REPO_ROOT / "menu.json"
PROFILE_URL = "https://www.instagram.com/sushiro.hk/"
OFFICIAL_DISCOVERY_URL = "https://sushirohk.com.hk/tc/index.php"
PUBLIC_INDEX_FEED_URL = "https://www.bing.com/search?" + urlencode({
    "q": '"sushiro.hk" site:instagram.com',
    "format": "rss",
})
FETCH_ATTEMPTS = 2
MAX_DISCOVERED_POSTS = 24
OVERLAP_DAYS = 7
EXTRACTION_VERSION = "instagram-caption-v3"
HONG_KONG_TZ = timezone(timedelta(hours=8), "Asia/Hong_Kong")

POST_PATH_RE = re.compile(r"^/(p|reel|tv)/([^/?#]+)/?$", re.IGNORECASE)
OWNER_POST_PATH_RE = re.compile(r"^/([^/?#]+)/((?:p|reel|tv))/([^/?#]+)/?$", re.IGNORECASE)
HKD_PRICE_RE = re.compile(r"(?:HK\s*\$|HKD)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
CAPTION_PRICE_RE = re.compile(r"(?P<marker>HK\s*\$|HKD|\$)\s*(?P<amount>\d+(?:\.\d+)?)", re.IGNORECASE)
AMBIGUOUS_PRICE_RE = re.compile(r"(?:^|[^A-Za-z])\$\s*\d+(?:\.\d+)?")
ISO_DATE_RE = re.compile(r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})")
CHINESE_DATE_RE = re.compile(
    r"(?:(?P<year>\d{4})\s*年)?\s*"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?"
)
SALE_PERIOD_RE = re.compile(r"(?:📅\s*)?販售期間\s*[:：]?")
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")
PROMOTION_SECTION_RE = re.compile(r"🎬\s*同場加映|🔸\s*【|═{6,}|-{10,}")
DATE_CONFIDENCE = {"explicit-section", "manual-transcription", "not-stated", "ambiguous"}
POST_STATUSES = {"found", "needs_review", "future", "expired", "unavailable"}
SOURCE_STATUSES = {"posts_found", "profile_only", "manual_only", "partial", "unavailable", "available"}
PROFILE_FETCH_STATUSES = {"success", "unavailable"}
POST_DISCOVERY_STATUSES = {"found", "none", "manual_only", "partial", "unavailable"}
ITEM_TYPES = {"dish", "drink", "merchandise"}
PURCHASE_CONDITIONS = {"standard", "conditional-add-on", "unknown"}
PRICE_CONFIDENCE = {"explicit-hkd", "ambiguous-currency", "manual-transcription", "missing"}


def normalize(value: str) -> str:
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def canonical_post_url(value: str, base_url: str = PROFILE_URL) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    absolute = urljoin(base_url, value.strip()).split("#", 1)[0]
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or (host != "instagram.com" and not host.endswith(".instagram.com"))
    ):
        return None
    match = POST_PATH_RE.match(parsed.path)
    if match:
        return f"https://www.instagram.com/{match.group(1).lower()}/{match.group(2)}/"

    base_path_parts = [part for part in urlparse(base_url).path.split("/") if part]
    owner = base_path_parts[0] if base_path_parts and base_path_parts[0].lower() not in {"p", "reel", "tv"} else None
    owner_match = OWNER_POST_PATH_RE.match(parsed.path)
    if owner and owner_match and owner_match.group(1).casefold() == owner.casefold():
        return f"https://www.instagram.com/{owner_match.group(2).lower()}/{owner_match.group(3)}/"
    return None


def canonical_post_id(post_url: str) -> str:
    parts = [part for part in urlparse(post_url).path.split("/") if part]
    return parts[1] if len(parts) == 2 else ""


def content_fingerprint(caption: str, image_urls: list[str]) -> str:
    payload = json.dumps(
        {"caption": normalize(caption), "imageUrls": sorted(set(image_urls))},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_existing_menu_ids(path: Path = MENU_PATH) -> dict[str, str]:
    data = load_json(path) if path.exists() else None
    if not data or not isinstance(data.get("categories"), dict):
        return {}
    matches: dict[str, str] = {}
    for category_items in data["categories"].values():
        if not isinstance(category_items, list):
            continue
        for item in category_items:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("name"), str):
                matches[normalize(item["name"]).casefold()] = item["id"]
    return matches


def annotate_existing_menu_matches(candidate: dict[str, object], menu_ids: dict[str, str]) -> None:
    items = candidate.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        existing_id = menu_ids.get(normalize(item["name"]).casefold())
        if existing_id:
            item["existingMenuId"] = existing_id


class PublicPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        tag = tag.lower()
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if key and content and key.lower() not in self.metadata:
                self.metadata[key.lower()] = normalize(content)
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        elif tag == "img" and attributes.get("src"):
            self.images.append(attributes["src"])


def parse_public_page(page: str, base_url: str) -> dict[str, object]:
    parser = PublicPageParser()
    parser.feed(page)
    post_urls = sorted(
        {
            post_url
            for link in parser.links
            if (post_url := canonical_post_url(link, base_url)) is not None
        }
    )
    image_urls = sorted({urljoin(base_url, image) for image in parser.images if image.strip()})
    return {
        "metadata": parser.metadata,
        "postUrls": post_urls,
        "imageUrls": image_urls,
    }


def has_usable_post_metadata(post_url: str, post_data: dict[str, object]) -> bool:
    metadata = post_data.get("metadata")
    if not isinstance(metadata, dict):
        return False
    description = metadata.get("og:description")
    if not isinstance(description, str):
        return False
    normalized_description = normalize(description)
    if not normalized_description:
        return False
    generic_login_shells = (
        r"^log in to instagram\b",
        r"^sign up for instagram\b",
        r"^create an account to see photos and videos\b",
        r"^see posts, photos and more on instagram\b",
        r"^instagram photos and videos\s*$",
    )
    if any(re.search(pattern, normalized_description, re.IGNORECASE) for pattern in generic_login_shells):
        return False
    page_url = metadata.get("og:url")
    return not page_url or canonical_post_url(str(page_url), PROFILE_URL) == canonical_post_url(post_url, PROFILE_URL)



def discover_public_index_posts(feed: str) -> list[str]:
    """Best-effort direct Instagram post links from a public RSS search index.

    Indexed results are NOT proof of account ownership. The fetched post must
    also identify @sushiro.hk before it can become a review-only candidate.
    """
    try:
        document = ElementTree.fromstring(feed)
    except ElementTree.ParseError as exc:
        raise ValueError("Public search index did not return parseable RSS.") from exc
    if document.tag.lower() != "rss":
        raise ValueError("Public search index did not return an RSS document.")
    urls: set[str] = set()
    for item in document.findall(".//item"):
        link = item.findtext("link") or ""
        post_url = canonical_post_url(link, PROFILE_URL)
        if post_url:
            urls.add(post_url)
    return sorted(urls)


def indexed_post_owner_confirmed(metadata: dict[str, str]) -> bool:
    """Do not trust a search result alone to attribute a post to this account."""
    for field in ("og:title", "og:description"):
        value = normalize(metadata.get(field, ""))[:240]
        if re.search(r"@sushiro\.hk\b", value, re.IGNORECASE):
            return True
        if re.search(r"\bsushiro\.hk\s+on\s+instagram\b", value, re.IGNORECASE):
            return True
    return False


def extract_author_evidence(metadata: dict[str, str]) -> dict[str, str] | None:
    """Return a minimal public metadata citation proving the post author."""
    for field in ("article:author", "author", "og:title", "og:description"):
        value = normalize(metadata.get(field, ""))[:500]
        if not value:
            continue
        if field in {"article:author", "author"} and value.casefold().strip(" @/") == "sushiro.hk":
            return {
                "account": "sushiro.hk",
                "sourceField": field,
                "evidenceText": value[:100],
                "confidence": "high",
            }
        match = re.search(
            r"(?:^|[-–—]\s*|\()@?sushiro\.hk\s*(?:\)|\s+(?:建立於|created|on\s+instagram)\b)",
            value,
            re.IGNORECASE,
        )
        if match:
            return {
                "account": "sushiro.hk",
                "sourceField": field,
                "evidenceText": normalize(match.group(0)).strip(" -–—()")[:100],
                "confidence": "high",
            }
    return None


def caption_branch_evidence(caption: str) -> dict[str, str]:
    # "All branches ... except X" is NOT a universal-availability claim.
    # Check exclusions across the full caption before matching an earlier
    # "全線分店" phrase (the exception may occur much later).
    excluded = re.search(r"除外|不適用|不包括|不包含|不含|恕不供應|恕不適用", caption)
    restricted = re.search(r"只限|僅限|仅限|指定分店|分店限定|限定店舖|限定店铺", caption)
    if excluded or restricted:
        match = excluded or restricted
        assert match is not None
        result = {
            "branchScope": "restricted",
            "branchEvidenceText": normalize(caption[max(0, match.start() - 90) : match.end() + 100])[:240],
        }
        if excluded:
            result["branchExclusions"] = [normalize(caption[max(0, excluded.start() - 90) : excluded.end() + 30])[:150]]
        return result
    all_hk = re.search(r"全港(?:各)?分店|全線(?:活動)?分店|全线(?:活动)?分店|全線門市|全线门市|全港門市", caption)
    if all_hk:
        evidence = normalize(caption[max(0, all_hk.start() - 20) : all_hk.end() + 60])[:140]
        if "*" in evidence or "＊" in evidence:
            return {"branchScope": "unknown", "branchEvidenceText": evidence}
        return {"branchScope": "all-hk", "branchEvidenceText": evidence}
    return {"branchScope": "unknown", "branchEvidenceText": ""}


def fetch_public(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; sushiro-counter-instagram-detector/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=25) as response:
        return response.read().decode("utf-8", errors="strict")


def fetch_with_retries(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return fetch_public(url)
        except (HTTPError, OSError, UnicodeError, URLError) as exc:
            last_error = exc
            if attempt < FETCH_ATTEMPTS:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Could not fetch public Instagram page: {last_error}")


def parse_published_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        match = re.search(r"(?:created|建立於)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", value, re.IGNORECASE)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%B %d, %Y").date()
        except ValueError:
            return None


def parse_date_window(
    text: str,
    *,
    published_date: date | None = None,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    today = today or datetime.now(HONG_KONG_TZ).date()
    found: list[date] = []
    known_year = published_date.year if published_date else today.year

    date_matches = sorted(
        [
            (match.start(), "iso", match)
            for match in ISO_DATE_RE.finditer(text)
        ]
        + [
            (match.start(), "chinese", match)
            for match in CHINESE_DATE_RE.finditer(text)
        ],
        key=lambda entry: entry[0],
    )
    seen_month_days: dict[tuple[int, int], date] = {}
    last_parsed: date | None = None
    for _, kind, match in date_matches:
        if kind == "iso":
            year = int(match["year"])
        else:
            month = int(match["month"])
            day = int(match["day"])
            month_day = (month, day)
            if match["year"]:
                year = int(match["year"])
            elif month_day in seen_month_days:
                year = seen_month_days[month_day].year
            elif last_parsed and month_day < (last_parsed.month, last_parsed.day):
                year = last_parsed.year + 1
            else:
                year = last_parsed.year if last_parsed else known_year
        try:
            parsed = date(year, int(match["month"]), int(match["day"]))
        except ValueError:
            continue
        if parsed not in found:
            found.append(parsed)
        seen_month_days[(parsed.month, parsed.day)] = parsed
        last_parsed = parsed
        known_year = parsed.year

    unique_dates: list[date] = []
    for parsed in found:
        if parsed not in unique_dates:
            unique_dates.append(parsed)
    if not unique_dates:
        return None, None
    start = unique_dates[0]
    end = unique_dates[1] if len(unique_dates) > 1 else None
    return start.isoformat(), end.isoformat() if end else None


def candidate_item_id(post_url: str, name: str) -> str:
    digest = hashlib.sha1(f"{post_url}\n{name.casefold()}".encode("utf-8")).hexdigest()[:12]
    return f"instagram_{digest}"


def clean_item_name(value: str) -> str:
    value = normalize(value)
    value = re.sub(r"[\ufe0e\ufe0f\u200d]", "", value)
    value = re.sub(r"^[\s•·*#\-–—:：|,，。;；]+", "", value)
    if "：" in value:
        value = value.rsplit("：", 1)[-1]
    elif ":" in value:
        value = value.rsplit(":", 1)[-1]
    value = EMOJI_RE.sub("", value)
    return normalize(value).strip(" -–—,，。;；｜|→←↑↓！!？?·•")


def classify_item_type(name: str) -> str:
    if re.search(r"立牌|襟章|徽章|海報|海报|精品|卡套|收藏卡|插牌|商品", name):
        return "merchandise"
    if re.search(r"茶碗蒸|蒸蛋", name):
        return "dish"
    if re.search(r"飲品|饮品|飲料|饮料|果汁|茶飲|茶饮|茶水|茶$|啤酒|酒|梳打|汽水|可爾必思|咖啡|奶茶", name):
        return "drink"
    return "dish"


def classify_purchase_condition(name: str, caption: str) -> str:
    if classify_item_type(name) == "merchandise" and re.search(r"加購|加购", caption):
        return "conditional-add-on"
    return "standard"


def caption_item_name(caption: str, match: re.Match[str], previous_price_end: int = 0) -> str:
    prefix = caption[previous_price_end : match.start()]
    prefix = re.split(r"[\r\n。；;]|📅|⏳", prefix)[-1]
    prefix = re.split(r"售完即止|售罄|數量有限|数量有限", prefix)[-1]
    has_item_separator = "｜" in prefix or "|" in prefix
    # The full-width separator is part of Sushiro's item heading format.
    for separator in ("｜", "|"):
        if separator in prefix:
            prefix = prefix.rsplit(separator, 1)[-2]
            break
    emoji_matches = list(EMOJI_RE.finditer(prefix))
    if emoji_matches:
        prefix = prefix[emoji_matches[-1].end() :]
    elif not has_item_separator and re.search(r"[。！？!?；;]", prefix):
        return ""
    else:
        prefix = re.split(r"[🎬🔸⛩️✈️]", prefix)[-1]
    if re.search(r"[。！？!?；;]", prefix):
        return ""
    return clean_item_name(prefix)


def reliable_item_name(name: str) -> bool:
    if not name or len(name) > 56:
        return False
    if not re.search(r"[\u3400-\u9fffA-Za-z0-9]", name):
        return False
    if re.search(r"[。！？!?；;\n]", name):
        return False
    return True


def parse_sale_period(
    caption: str,
    marker: re.Match[str],
    *,
    published_date: date | None,
    today: date | None,
) -> tuple[str | None, str | None, bool, str]:
    next_marker = SALE_PERIOD_RE.search(caption, marker.end())
    section_end = next_marker.start() if next_marker else len(caption)
    section_text = caption[marker.end() : section_end]
    date_matches = sorted(
        [(match.start(), match.end(), match.group(0)) for match in ISO_DATE_RE.finditer(section_text)]
        + [(match.start(), match.end(), match.group(0)) for match in CHINESE_DATE_RE.finditer(section_text)],
        key=lambda entry: entry[0],
    )
    if not date_matches:
        local_text = section_text[: EMOJI_RE.search(section_text).start()] if EMOJI_RE.search(section_text) else section_text[:80]
        return None, None, bool(re.search(r"售完即止|售罄|數量有限|数量有限", local_text)), normalize(section_text)[:180]

    first = date_matches[0]
    selected = first[2]
    if len(date_matches) > 1:
        second = date_matches[1]
        between = section_text[first[1] : second[0]]
        if re.fullmatch(r"\s*(?:至|到|[-–—~～〜])\s*", between):
            selected += between + second[2]
            selected_end = second[1]
        else:
            selected_end = first[1]
    else:
        selected_end = first[1]
    start_date, end_date = parse_date_window(
        selected,
        published_date=published_date,
        today=today,
    )
    supply_tail = section_text[selected_end:]
    next_emoji = EMOJI_RE.search(supply_tail)
    if next_emoji:
        supply_tail = supply_tail[: next_emoji.start()]
    else:
        supply_tail = supply_tail[:80]
    evidence_text = normalize(f"{selected} {supply_tail}")[:180]
    return start_date, end_date, bool(re.search(r"售完即止|售罄|數量有限|数量有限", selected + supply_tail)), evidence_text


def associate_sale_periods(
    caption: str,
    price_matches: list[re.Match[str]],
    *,
    published_date: date | None,
    today: date | None,
) -> tuple[dict[int, dict[str, object]], set[int], list[str]]:
    associations: dict[int, dict[str, object]] = {}
    ambiguous_items: set[int] = set()
    reasons: list[str] = []
    period_markers = list(SALE_PERIOD_RE.finditer(caption))

    if not period_markers:
        caption_dates = list(ISO_DATE_RE.finditer(caption)) + list(CHINESE_DATE_RE.finditer(caption))
        if len(price_matches) == 1:
            date_tail = caption[price_matches[0].end() :]
            start_date, end_date = parse_date_window(
                date_tail,
                published_date=published_date,
                today=today,
            )
            if (start_date or end_date) and not PROMOTION_SECTION_RE.search(date_tail) and len(date_tail) <= 160:
                association: dict[str, object] = {
                    "startDate": start_date,
                    "endDate": end_date,
                    "dateConfidence": "explicit-section",
                    "dateEvidenceText": normalize(date_tail)[:180],
                }
                if re.search(r"售完即止|售罄|數量有限|数量有限", date_tail):
                    association["supplyCondition"] = "until-sold-out"
                    association["availabilityEvidenceText"] = normalize(date_tail)[:180]
                elif end_date:
                    association["availabilityEvidenceText"] = normalize(date_tail)[:180]
                associations[0] = association
        elif caption_dates:
            ambiguous_items.update(range(len(price_matches)))
            reasons.append("Dates appeared in a multi-item caption without item or promotion section markers; item dates were left unset for review.")
        return associations, ambiguous_items, reasons

    for marker in period_markers:
        previous_index = next(
            (index for index in range(len(price_matches) - 1, -1, -1) if price_matches[index].end() <= marker.start()),
            None,
        )
        next_index = next(
            (index for index, price_match in enumerate(price_matches) if price_match.start() >= marker.end()),
            None,
        )
        previous_text = caption[price_matches[previous_index].end() : marker.start()] if previous_index is not None else ""
        previous_has_section_boundary = bool(PROMOTION_SECTION_RE.search(previous_text))
        previous_is_item_context = bool(normalize(previous_text)) and not previous_has_section_boundary

        start_date, end_date, until_sold_out, period_evidence = parse_sale_period(
            caption,
            marker,
            published_date=published_date,
            today=today,
        )
        if not start_date and not end_date:
            reasons.append("A sale-period marker had no reliably parseable date; nearby items remain undated for review.")
            if previous_index is not None:
                ambiguous_items.add(previous_index)
            if next_index is not None:
                ambiguous_items.add(next_index)
            continue

        if previous_index is not None and previous_is_item_context:
            target_indices = [previous_index]
        elif next_index is not None:
            next_marker = SALE_PERIOD_RE.search(caption, marker.end())
            next_marker_start = next_marker.start() if next_marker else len(caption)
            boundary = PROMOTION_SECTION_RE.search(caption, marker.end(), next_marker_start)
            forward_end = min(next_marker_start, boundary.start() if boundary else next_marker_start)
            target_indices = [
                index for index in range(next_index, len(price_matches))
                if price_matches[index].start() < forward_end
            ]
        elif previous_index is not None:
            ambiguous_items.add(previous_index)
            reasons.append("A sale-period marker could not be confidently assigned to an item; the nearby item remains undated for review.")
            continue
        else:
            reasons.append("A sale-period marker could not be associated with a priced item; no item date was inferred.")
            continue

        if not target_indices:
            reasons.append("A sale-period marker could not be associated with a priced item; no item date was inferred.")
            continue
        for index in target_indices:
            period = associations.setdefault(index, {})
            existing_window = (period.get("startDate"), period.get("endDate"))
            incoming_window = (start_date, end_date)
            if period.get("dateConfidence") == "ambiguous":
                continue
            if period.get("dateConfidence") == "explicit-section" and existing_window != incoming_window:
                period.clear()
                period["dateConfidence"] = "ambiguous"
                ambiguous_items.add(index)
                reasons.append("Conflicting sale periods matched one item; its dates were left unset for review.")
                continue
            if start_date:
                period["startDate"] = start_date
            if end_date:
                period["endDate"] = end_date
            if until_sold_out:
                period["supplyCondition"] = "until-sold-out"
                period["availabilityEvidenceText"] = period_evidence
            elif end_date:
                period["availabilityEvidenceText"] = period_evidence
            period["dateEvidenceText"] = period_evidence
            period["dateConfidence"] = "explicit-section"

    return associations, ambiguous_items, reasons


def is_purchase_threshold_name(name: str) -> bool:
    return bool(re.search(r"消費滿|消费满|即可加購|即可加购|最多可加購|最多可加购", name))


def parse_caption_items(
    caption: str,
    post_url: str,
    *,
    published_date: date | None = None,
    today: date | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    items: list[dict[str, object]] = []
    reasons: list[str] = []
    price_matches = list(CAPTION_PRICE_RE.finditer(caption))
    sale_periods, ambiguous_dates, date_reasons = associate_sale_periods(
        caption,
        price_matches,
        published_date=published_date,
        today=today,
    )
    reasons.extend(date_reasons)
    for index, match in enumerate(price_matches):
        previous_price_end = price_matches[index - 1].end() if index else 0
        name = caption_item_name(caption, match, previous_price_end)
        if not reliable_item_name(name):
            reasons.append("A caption price was found without a reliable item name.")
            continue
        if is_purchase_threshold_name(name):
            reasons.append("A caption price was associated with a spend threshold or add-on quantity; it was not treated as a menu item.")
            continue
        item = {
            "id": candidate_item_id(post_url, name),
            "name": name,
            "itemType": classify_item_type(name),
            "foodCategory": "drink" if classify_item_type(name) == "drink" else "food",
            "purchaseCondition": classify_purchase_condition(name, caption),
            "dateReviewRequired": True,
            "priceEvidenceSource": "caption",
            "priceEvidenceQuote": match.group(0),
            "priceEvidenceText": normalize(caption[max(0, match.start() - len(name) - 12) : match.end()])[:180],
            **caption_branch_evidence(caption),
        }
        if index in sale_periods:
            item.update(sale_periods[index])
        elif index in ambiguous_dates:
            item["dateConfidence"] = "ambiguous"
            item["dateReviewReason"] = "A nearby sale period could not be confidently associated with this item."
        else:
            item["dateConfidence"] = "not-stated"
            item["dateReviewReason"] = "No item-specific sale period was reliably associated with this item."
        raw_price = float(match.group("amount"))
        marker = match.group("marker")
        if marker.upper().replace(" ", "") in {"HK$", "HKD"}:
            item["price"] = int(raw_price) if raw_price.is_integer() else raw_price
            item["currency"] = "HKD"
            item["priceSource"] = "explicit-caption"
            item["priceConfidence"] = "explicit-hkd"
        else:
            item["priceText"] = match.group(0)
            item["priceSource"] = "ambiguous-caption"
            item["priceConfidence"] = "ambiguous-currency"
            reasons.append("A price was present without an explicit HKD marker; its numeric value was not imported.")
        if not any(existing["name"] == name for existing in items):
            items.append(item)

    if AMBIGUOUS_PRICE_RE.search(caption) and not CAPTION_PRICE_RE.search(caption):
        reasons.append("A price was present without an explicit HKD marker; its numeric value was not imported.")
    if not items:
        reasons.append("No item with an explicit HK$ or HKD price was found in the public caption.")
    return items, reasons


def normalize_manual_item(item: object, post_url: str) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(item, dict):
        return None, "A manually supplied item was not an object."
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "A manually supplied item has no name."
    normalized_name = normalize(name)
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        item_id = candidate_item_id(post_url, normalized_name)
    result: dict[str, object] = {
        "id": item_id,
        "name": normalized_name,
        "itemType": item.get("itemType") if item.get("itemType") in ITEM_TYPES else classify_item_type(normalized_name),
        "purchaseCondition": item.get("purchaseCondition")
        if item.get("purchaseCondition") in PURCHASE_CONDITIONS
        else "unknown",
        "currency": "HKD",
        "priceSource": "manual-transcription",
        "priceConfidence": "manual-transcription",
        "dateConfidence": "manual-transcription" if item.get("startDate") or item.get("endDate") else "not-stated",
        "dateReviewRequired": True,
    }
    for field in ("startDate", "endDate"):
        value = item.get(field)
        if value is None:
            continue
        try:
            result[field] = date.fromisoformat(str(value)).isoformat()
        except ValueError:
            return None, f"A manually supplied item has an invalid {field}."
    supply_condition = item.get("supplyCondition")
    if isinstance(supply_condition, str) and supply_condition in {"until-sold-out", "unknown"}:
        result["supplyCondition"] = supply_condition
    price = item.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or price < 0:
        result.pop("currency", None)
        result["priceSource"] = "manual-missing-price"
        result["priceConfidence"] = "missing"
        return result, "A manually supplied item has no valid price; manual review is required."
    result["price"] = int(price) if isinstance(price, float) and price.is_integer() else price
    return result, None


def make_candidate(
    post_url: str,
    metadata: dict[str, str] | None = None,
    image_urls: list[str] | None = None,
    manual_post: dict[str, object] | None = None,
    *,
    today: date | None = None,
    extraction_method: str | None = None,
) -> dict[str, object]:
    metadata = metadata or {}
    manual_post = manual_post or {}
    today = today or datetime.now(HONG_KONG_TZ).date()
    caption = manual_post.get("caption") or metadata.get("og:description") or ""
    caption = normalize(str(caption))
    published_value = manual_post.get("publishedAt") or metadata.get("article:published_time") or metadata.get("og:description")
    published_date = parse_published_date(published_value)

    combined_image_urls = list(image_urls or [])
    manual_image_urls = manual_post.get("imageUrls")
    if isinstance(manual_image_urls, list):
        combined_image_urls.extend(str(url) for url in manual_image_urls if isinstance(url, str) and url.strip())
    for image_key in ("og:image", "og:image:url"):
        image_url = metadata.get(image_key)
        if image_url:
            combined_image_urls.append(image_url)

    review_reasons = [
        "Instagram-derived menu changes require human review before publishing.",
        "Only public caption text and exposed image URLs are extracted; text inside carousel images is not OCRed.",
    ]
    items: list[dict[str, object]] = []
    if isinstance(manual_post.get("items"), list):
        for raw_item in manual_post["items"]:
            normalized_item, reason = normalize_manual_item(raw_item, post_url)
            if normalized_item is not None:
                items.append(normalized_item)
            if reason:
                review_reasons.append(reason)
    else:
        items, caption_reasons = parse_caption_items(
            caption,
            post_url,
            published_date=published_date,
            today=today,
        )
        review_reasons.extend(caption_reasons)

    deduped_items: list[dict[str, object]] = []
    item_names: set[str] = set()
    for item in items:
        key = str(item["name"]).casefold()
        if key not in item_names:
            item_names.add(key)
            item.setdefault("itemType", classify_item_type(str(item["name"])))
            if item.get("purchaseCondition") not in PURCHASE_CONDITIONS or item.get("purchaseCondition") == "unknown":
                item["purchaseCondition"] = classify_purchase_condition(str(item["name"]), caption)
            deduped_items.append(item)
    items = deduped_items

    item_windows = [
        (item.get("startDate"), item.get("endDate"))
        for item in items
        if item.get("startDate") or item.get("endDate")
    ]
    unique_item_windows = set(item_windows)
    manual_start = str(manual_post.get("startDate") or "") or None
    manual_end = str(manual_post.get("endDate") or "") or None
    if manual_start or manual_end:
        start_date, end_date = manual_start, manual_end
        date_scope = "global"
    elif item_windows and len(item_windows) == len(items) and len(unique_item_windows) == 1:
        start_date, end_date = next(iter(unique_item_windows))
        date_scope = "global"
    elif item_windows:
        start_date, end_date = None, None
        date_scope = "item-specific"
        review_reasons.append("Multiple or partial item-specific promotion windows were kept on items; no global date window was inferred.")
    else:
        start_date, end_date = None, None
        date_scope = "none"
        if items:
            review_reasons.append("No item-specific sale dates were confidently available; verify dates manually.")

    status = "found" if items and all("price" in item for item in items) else "needs_review"
    if date_scope == "item-specific":
        status = "needs_review"
    elif end_date and end_date < today.isoformat():
        status = "expired"
    elif start_date and start_date > today.isoformat():
        status = "future"

    extraction_method = extraction_method or str(
        manual_post.get("extractionMethod")
        or ("manual-source" if manual_post else "public-html-meta-caption")
    )
    if combined_image_urls and extraction_method == "public-html-meta-caption":
        extraction_method = "public-html-meta-caption-and-exposed-media"
    supply_condition = "until-sold-out" if re.search(r"售完即止|售罄|數量有限|数量有限", caption) else None
    result: dict[str, object] = {
        "url": post_url,
        "postId": canonical_post_id(post_url),
        "contentFingerprint": content_fingerprint(caption, combined_image_urls),
        "extractionMethod": extraction_method,
        "extractionVersion": EXTRACTION_VERSION,
        "authorEvidence": extract_author_evidence(metadata),
        "status": status,
        "verificationStatus": "unverified",
        "reviewStatus": "review-only",
        "requiresReview": True,
        "publishedAt": published_date.isoformat() if published_date else None,
        "startDate": start_date,
        "endDate": end_date,
        "dateScope": date_scope,
        "supplyCondition": supply_condition,
        "caption": caption,
        "imageUrls": sorted(set(combined_image_urls)),
        "items": items,
        "reviewReasons": sorted(set(review_reasons)),
    }
    return result


def unavailable_candidate(post_url: str, reason: str | None = None) -> dict[str, object]:
    return {
        "url": post_url,
        "postId": canonical_post_id(post_url),
        "contentFingerprint": None,
        "extractionMethod": "unavailable",
        "status": "unavailable",
        "verificationStatus": "unverified",
        "reviewStatus": "review-only",
        "requiresReview": True,
        "publishedAt": None,
        "startDate": None,
        "endDate": None,
        "dateScope": "none",
        "supplyCondition": None,
        "caption": "",
        "imageUrls": [],
        "items": [],
        "reviewReasons": [reason or "The public post could not be fetched; no menu data was inferred."],
    }


def load_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_source_data(path: Path) -> tuple[dict[str, object], list[str]]:
    data = load_json(path)
    if data is None:
        return {"schemaVersion": 1, "account": "sushiro.hk", "posts": []}, [
            "The optional Instagram source file was missing or invalid; automatic discovery continued."
        ]
    if data.get("schemaVersion") != 1 or not isinstance(data.get("posts"), list):
        return {"schemaVersion": 1, "account": "sushiro.hk", "posts": []}, [
            "The optional Instagram source file has an invalid schema; automatic discovery continued."
        ]
    return data, []


def load_existing_candidates(path: Path) -> dict[str, object]:
    data = load_json(path)
    if not data or data.get("schemaVersion") != 1 or not isinstance(data.get("posts"), list):
        return {"schemaVersion": 1, "account": "sushiro.hk", "posts": []}
    return data


def empty_scan_state() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "account": "sushiro.hk",
        "overlapDays": OVERLAP_DAYS,
        "lastAttemptAt": None,
        "lastSuccessfulScanAt": None,
        "discoveredPosts": {},
    }


def load_scan_state(path: Path) -> tuple[dict[str, object], list[str]]:
    if not path.exists():
        return empty_scan_state(), []
    data = load_json(path)
    if data is None:
        return empty_scan_state(), ["The Instagram scan state file was missing or invalid; the next scan starts conservatively."]
    try:
        validate_scan_state(data)
    except ValueError:
        return empty_scan_state(), ["The Instagram scan state file had an invalid schema; the next scan starts conservatively."]
    return data, []


def validate_scan_state(data: dict[str, object]) -> None:
    if data.get("schemaVersion") != 1 or not isinstance(data.get("discoveredPosts"), dict):
        raise ValueError("Invalid Instagram scan state schema")
    for field in ("lastAttemptAt", "lastSuccessfulScanAt"):
        value = data.get(field)
        if value is not None:
            try:
                datetime.fromisoformat(str(value))
            except ValueError as exc:
                raise ValueError(f"Invalid Instagram scan state timestamp: {field}") from exc
    for post_id, entry in data["discoveredPosts"].items():
        if not isinstance(post_id, str) or not isinstance(entry, dict):
            raise ValueError("Invalid Instagram discovered post entry")
        url = entry.get("url")
        if not isinstance(url, str) or canonical_post_url(url) != url or canonical_post_id(url) != post_id:
            raise ValueError(f"Invalid Instagram discovered post URL: {post_id}")
        fingerprint = entry.get("contentFingerprint")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError(f"Invalid Instagram content fingerprint: {post_id}")


def write_scan_state(path: Path, state: dict[str, object]) -> None:
    validate_scan_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def timestamp_for(value: datetime | None = None) -> str:
    value = value or datetime.now(HONG_KONG_TZ)
    if value.tzinfo is None:
        value = value.replace(tzinfo=HONG_KONG_TZ)
    return value.astimezone(HONG_KONG_TZ).replace(microsecond=0).isoformat()


def should_process_post(
    published_date: date | None,
    previous_successful_scan: str | None,
    previous_fingerprint: str | None,
    current_fingerprint: str,
    *,
    today: date,
) -> bool:
    if not previous_fingerprint or previous_fingerprint != current_fingerprint:
        return True
    if not previous_successful_scan:
        return True
    if not published_date:
        # Unknown publication dates cannot be overlap-filtered. A stable
        # fingerprint still proves that this already-seen page is unchanged.
        return False
    try:
        previous_date = datetime.fromisoformat(previous_successful_scan).date()
    except ValueError:
        return True
    return published_date >= previous_date - timedelta(days=OVERLAP_DAYS)


def merge_items(old_items: object, new_items: object) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for raw_item in [*(old_items if isinstance(old_items, list) else []), *(new_items if isinstance(new_items, list) else [])]:
        if not isinstance(raw_item, dict) or not raw_item.get("name"):
            continue
        key = str(raw_item.get("id") or raw_item["name"]).casefold()
        if key not in merged:
            order.append(key)
            merged[key] = {}
        merged[key].update(raw_item)
    return [merged[key] for key in order]


def merge_posts(previous: object, current: object) -> list[dict[str, object]]:
    previous_posts = previous if isinstance(previous, list) else []
    current_posts = current if isinstance(current, list) else []
    by_url: dict[str, dict[str, object]] = {}
    for raw_post in previous_posts:
        if isinstance(raw_post, dict) and isinstance(raw_post.get("url"), str):
            by_url[raw_post["url"]] = dict(raw_post)
    for raw_post in current_posts:
        if not isinstance(raw_post, dict) or not isinstance(raw_post.get("url"), str):
            continue
        url = raw_post["url"]
        if url not in by_url:
            by_url[url] = dict(raw_post)
            continue
        old_post = by_url[url]
        if raw_post.get("status") == "unavailable":
            continue
        merged = dict(old_post)
        merged.update(raw_post)
        if (
            raw_post.get("contentFingerprint")
            and raw_post.get("contentFingerprint") != old_post.get("contentFingerprint")
        ):
            merged["items"] = raw_post.get("items", [])
        else:
            merged["items"] = merge_items(old_post.get("items"), raw_post.get("items"))
        if not merged.get("caption") and old_post.get("caption"):
            merged["caption"] = old_post["caption"]
        if not merged.get("imageUrls") and old_post.get("imageUrls"):
            merged["imageUrls"] = old_post["imageUrls"]
        by_url[url] = merged
    return [by_url[url] for url in sorted(by_url)]


def validate_candidates(data: dict[str, object]) -> None:
    if data.get("schemaVersion") != 1 or not isinstance(data.get("posts"), list):
        raise ValueError("Invalid Instagram candidate schema")
    if data.get("sourceStatus") not in SOURCE_STATUSES:
        raise ValueError("Invalid Instagram candidate source status")
    if data.get("profileFetchStatus") not in PROFILE_FETCH_STATUSES:
        raise ValueError("Invalid Instagram profile fetch status")
    if data.get("postDiscoveryStatus") not in POST_DISCOVERY_STATUSES:
        raise ValueError("Invalid Instagram post discovery status")
    seen_urls: set[str] = set()
    seen_item_ids: set[str] = set()
    for post in data["posts"]:
        if not isinstance(post, dict):
            raise ValueError("Instagram candidate post is not an object")
        url = post.get("url")
        status = post.get("status")
        if not isinstance(url, str) or canonical_post_url(url) != url:
            raise ValueError(f"Invalid Instagram post URL: {url}")
        if post.get("postId") is not None and post.get("postId") != canonical_post_id(url):
            raise ValueError(f"Instagram candidate post ID does not match URL: {url}")
        fingerprint = post.get("contentFingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        ):
            raise ValueError(f"Invalid Instagram candidate fingerprint: {url}")
        if url in seen_urls:
            raise ValueError(f"Duplicate Instagram post URL: {url}")
        if status not in POST_STATUSES:
            raise ValueError(f"Invalid Instagram candidate status: {url}")
        if post.get("reviewStatus") is not None and post.get("reviewStatus") != "review-only":
            raise ValueError(f"Instagram candidate is not review-only: {url}")
        if post.get("dateScope") is not None and post.get("dateScope") not in {"none", "global", "item-specific"}:
            raise ValueError(f"Invalid Instagram candidate date scope: {url}")
        if post.get("requiresReview") is not True:
            raise ValueError(f"Instagram candidate is not review-only: {url}")
        dates: dict[str, date] = {}
        for field in ("publishedAt", "startDate", "endDate"):
            value = post.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"Invalid Instagram candidate date: {url}")
            try:
                dates[field] = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"Invalid Instagram candidate date: {url}") from exc
        if "startDate" in dates and "endDate" in dates and dates["endDate"] < dates["startDate"]:
            raise ValueError(f"Instagram candidate ends before it starts: {url}")
        items = post.get("items")
        if not isinstance(items, list):
            raise ValueError(f"Instagram candidate items are invalid: {url}")
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item.get("id")
                or not isinstance(item.get("name"), str)
                or not item.get("name")
            ):
                raise ValueError(f"Instagram candidate item has no id: {url}")
            if item.get("itemType") is not None and item.get("itemType") not in ITEM_TYPES:
                raise ValueError(f"Invalid Instagram candidate item type: {item['id']}")
            if item.get("purchaseCondition") is not None and item.get("purchaseCondition") not in PURCHASE_CONDITIONS:
                raise ValueError(f"Invalid Instagram purchase condition: {item['id']}")
            if item.get("existingMenuId") is not None and (
                not isinstance(item.get("existingMenuId"), str) or not item.get("existingMenuId")
            ):
                raise ValueError(f"Invalid existing menu match: {item['id']}")
            if item.get("priceConfidence") is not None and (
                not isinstance(item.get("priceConfidence"), str) or item.get("priceConfidence") not in PRICE_CONFIDENCE
            ):
                raise ValueError(f"Invalid Instagram price confidence: {item['id']}")
            if item.get("dateConfidence") is not None and (
                not isinstance(item.get("dateConfidence"), str) or item.get("dateConfidence") not in DATE_CONFIDENCE
            ):
                raise ValueError(f"Invalid Instagram date confidence: {item['id']}")
            if item.get("dateReviewRequired") is not None and not isinstance(item.get("dateReviewRequired"), bool):
                raise ValueError(f"Invalid Instagram date review status: {item['id']}")
            if item.get("dateConfidence") in {"not-stated", "ambiguous"} and (
                "startDate" in item or "endDate" in item
            ):
                raise ValueError(f"Uncertain Instagram item dates were imported: {item['id']}")
            if item.get("priceSource") == "ambiguous-caption" and "price" in item:
                raise ValueError(f"Ambiguous Instagram price was imported: {item['id']}")
            if item["id"] in seen_item_ids:
                raise ValueError(f"Duplicate Instagram candidate item id: {item['id']}")
            if "price" in item:
                price = item["price"]
                if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                    raise ValueError(f"Invalid Instagram candidate price: {item['id']}")
                if isinstance(price, float) and not math.isfinite(price):
                    raise ValueError(f"Invalid Instagram candidate price: {item['id']}")
            item_dates: dict[str, date] = {}
            for field in ("startDate", "endDate"):
                value = item.get(field)
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValueError(f"Invalid Instagram item date: {item['id']}")
                try:
                    item_dates[field] = date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"Invalid Instagram item date: {item['id']}") from exc
            if "startDate" in item_dates and "endDate" in item_dates and item_dates["endDate"] < item_dates["startDate"]:
                raise ValueError(f"Instagram item ends before it starts: {item['id']}")
            seen_item_ids.add(item["id"])
        seen_urls.add(url)


def machine_readable_report(data: dict[str, object], generated_at: str) -> dict[str, object]:
    posts: list[dict[str, object]] = []
    raw_posts = data.get("posts") if isinstance(data.get("posts"), list) else []
    for post in sorted(
        (post for post in raw_posts if isinstance(post, dict)),
        key=lambda post: (str(post.get("postId") or ""), str(post.get("url") or "")),
    ):
        report_items: list[dict[str, object]] = []
        raw_items = post.get("items") if isinstance(post.get("items"), list) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            compact_item: dict[str, object] = {
                "id": item.get("id"),
                "name": item.get("name"),
                "itemType": item.get("itemType", "dish"),
                "purchaseCondition": item.get("purchaseCondition", "unknown"),
                "priceConfidence": item.get("priceConfidence", "missing"),
            }
            for field in (
                "price", "priceText", "startDate", "endDate", "supplyCondition", "existingMenuId",
                "foodCategory", "dateConfidence", "dateEvidenceText", "availabilityEvidenceText",
                "dateReviewRequired", "dateReviewReason", "priceEvidenceSource", "priceEvidenceQuote",
                "priceEvidenceText", "branchScope", "branchEvidenceText", "branchExclusions",
            ):
                if field in item:
                    compact_item[field] = item[field]
            report_items.append(compact_item)
        posts.append(
            {
                "postId": post.get("postId") or canonical_post_id(str(post.get("url") or "")),
                "url": post.get("url"),
                "contentFingerprint": post.get("contentFingerprint"),
                "extractionMethod": post.get("extractionMethod"),
                "extractionVersion": post.get("extractionVersion"),
                "authorEvidence": post.get("authorEvidence"),
                "observedAt": post.get("observedAt"),
                "discoverySources": post.get("discoverySources", []),
                "publishedAt": post.get("publishedAt"),
                "status": post.get("status"),
                "reviewStatus": post.get("reviewStatus", "review-only"),
                "dateScope": post.get("dateScope", "none"),
                "startDate": post.get("startDate"),
                "endDate": post.get("endDate"),
                "supplyCondition": post.get("supplyCondition"),
                "items": report_items,
                "reviewReasons": post.get("reviewReasons", []),
            }
        )
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "account": data.get("account"),
        "profileUrl": data.get("profileUrl"),
        "sourceMethod": data.get("sourceMethod"),
        "profileFetchStatus": data.get("profileFetchStatus"),
        "postDiscoveryStatus": data.get("postDiscoveryStatus"),
        "sourceStatus": data.get("sourceStatus"),
        "discoveryComplete": data.get("discoveryComplete", False),
        "discoveryCoverage": data.get("discoveryCoverage", "public-html-best-effort-not-exhaustive"),
        "sourceFailures": data.get("sourceFailures", []),
        "discoverySourceCounts": data.get("discoverySourceCounts", {}),
        "postCount": len(posts),
        "reviewOnly": True,
        "limitations": data.get("limitations", []),
        "posts": posts,
    }


def write_machine_report(path: Path, data: dict[str, object], generated_at: str) -> None:
    report = machine_readable_report(data, generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def detect(
    *,
    profile_url: str = PROFILE_URL,
    sources_path: Path = SOURCES_PATH,
    output_path: Path = OUTPUT_PATH,
    state_path: Path | None = None,
    report_path: Path | None = None,
    fetcher=fetch_with_retries,
    today: date | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    today = today or datetime.now(HONG_KONG_TZ).date()
    if state_path is None:
        state_path = STATE_PATH if output_path == OUTPUT_PATH else output_path.with_name("instagram_scan_state.json")
    source_data, limitations = load_source_data(sources_path)
    existing_menu_ids = load_existing_menu_ids()
    scan_state, state_limitations = load_scan_state(state_path)
    limitations.extend(state_limitations)
    attempt_at = timestamp_for(now)
    previous_successful_scan = scan_state.get("lastSuccessfulScanAt")
    discovered_posts = dict(scan_state.get("discoveredPosts") or {})
    scan_state["lastAttemptAt"] = attempt_at
    existing = load_existing_candidates(output_path)
    existing_urls = {
        post.get("url") for post in existing.get("posts", []) if isinstance(post, dict) and isinstance(post.get("url"), str)
    }
    current_posts: dict[str, dict[str, object]] = {}
    profile_fetch_status = "success"
    automatic_post_urls: list[str] = []
    fallback_post_sources: dict[str, set[str]] = {}
    fallback_source_failures: list[str] = []
    post_fetch_failed = False
    manual_fetch_failed = False
    discovery_incomplete = False

    try:
        profile_page = fetcher(profile_url)
    except Exception as exc:
        profile_fetch_status = "unavailable"
        limitations.append("Instagram public profile was not accessible; existing candidates were preserved.")
        print(f"Instagram profile unavailable: {exc}", file=sys.stderr)
        profile_data = {"metadata": {}, "postUrls": [], "imageUrls": []}
    else:
        profile_data = parse_public_page(profile_page, profile_url)
        all_automatic_post_urls = profile_data["postUrls"] if isinstance(profile_data["postUrls"], list) else []
        automatic_post_urls = all_automatic_post_urls[:MAX_DISCOVERED_POSTS]
        if len(all_automatic_post_urls) > MAX_DISCOVERED_POSTS:
            discovery_incomplete = True
            limitations.append(
                f"The public profile exposed {len(all_automatic_post_urls)} post links; processed the first {MAX_DISCOVERED_POSTS} in stable canonical URL order, so the scan was incomplete."
            )
        if not automatic_post_urls:
            discovery_incomplete = True
            limitations.append(
                "Profile HTTP access succeeded, but the returned HTML exposed no public post anchors; an empty result is not treated as a successful scan."
            )
    # These sources are only supplementary, public, and best-effort. A search
    # result is never trusted to establish account ownership by itself.
    for source_name, source_url in (
        ("official-website-link", OFFICIAL_DISCOVERY_URL),
        ("public-search-rss", PUBLIC_INDEX_FEED_URL),
    ):
        try:
            source_content = fetcher(source_url)
            if source_name == "official-website-link":
                official_parser = PublicPageParser()
                official_parser.feed(source_content)
                source_posts = sorted({
                    post_url for link in official_parser.links
                    if (post_url := canonical_post_url(urljoin(source_url, link), PROFILE_URL))
                })
            else:
                source_posts = discover_public_index_posts(source_content)
            for discovered_url in source_posts:
                fallback_post_sources.setdefault(discovered_url, set()).add(source_name)
        except Exception as exc:
            fallback_source_failures.append(source_name)
            discovery_incomplete = True
            limitations.append(f"{source_name} was unavailable; fallback coverage is incomplete.")
            print(f"Instagram fallback unavailable ({source_name}): {exc}", file=sys.stderr)

    all_discovered_post_urls = sorted(set(automatic_post_urls) | set(fallback_post_sources))
    if len(all_discovered_post_urls) > MAX_DISCOVERED_POSTS:
        discovery_incomplete = True
        limitations.append(
            f"Combined public sources exposed {len(all_discovered_post_urls)} post URLs; "
            f"only the first {MAX_DISCOVERED_POSTS} canonical URLs were processed."
        )
    for post_url in all_discovered_post_urls[:MAX_DISCOVERED_POSTS]:
        try:
            post_page = fetcher(post_url)
        except Exception as exc:
            post_fetch_failed = True
            print(f"Instagram post unavailable ({post_url}): {exc}", file=sys.stderr)
            current_posts[post_url] = unavailable_candidate(post_url)
            continue
        post_data = parse_public_page(post_page, post_url)
        if not has_usable_post_metadata(post_url, post_data):
            post_fetch_failed = True
            limitations.append(
                f"Public post {canonical_post_id(post_url)} returned HTTP content without usable public metadata; the checkpoint was not advanced."
            )
            current_posts[post_url] = unavailable_candidate(
                post_url,
                "The public post returned HTTP content but no usable public caption metadata; no menu data was inferred.",
            )
            continue
        metadata = post_data["metadata"]
        sources = fallback_post_sources.get(post_url, set())
        # Official website links come from a first-party page. Search-index
        # URLs must independently prove account ownership on the post page.
        if (
            post_url not in automatic_post_urls
            and "official-website-link" not in sources
            and not indexed_post_owner_confirmed(metadata)
        ):
            limitations.append(
                f"Search-index post {canonical_post_id(post_url)} had no verified @sushiro.hk "
                "owner evidence; ignored."
            )
            continue
        candidate = make_candidate(
            post_url,
            metadata if isinstance(metadata, dict) else {},
            post_data["imageUrls"] if isinstance(post_data["imageUrls"], list) else [],
            today=today,
        )
        candidate["observedAt"] = attempt_at
        candidate["discoverySources"] = sorted(
            (["instagram-profile"] if post_url in automatic_post_urls else [])
            + list(fallback_post_sources.get(post_url, set()))
        )
        post_id = canonical_post_id(post_url)
        fingerprint = candidate.get("contentFingerprint")
        previous_entry = discovered_posts.get(post_id) if isinstance(discovered_posts.get(post_id), dict) else None
        should_process = should_process_post(
            parse_published_date(candidate.get("publishedAt")),
            previous_successful_scan if isinstance(previous_successful_scan, str) else None,
            previous_entry.get("contentFingerprint") if previous_entry else None,
            str(fingerprint),
            today=today,
        )
        discovered_posts[post_id] = {
            "url": post_url,
            "publishedAt": candidate.get("publishedAt"),
            "contentFingerprint": fingerprint,
            "lastSeenAt": attempt_at,
        }
        if should_process or post_url not in existing_urls:
            current_posts[post_url] = candidate

    for raw_manual_post in source_data.get("posts", []):
        if not isinstance(raw_manual_post, dict):
            limitations.append("An invalid manual Instagram post entry was skipped.")
            continue
        post_url = canonical_post_url(str(raw_manual_post.get("url", "")), profile_url)
        if not post_url:
            limitations.append("A manual Instagram post entry had an invalid public post URL.")
            continue
        has_manual_content = bool(raw_manual_post.get("caption")) or isinstance(raw_manual_post.get("items"), list)
        if not has_manual_content:
            try:
                manual_page = fetcher(post_url)
                manual_data = parse_public_page(manual_page, post_url)
                if not has_usable_post_metadata(post_url, manual_data):
                    raise RuntimeError("public post returned no usable metadata")
                metadata = manual_data["metadata"]
                manual_candidate = make_candidate(
                    post_url,
                    metadata if isinstance(metadata, dict) else {},
                    manual_data["imageUrls"] if isinstance(manual_data["imageUrls"], list) else [],
                    manual_post=raw_manual_post,
                    today=today,
                )
            except Exception as exc:
                manual_fetch_failed = True
                print(f"Manual Instagram post unavailable ({post_url}): {exc}", file=sys.stderr)
                manual_candidate = unavailable_candidate(post_url)
        else:
            manual_candidate = make_candidate(post_url, manual_post=raw_manual_post, today=today)
        if post_url not in current_posts:
            current_posts[post_url] = manual_candidate
        else:
            current_posts[post_url] = {
                **current_posts[post_url],
                **manual_candidate,
                "items": merge_items(current_posts[post_url].get("items"), manual_candidate.get("items")),
            }

    for candidate in current_posts.values():
        candidate.setdefault("observedAt", attempt_at)
        candidate.setdefault("extractionVersion", EXTRACTION_VERSION)
        candidate.setdefault("discoverySources", ["manual-source"])
        annotate_existing_menu_matches(candidate, existing_menu_ids)
    posts = merge_posts(existing.get("posts"), list(current_posts.values()))
    if profile_fetch_status == "unavailable" and all_discovered_post_urls:
        source_status = "partial"
        post_discovery_status = "partial"
    elif profile_fetch_status == "unavailable":
        source_status = "unavailable"
        post_discovery_status = "unavailable"
    elif post_fetch_failed or manual_fetch_failed:
        source_status = "partial"
        post_discovery_status = "partial"
        limitations.append("One or more discovered public post pages could not be fetched or had unusable public metadata; those candidates remain review-only.")
    elif fallback_source_failures:
        source_status = "partial" if all_discovered_post_urls else "unavailable"
        post_discovery_status = "partial" if all_discovered_post_urls else "unavailable"
    elif discovery_incomplete and all_discovered_post_urls:
        source_status = "partial"
        post_discovery_status = "partial"
    elif all_discovered_post_urls and not automatic_post_urls:
        source_status = "partial"
        post_discovery_status = "partial"
        limitations.append("Profile discovery was empty; indexed or official-site links are only partial coverage.")
    elif automatic_post_urls:
        source_status = "posts_found"
        post_discovery_status = "found"
    elif current_posts:
        source_status = "manual_only"
        post_discovery_status = "manual_only"
    else:
        source_status = "profile_only"
        post_discovery_status = "none"
    if not all_discovered_post_urls and not current_posts:
        limitations.append("No public post candidate was available in this run.")
    automatic_scan_successful = (
        profile_fetch_status == "success"
        and bool(automatic_post_urls)
        and not discovery_incomplete
        and not post_fetch_failed
        and not fallback_source_failures
    )
    if automatic_scan_successful:
        scan_state["lastSuccessfulScanAt"] = attempt_at
    else:
        limitations.append("The automatic Instagram scan checkpoint was not advanced because discovery was empty or incomplete.")
    scan_state["discoveredPosts"] = discovered_posts
    report = {
        "schemaVersion": 1,
        "account": str(source_data.get("account") or "sushiro.hk"),
        "profileUrl": profile_url,
        "sourceMethod": "public-html-best-effort",
        "profileFetchStatus": profile_fetch_status,
        "postDiscoveryStatus": post_discovery_status,
        "sourceStatus": source_status,
        "discoveryComplete": automatic_scan_successful,
        "discoveryCoverage": "public-html-best-effort-not-exhaustive",
        "sourceFailures": sorted(set(fallback_source_failures)),
        "discoverySourceCounts": {
            "instagramProfileAnchors": len(automatic_post_urls),
            "officialWebsiteLinks": sum("official-website-link" in sources for sources in fallback_post_sources.values()),
            "publicSearchRssLinks": sum("public-search-rss" in sources for sources in fallback_post_sources.values()),
        },
        "requiresReview": True,
        "limitations": sorted(set(limitations + [
            "Public HTML discovery covers only the links exposed by the accessible pages and does not prove account-wide post completeness."
        ])),
        "posts": posts,
    }
    validate_candidates(report)
    if report_path is not None:
        write_machine_report(report_path, report, attempt_at)
    write_scan_state(state_path, scan_state)
    # Operational diagnostics belong in the Actions artifact, not in a PR.
    # Only new or edited reviewable posts should change the tracked report.
    changed = posts != existing.get("posts", [])
    if changed:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-url", default=PROFILE_URL)
    parser.add_argument("--sources", type=Path, default=SOURCES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--report", type=Path, default=None, help="Write a compact machine-readable scan report")
    parser.add_argument("--check", action="store_true", help="Validate the existing candidate report")
    args = parser.parse_args()

    if args.check:
        existing = load_json(args.output)
        if existing is None:
            print(f"Candidate report not found: {args.output}")
            return 0
        try:
            validate_candidates(existing)
            if args.state.exists():
                state = load_json(args.state)
                if state is None:
                    raise ValueError("Instagram scan state is invalid")
                validate_scan_state(state)
        except ValueError as exc:
            print(f"Instagram candidate validation failed: {exc}", file=sys.stderr)
            return 1
        print("Instagram candidate report validation passed")
        return 0

    try:
        report, changed = detect(
            profile_url=args.profile_url,
            sources_path=args.sources,
            output_path=args.output,
            state_path=args.state,
            report_path=args.report,
        )
    except (OSError, ValueError) as exc:
        print(f"Instagram candidate detection failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Instagram candidate detection completed: {len(report['posts'])} posts, "
        f"profile={report['profileFetchStatus']}, discovery={report['postDiscoveryStatus']}, "
        f"changed={'yes' if changed else 'no'}, state={args.state}, report={args.report or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
