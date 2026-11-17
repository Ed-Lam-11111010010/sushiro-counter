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
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = REPO_ROOT / "data" / "instagram_sources.json"
OUTPUT_PATH = REPO_ROOT / "data" / "instagram_candidates.json"
PROFILE_URL = "https://www.instagram.com/sushiro.hk/"
FETCH_ATTEMPTS = 2
MAX_DISCOVERED_POSTS = 24
HONG_KONG_TZ = timezone(timedelta(hours=8), "Asia/Hong_Kong")

POST_PATH_RE = re.compile(r"^/(p|reel|tv)/([^/?#]+)/?$", re.IGNORECASE)
HKD_PRICE_RE = re.compile(r"(?:HK\s*\$|HKD)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
AMBIGUOUS_PRICE_RE = re.compile(r"(?:^|[^A-Za-z])\$\s*\d+(?:\.\d+)?")
ISO_DATE_RE = re.compile(r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})")
CHINESE_DATE_RE = re.compile(
    r"(?:(?P<year>\d{4})\s*年)?\s*"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?"
)
POST_STATUSES = {"found", "needs_review", "future", "expired", "unavailable"}
SOURCE_STATUSES = {"posts_found", "profile_only", "manual_only", "partial", "unavailable", "available"}
PROFILE_FETCH_STATUSES = {"success", "unavailable"}
POST_DISCOVERY_STATUSES = {"found", "none", "manual_only", "partial", "unavailable"}


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
    if not match:
        return None
    return f"https://www.instagram.com/{match.group(1).lower()}/{match.group(2)}/"


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
    for _, kind, match in date_matches:
        if kind == "iso":
            year = int(match["year"])
        else:
            month = int(match["month"])
            day = int(match["day"])
            year = int(match["year"]) if match["year"] else known_year
            if not match["year"] and found and (month, day) <= (found[-1].month, found[-1].day):
                year = found[-1].year + 1
        try:
            parsed = date(year, int(match["month"]), int(match["day"]))
        except ValueError:
            continue
        found.append(parsed)
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
    value = re.sub(r"^[\s•·*#\-–—:：|,，。;；]+", "", value)
    if "：" in value:
        value = value.rsplit("：", 1)[-1]
    elif ":" in value:
        value = value.rsplit(":", 1)[-1]
    return normalize(value).strip(" -–—,，。;；")


def parse_caption_items(caption: str, post_url: str) -> tuple[list[dict[str, object]], list[str]]:
    items: list[dict[str, object]] = []
    reasons: list[str] = []
    for match in HKD_PRICE_RE.finditer(caption):
        prefix = caption[: match.start()]
        previous_price = list(HKD_PRICE_RE.finditer(prefix))
        if previous_price:
            prefix = prefix[previous_price[-1].end() :]
        line = re.split(r"[\r\n。；;]", prefix)[-1]
        name = clean_item_name(line)
        if not name or len(name) > 120:
            reasons.append("An explicit HKD price was found without a reliable item name.")
            continue
        raw_price = float(match.group(1))
        price: int | float = int(raw_price) if raw_price.is_integer() else raw_price
        item = {
            "id": candidate_item_id(post_url, name),
            "name": name,
            "price": price,
            "currency": "HKD",
            "priceSource": "explicit-caption",
        }
        if not any(existing["name"] == name for existing in items):
            items.append(item)

    if AMBIGUOUS_PRICE_RE.search(caption) and not HKD_PRICE_RE.search(caption):
        reasons.append("A price was present without an explicit HKD marker; it was not imported.")
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
        "currency": "HKD",
        "priceSource": "manual-transcription",
    }
    price = item.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or price < 0:
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
) -> dict[str, object]:
    metadata = metadata or {}
    manual_post = manual_post or {}
    today = today or datetime.now(HONG_KONG_TZ).date()
    caption = manual_post.get("caption") or metadata.get("og:description") or ""
    caption = normalize(str(caption))
    published_value = manual_post.get("publishedAt") or metadata.get("article:published_time")
    published_date = parse_published_date(published_value)
    start_date, end_date = parse_date_window(
        caption,
        published_date=published_date,
        today=today,
    )
    start_date = str(manual_post.get("startDate") or start_date or "") or None
    end_date = str(manual_post.get("endDate") or end_date or "") or None

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
        items, caption_reasons = parse_caption_items(caption, post_url)
        review_reasons.extend(caption_reasons)

    deduped_items: list[dict[str, object]] = []
    item_names: set[str] = set()
    for item in items:
        key = str(item["name"]).casefold()
        if key not in item_names:
            item_names.add(key)
            deduped_items.append(item)
    items = deduped_items

    status = "found" if items and all("price" in item for item in items) else "needs_review"
    if end_date and end_date < today.isoformat():
        status = "expired"
    elif start_date and start_date > today.isoformat():
        status = "future"

    result: dict[str, object] = {
        "url": post_url,
        "status": status,
        "verificationStatus": "unverified",
        "requiresReview": True,
        "publishedAt": published_date.isoformat() if published_date else None,
        "startDate": start_date,
        "endDate": end_date,
        "caption": caption,
        "imageUrls": sorted(set(combined_image_urls)),
        "items": items,
        "reviewReasons": sorted(set(review_reasons)),
    }
    return result


def unavailable_candidate(post_url: str) -> dict[str, object]:
    return {
        "url": post_url,
        "status": "unavailable",
        "verificationStatus": "unverified",
        "requiresReview": True,
        "publishedAt": None,
        "startDate": None,
        "endDate": None,
        "caption": "",
        "imageUrls": [],
        "items": [],
        "reviewReasons": ["The public post could not be fetched; no menu data was inferred."],
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
        if url in seen_urls:
            raise ValueError(f"Duplicate Instagram post URL: {url}")
        if status not in POST_STATUSES:
            raise ValueError(f"Invalid Instagram candidate status: {url}")
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
            if item["id"] in seen_item_ids:
                raise ValueError(f"Duplicate Instagram candidate item id: {item['id']}")
            if "price" in item:
                price = item["price"]
                if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                    raise ValueError(f"Invalid Instagram candidate price: {item['id']}")
                if isinstance(price, float) and not math.isfinite(price):
                    raise ValueError(f"Invalid Instagram candidate price: {item['id']}")
            seen_item_ids.add(item["id"])
        seen_urls.add(url)


def detect(
    *,
    profile_url: str = PROFILE_URL,
    sources_path: Path = SOURCES_PATH,
    output_path: Path = OUTPUT_PATH,
    fetcher=fetch_with_retries,
    today: date | None = None,
) -> tuple[dict[str, object], bool]:
    today = today or datetime.now(HONG_KONG_TZ).date()
    source_data, limitations = load_source_data(sources_path)
    existing = load_existing_candidates(output_path)
    current_posts: dict[str, dict[str, object]] = {}
    profile_fetch_status = "success"
    automatic_post_urls: list[str] = []
    post_fetch_failed = False
    manual_fetch_failed = False

    try:
        profile_page = fetcher(profile_url)
    except Exception as exc:
        profile_fetch_status = "unavailable"
        limitations.append("Instagram public profile was not accessible; existing candidates were preserved.")
        print(f"Instagram profile unavailable: {exc}", file=sys.stderr)
        profile_data = {"metadata": {}, "postUrls": [], "imageUrls": []}
    else:
        profile_data = parse_public_page(profile_page, profile_url)
        automatic_post_urls = profile_data["postUrls"] if isinstance(profile_data["postUrls"], list) else []
        if not automatic_post_urls:
            limitations.append(
                "Profile HTTP access succeeded, but the returned HTML exposed no public post links; client-rendered Instagram content may require a supported API or manual source entry."
            )
        for post_url in automatic_post_urls[:MAX_DISCOVERED_POSTS]:
            try:
                post_page = fetcher(post_url)
            except Exception as exc:
                post_fetch_failed = True
                print(f"Instagram post unavailable ({post_url}): {exc}", file=sys.stderr)
                current_posts[post_url] = unavailable_candidate(post_url)
                continue
            post_data = parse_public_page(post_page, post_url)
            metadata = post_data["metadata"]
            current_posts[post_url] = make_candidate(
                post_url,
                metadata if isinstance(metadata, dict) else {},
                post_data["imageUrls"] if isinstance(post_data["imageUrls"], list) else [],
                today=today,
            )

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

    posts = merge_posts(existing.get("posts"), list(current_posts.values()))
    if profile_fetch_status == "unavailable":
        source_status = "unavailable"
        post_discovery_status = "unavailable"
    elif post_fetch_failed or manual_fetch_failed:
        source_status = "partial"
        post_discovery_status = "partial"
        limitations.append("One or more discovered public post pages could not be fetched; those candidates remain review-only.")
    elif automatic_post_urls:
        source_status = "posts_found"
        post_discovery_status = "found"
    elif current_posts:
        source_status = "manual_only"
        post_discovery_status = "manual_only"
    else:
        source_status = "profile_only"
        post_discovery_status = "none"
    if not current_posts:
        limitations.append("No public post candidate was available in this run.")
    report = {
        "schemaVersion": 1,
        "account": str(source_data.get("account") or "sushiro.hk"),
        "profileUrl": profile_url,
        "sourceMethod": "public-html-best-effort",
        "profileFetchStatus": profile_fetch_status,
        "postDiscoveryStatus": post_discovery_status,
        "sourceStatus": source_status,
        "requiresReview": True,
        "limitations": sorted(set(limitations)),
        "posts": posts,
    }
    validate_candidates(report)
    changed = report != existing
    if changed:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-url", default=PROFILE_URL)
    parser.add_argument("--sources", type=Path, default=SOURCES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true", help="Validate the existing candidate report")
    args = parser.parse_args()

    if args.check:
        existing = load_json(args.output)
        if existing is None:
            print(f"Candidate report not found: {args.output}")
            return 0
        try:
            validate_candidates(existing)
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
        )
    except (OSError, ValueError) as exc:
        print(f"Instagram candidate detection failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Instagram candidate detection completed: {len(report['posts'])} posts, "
        f"profile={report['profileFetchStatus']}, discovery={report['postDiscoveryStatus']}, "
        f"changed={'yes' if changed else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
