#!/usr/bin/env python3
"""Evidence-gated Instagram promotion preview and append-only publisher.

The default mode is a deterministic dry run. This module deliberately does not
change the existing detector's review-only contract. Production writes require
the CLI flag, a reviewed policy version, an enabled policy, and an explicit
repository-side kill-switch configuration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import detect_instagram_menu as detector
import update_menu

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = REPO_ROOT / "data" / "instagram_candidates.json"
POLICY_PATH = REPO_ROOT / "data" / "instagram_autopublish_policy.json"
PROMOTIONS_PATH = REPO_ROOT / "data" / "promotions.json"
MENU_PATH = REPO_ROOT / "menu.json"
PREVIEW_PATH = REPO_ROOT / "data" / "autopublish_preview.json"
DIFF_PATH = REPO_ROOT / "data" / "autopublish_diff.json"
OCR_SCHEMA_VERSION = 1
PRICE_EVIDENCE_RE = re.compile(r"(?:HK\s*\$|HKD)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
AMBIGUOUS_PRICE_RE = re.compile(r"(?:^|[^A-Za-z])\$\s*(\d+(?:\.\d+)?)")
RESTRICTION_RE = re.compile(r"只限|僅限|仅限|指定分店|分店限定|限定店舖|限定店铺")
UNSUPPORTED_PACKAGE_RE = re.compile(r"套裝|套组|套餐|加購|加购|贈品|赠品|消費滿|消费满|即可加購|即可加购|set meal|bundle", re.IGNORECASE)
UNTIL_SOLD_RE = re.compile(r"售完即止|售罄|數量有限|数量有限")
OPEN_ENDED_RE = re.compile(r"長期供應|长期供应|不限期|不設期限|不设期限")
MAX_OCR_IMAGE_BYTES = 12 * 1024 * 1024


def allowed_public_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "instagram.com"
        or host.endswith(".instagram.com")
        or host == "cdninstagram.com"
        or host.endswith(".cdninstagram.com")
        or host == "fbcdn.net"
        or host.endswith(".fbcdn.net")
    )


def fetch_public_image_bytes(url: str) -> bytes:
    if not allowed_public_image_url(url):
        raise ValueError("OCR image URL is outside the public Instagram media host allowlist")
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; sushiro-counter-menu-evidence/1.0)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=20) as response:
        if not allowed_public_image_url(response.geturl()):
            raise ValueError("Public Instagram media redirected outside the allowed host list")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            raise ValueError("Public media URL did not return an image")
        payload = response.read(MAX_OCR_IMAGE_BYTES + 1)
    if not payload or len(payload) > MAX_OCR_IMAGE_BYTES:
        raise ValueError("Public Instagram image is empty or exceeds the size limit")
    return payload


def normalize_name(value: str) -> str:
    value = detector.normalize(value).casefold()
    return re.sub(r"[\s\-‐‑–—_·•・,，。:：()（）\[\]【】]+", "", value)


def json_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=detector.HONG_KONG_TZ)
    return result.astimezone(detector.HONG_KONG_TZ)


def validate_policy(policy: dict[str, object]) -> None:
    if policy.get("schemaVersion") != 1:
        raise ValueError("Unsupported auto-publish policy schema")
    if not isinstance(policy.get("policyVersion"), str) or not policy["policyVersion"].strip():
        raise ValueError("Policy version is required")
    for flag in ("productionEnabled", "killSwitch", "requireCompleteCarouselOcr"):
        if not isinstance(policy.get(flag), bool):
            raise ValueError(f"Policy flag must be boolean: {flag}")
    if policy.get("sourceAccount") != "sushiro.hk":
        raise ValueError("Only the configured Sushiro HK account is supported")
    bounds = policy.get("priceBoundsHKD")
    if not isinstance(bounds, dict):
        raise ValueError("priceBoundsHKD must be an object")
    minimum, maximum = bounds.get("minimum"), bounds.get("maximum")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (minimum, maximum)):
        raise ValueError("Price bounds must be finite numbers")
    if minimum <= 0 or maximum < minimum:
        raise ValueError("Price bounds are invalid")
    for key in ("minimumOcrConfidence", "fuzzyNameMatchThreshold"):
        value = policy.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise ValueError(f"Policy threshold must be between zero and one: {key}")
    for key in ("maximumEvidenceAgeHours", "maximumItemsPerRun", "maximumPromotionsPerRun"):
        value = policy.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Policy limit must be a positive integer: {key}")
    for key in ("protectedPostIds", "protectedPromotionIds", "protectedItemIds", "protectedItemNames"):
        value = policy.get(key)
        if not isinstance(value, list) or any(not isinstance(entry, str) or not entry.strip() for entry in value):
            raise ValueError(f"Policy protection list is invalid: {key}")
    providers = policy.get("trustedOcrProviders")
    if not isinstance(providers, list) or any(not isinstance(entry, str) or not entry.strip() for entry in providers):
        raise ValueError("trustedOcrProviders must be a list of non-empty provider identifiers")


def validate_candidate_document(document: dict[str, object]) -> None:
    if document.get("schemaVersion") != 1 or not isinstance(document.get("posts"), list):
        raise ValueError("Invalid Instagram candidate document")
    if document.get("account") != "sushiro.hk":
        raise ValueError("Candidate document is not for the configured Sushiro HK account")
    seen: set[str] = set()
    for post in document["posts"]:
        if not isinstance(post, dict):
            raise ValueError("Candidate post must be an object")
        if post.get("reviewStatus") != "review-only" or post.get("requiresReview") is False:
            raise ValueError("Instagram source candidates must remain review-only")
        if post.get("verificationStatus") not in {None, "unverified"}:
            raise ValueError("The auto-publish engine cannot accept pre-verified detector candidates")
        url = post.get("url")
        post_id = post.get("postId")
        canonical = detector.canonical_post_url(url) if isinstance(url, str) else None
        if not canonical or canonical != url:
            raise ValueError(f"Candidate has a non-canonical public source URL: {url}")
        expected_id = detector.canonical_post_id(canonical)
        if post_id is not None and post_id != expected_id:
            raise ValueError(f"Candidate post ID does not match source URL: {url}")
        if canonical in seen:
            raise ValueError(f"Duplicate candidate source URL: {canonical}")
        seen.add(canonical)
        items = post.get("items", [])
        if not isinstance(items, list):
            raise ValueError(f"Candidate items must be a list: {canonical}")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                raise ValueError(f"Candidate item has no reliable name: {canonical}")
            if item.get("verificationStatus") not in {None, "unverified"}:
                raise ValueError(f"Detector item must remain unverified: {canonical}")


def validate_ocr_document(document: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if document is None:
        return {}
    if document.get("schemaVersion") != OCR_SCHEMA_VERSION or not isinstance(document.get("posts"), list):
        raise ValueError("Invalid OCR evidence sidecar schema")
    by_id: dict[str, dict[str, object]] = {}
    for post in document["posts"]:
        if not isinstance(post, dict) or not isinstance(post.get("postId"), str):
            raise ValueError("OCR evidence post must include a postId")
        if post["postId"] in by_id:
            raise ValueError(f"Duplicate OCR evidence for post {post['postId']}")
        if not isinstance(post.get("images"), list) or not isinstance(post.get("items", []), list):
            raise ValueError(f"OCR evidence images/items must be lists: {post['postId']}")
        by_id[post["postId"]] = post
    return by_id


def attach_ocr_items(
    post: dict[str, object],
    ocr_post: dict[str, object] | None,
    policy: dict[str, object],
) -> tuple[list[dict[str, object]], list[str]]:
    raw_items = post.get("items") if isinstance(post.get("items"), list) else []
    items = [dict(item) for item in raw_items if isinstance(item, dict)]
    if not ocr_post:
        return items, []
    reasons: list[str] = []
    if ocr_post.get("provider") not in policy.get("trustedOcrProviders", []):
        reasons.append("ocr_provider_not_trusted")
    if ocr_post.get("fingerprint") != post.get("contentFingerprint"):
        reasons.append("ocr_source_fingerprint_mismatch")
    if ocr_post.get("coverageComplete") is not True:
        reasons.append("carousel_ocr_coverage_incomplete")
    candidate_images = post.get("imageUrls") if isinstance(post.get("imageUrls"), list) else []
    image_entries = ocr_post.get("images") if isinstance(ocr_post.get("images"), list) else []
    ocr_images: dict[str, dict[str, object]] = {}
    for image in image_entries:
        if not isinstance(image, dict) or not isinstance(image.get("url"), str):
            reasons.append("ocr_image_record_invalid")
            continue
        url = image["url"]
        if url in ocr_images:
            reasons.append("ocr_image_duplicate")
            continue
        ocr_images[url] = image
        if image.get("status") != "complete":
            reasons.append("carousel_ocr_image_failed")
        confidence = image.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not policy["minimumOcrConfidence"] <= confidence <= 1
        ):
            reasons.append("carousel_ocr_confidence_ambiguous")
        if not isinstance(image.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", image["sha256"]):
            reasons.append("ocr_image_hash_missing")
    if set(candidate_images) != set(ocr_images):
        reasons.append("carousel_ocr_did_not_cover_all_exposed_images")

    by_name = {normalize_name(str(item.get("name", ""))): item for item in items}
    for raw_ocr_item in ocr_post.get("items", []):
        if not isinstance(raw_ocr_item, dict):
            reasons.append("ocr_item_record_invalid")
            continue
        name = raw_ocr_item.get("name")
        if not isinstance(name, str) or not detector.reliable_item_name(name):
            reasons.append("ocr_item_name_unreliable")
            continue
        confidence = raw_ocr_item.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not policy["minimumOcrConfidence"] <= confidence <= 1
        ):
            reasons.append("ocr_item_confidence_ambiguous")
            continue
        image_url = raw_ocr_item.get("imageUrl")
        image = ocr_images.get(image_url) if isinstance(image_url, str) else None
        if not image or raw_ocr_item.get("imageSha256") != image.get("sha256"):
            reasons.append("ocr_item_image_provenance_mismatch")
            continue
        quote = raw_ocr_item.get("priceEvidenceText")
        price_match = PRICE_EVIDENCE_RE.search(quote) if isinstance(quote, str) else None
        price = raw_ocr_item.get("price")
        if not price_match or isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
            reasons.append("ocr_price_not_explicit_hkd")
            continue
        if float(price_match.group(1)) != float(price):
            reasons.append("ocr_price_evidence_mismatch")
            continue
        item = by_name.get(normalize_name(name))
        caption_price = _numeric_price(item) if item else None
        if item and item.get("priceConfidence") == "explicit-hkd" and caption_price is not None and caption_price != float(price):
            reasons.append("caption_ocr_price_conflict")
            continue
        if item is None:
            item = {
                "id": detector.candidate_item_id(str(post["url"]), name),
                "name": name,
                "itemType": raw_ocr_item.get("itemType", detector.classify_item_type(name)),
                "foodCategory": raw_ocr_item.get("foodCategory", "drink" if detector.classify_item_type(name) == "drink" else "food"),
                "purchaseCondition": raw_ocr_item.get("purchaseCondition", "standard"),
            }
            items.append(item)
            by_name[normalize_name(name)] = item
        evidence = dict(raw_ocr_item)
        evidence["source"] = "ocr"
        evidence["provider"] = ocr_post.get("provider")
        item["ocrEvidence"] = evidence
        if "price" not in item:
            item["price"] = price
            item["currency"] = "HKD"
            item["priceConfidence"] = "explicit-hkd"
            item["priceEvidenceSource"] = "ocr"
            item["priceEvidenceQuote"] = quote
            item["priceEvidenceText"] = quote
        for field in (
            "startDate", "endDate", "dateEvidenceText", "dateConfidence", "supplyCondition",
            "availabilityEvidenceText", "branchScope", "branchEvidenceText", "branchExclusions",
        ):
            if field in raw_ocr_item and field not in item:
                item[field] = raw_ocr_item[field]
    return items, sorted(set(reasons))


def verify_ocr_image_bytes(
    post: dict[str, object],
    ocr_post: dict[str, object] | None,
    *,
    image_fetcher: Callable[[str], bytes],
) -> list[str]:
    if not ocr_post:
        return []
    reasons: list[str] = []
    candidate_images = post.get("imageUrls") if isinstance(post.get("imageUrls"), list) else []
    for image in ocr_post.get("images", []):
        if not isinstance(image, dict):
            continue
        url = image.get("url")
        expected = image.get("sha256")
        if not isinstance(url, str) or url not in candidate_images:
            continue
        try:
            actual = hashlib.sha256(image_fetcher(url)).hexdigest()
        except Exception as exc:
            reasons.append(f"ocr_source_image_unavailable:{type(exc).__name__}")
            continue
        if actual != expected:
            reasons.append("ocr_source_image_hash_mismatch")
    return sorted(set(reasons))


def build_live_snapshot(
    post: dict[str, object],
    *,
    as_of: datetime,
    instagram_fetcher: Callable[[str], str],
) -> tuple[dict[str, object] | None, str | None]:
    url = str(post.get("url") or "")
    try:
        page = instagram_fetcher(url)
    except Exception as exc:
        return None, f"live_source_unavailable:{type(exc).__name__}"
    parsed = detector.parse_public_page(page, url)
    if not detector.has_usable_post_metadata(url, parsed):
        return None, "live_source_metadata_unusable"
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    fresh = detector.make_candidate(
        url,
        metadata,
        parsed.get("imageUrls") if isinstance(parsed.get("imageUrls"), list) else [],
        today=as_of.date(),
    )
    return fresh, None


def evidence_age_reason(post: dict[str, object], as_of: datetime, maximum_hours: int) -> str | None:
    observed = parse_timestamp(post.get("observedAt"))
    if observed is None:
        return "candidate_observation_time_missing"
    age = as_of - observed
    if age < timedelta(0) and as_of.time() == time.min and observed.date() == as_of.date():
        # --as-of is day-precision; same-day scan timestamps remain current.
        age = timedelta(0)
    if age < timedelta(minutes=-5):
        return "candidate_observation_time_in_future"
    if age > timedelta(hours=maximum_hours):
        return "candidate_evidence_stale"
    return None


def _live_item_by_name(fresh_post: dict[str, object] | None, name: str) -> dict[str, object] | None:
    if not fresh_post:
        return None
    for item in fresh_post.get("items", []):
        if isinstance(item, dict) and normalize_name(str(item.get("name", ""))) == normalize_name(name):
            return item
    return None


def _numeric_price(item: dict[str, object]) -> float | None:
    value = item.get("price")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _date_evidence_matches(item: dict[str, object], published: date | None, as_of: date) -> bool:
    evidence = item.get("dateEvidenceText")
    start = item.get("startDate")
    end = item.get("endDate")
    if not isinstance(evidence, str) or not evidence.strip() or not isinstance(start, str):
        return False
    parsed_start, parsed_end = detector.parse_date_window(evidence, published_date=published, today=as_of)
    return parsed_start == start and parsed_end == end


def _verified_catalog(menu: dict[str, object], promotions: dict[str, object]) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    by_name: dict[str, dict[str, object]] = {}
    by_id: dict[str, dict[str, object]] = {}
    categories = menu.get("categories") if isinstance(menu.get("categories"), dict) else {}
    for category, items in categories.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            indexed = {**item, "_source": "menu", "_category": category}
            name_key = normalize_name(str(item.get("name", "")))
            item_id = item.get("id")
            if name_key:
                by_name[name_key] = indexed
            if isinstance(item_id, str):
                by_id[item_id] = indexed
    for promotion in promotions.get("promotions", []):
        if not isinstance(promotion, dict):
            continue
        if promotion.get("verificationStatus") != "verified":
            continue
        for item in promotion.get("items", []):
            if not isinstance(item, dict) or item.get("verificationStatus") != "verified":
                continue
            indexed = {**item, "_source": "promotion", "_promotion": promotion}
            name_key = normalize_name(str(item.get("name", "")))
            item_id = item.get("id")
            if name_key:
                by_name[name_key] = indexed
            if isinstance(item_id, str):
                by_id[item_id] = indexed
    return by_name, by_id


def _catalog_conflict(
    item: dict[str, object],
    *,
    catalog_by_name: dict[str, dict[str, object]],
    catalog_by_id: dict[str, dict[str, object]],
    threshold: float,
) -> list[str]:
    reasons: list[str] = []
    name = str(item.get("name", ""))
    name_key = normalize_name(name)
    item_id = item.get("id")
    existing_id = item.get("existingMenuId")
    if isinstance(existing_id, str) and existing_id:
        reasons.append("already_matches_live_menu")
    if isinstance(item_id, str) and item_id in catalog_by_id:
        existing = catalog_by_id[item_id]
        if normalize_name(str(existing.get("name", ""))) != name_key:
            reasons.append("menu_item_id_collision")
        else:
            reasons.append("already_verified_item_id")
    existing = catalog_by_name.get(name_key)
    if existing:
        old_price, new_price = _numeric_price(existing), _numeric_price(item)
        if old_price is not None and new_price is not None and old_price != new_price:
            reasons.append("verified_price_conflict")
        else:
            reasons.append("duplicate_verified_item")
    elif name_key:
        closest = max(
            ((SequenceMatcher(None, name_key, old_name).ratio(), old) for old_name, old in catalog_by_name.items()),
            default=(0.0, None),
            key=lambda pair: pair[0],
        )
        if closest[0] >= threshold and closest[1] is not None:
            reasons.append("possible_duplicate_name")
    return sorted(set(reasons))


def _verify_official_price(
    item: dict[str, object],
    *,
    official_fetcher: Callable[[str], str],
) -> tuple[bool, str | None, str | None]:
    evidence = item.get("officialPriceEvidence")
    if not isinstance(evidence, dict):
        return False, None, "official_price_evidence_missing"
    source_url = evidence.get("sourceUrl")
    parsed_url = urlparse(source_url) if isinstance(source_url, str) else None
    if not parsed_url or parsed_url.scheme != "https" or parsed_url.hostname not in {"sushirohk.com.hk", "www.sushirohk.com.hk"}:
        return False, None, "official_price_source_not_allowlisted"
    try:
        page = official_fetcher(source_url)
        official_items = update_menu.parse_items(page)
    except Exception as exc:
        return False, source_url, f"official_price_source_unavailable:{type(exc).__name__}"
    name_key = normalize_name(str(item.get("name", "")))
    price = _numeric_price(item)
    matches = [
        found for found in official_items
        if normalize_name(str(found.get("name", ""))) == name_key
        and _numeric_price(found) == price
    ]
    if len(matches) != 1:
        return False, source_url, "official_price_not_exactly_confirmed"
    return True, source_url, None


def _evaluate_item(
    post: dict[str, object],
    item: dict[str, object],
    *,
    post_reasons: list[str],
    fresh_post: dict[str, object] | None,
    live_error: str | None,
    ocr_post: dict[str, object] | None,
    ocr_reasons: list[str],
    policy: dict[str, object],
    catalog_by_name: dict[str, dict[str, object]],
    catalog_by_id: dict[str, dict[str, object]],
    as_of: datetime,
    official_fetcher: Callable[[str], str],
) -> tuple[dict[str, object], dict[str, object] | None]:
    reasons = list(post_reasons) + list(ocr_reasons)
    name = str(item.get("name", "")).strip()
    item_type = item.get("itemType") if item.get("itemType") in detector.ITEM_TYPES else detector.classify_item_type(name)
    purchase = item.get("purchaseCondition", "standard")
    if not detector.reliable_item_name(name):
        reasons.append("item_name_unreliable")
    if item_type not in {"dish", "drink"}:
        reasons.append("merchandise_or_unsupported_item_type")
    if purchase != "standard":
        reasons.append("conditional_add_on_or_unknown_purchase_condition")
    if UNSUPPORTED_PACKAGE_RE.search(name):
        reasons.append("bundle_threshold_or_gift_not_a_standalone_menu_item")

    post_id = str(post.get("postId") or detector.canonical_post_id(str(post.get("url", ""))))
    protected_ids = {value.casefold() for value in policy["protectedItemIds"]}
    protected_names = {normalize_name(value) for value in policy["protectedItemNames"]}
    protected_posts = {value.casefold() for value in policy["protectedPostIds"]}
    if post_id.casefold() in protected_posts or str(item.get("id", "")).casefold() in protected_ids or normalize_name(name) in protected_names:
        reasons.append("protected_pr_10_candidate")

    price_source = item.get("priceEvidenceSource")
    price_confidence = item.get("priceConfidence")
    price = _numeric_price(item)
    price_quote = item.get("priceEvidenceQuote") or item.get("priceEvidenceText") or item.get("priceText")
    price_match = PRICE_EVIDENCE_RE.search(price_quote) if isinstance(price_quote, str) else None
    verified_source_url: str | None = None
    if price_confidence == "ambiguous-currency" or (
        isinstance(item.get("priceText"), str) and AMBIGUOUS_PRICE_RE.search(str(item["priceText"]))
    ):
        reasons.append("ambiguous_price_currency")
    if price_source == "caption":
        if price_confidence != "explicit-hkd" or not price_match:
            if isinstance(item.get("priceText"), str) and AMBIGUOUS_PRICE_RE.search(str(item["priceText"])):
                reasons.append("ambiguous_price_currency")
            else:
                reasons.append("caption_price_not_explicit_hkd")
        elif price is None or float(price_match.group(1)) != price or item.get("currency") != "HKD":
            reasons.append("caption_price_evidence_mismatch")
        live_item = _live_item_by_name(fresh_post, name)
        caption_verification_required = price_confidence == "explicit-hkd" or price is not None
        if live_error and caption_verification_required:
            reasons.append("live_source_not_verified")
        elif caption_verification_required and (fresh_post is None or live_item is None):
            reasons.append("item_not_reproduced_from_live_caption")
        elif caption_verification_required and live_item and (
            _numeric_price(live_item) != price or live_item.get("priceConfidence") != "explicit-hkd"
        ):
            reasons.append("live_caption_price_mismatch")
    elif price_source == "ocr":
        evidence = item.get("ocrEvidence") if isinstance(item.get("ocrEvidence"), dict) else {}
        confidence = evidence.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or confidence < policy["minimumOcrConfidence"]:
            reasons.append("ocr_price_confidence_ambiguous")
        if not price_match or price is None or float(price_match.group(1)) != price or item.get("currency") != "HKD":
            reasons.append("ocr_price_not_explicit_or_mismatched")
        if not isinstance(evidence.get("nameEvidenceText"), str) or normalize_name(name) not in normalize_name(evidence["nameEvidenceText"]):
            reasons.append("ocr_name_evidence_mismatch")
        live_item = _live_item_by_name(fresh_post, name)
        if live_error:
            reasons.append("live_source_not_verified")
        elif fresh_post is None:
            reasons.append("live_source_not_verified")
        elif live_item and _numeric_price(live_item) is not None:
            if _numeric_price(live_item) != price:
                reasons.append("caption_ocr_price_conflict")
        image_url = evidence.get("imageUrl")
        image_sha = evidence.get("imageSha256")
        if not ocr_post or not isinstance(image_url, str) or not isinstance(image_sha, str):
            reasons.append("ocr_source_image_missing")
        else:
            images = [entry for entry in ocr_post.get("images", []) if isinstance(entry, dict) and entry.get("url") == image_url]
            if not images or images[0].get("sha256") != image_sha:
                reasons.append("ocr_source_image_hash_mismatch")
        verified_source_url = str(post.get("url", ""))
        ocr_evidence = item.get("ocrEvidence") if isinstance(item.get("ocrEvidence"), dict) else {}
    elif price_source == "official-source":
        confirmed, verified_source_url, official_reason = _verify_official_price(item, official_fetcher=official_fetcher)
        if not confirmed and official_reason:
            reasons.append(official_reason)
        if price is None or price_match is None or float(price_match.group(1)) != price:
            reasons.append("official_price_evidence_mismatch")
        live_item = _live_item_by_name(fresh_post, name)
        if live_error:
            reasons.append("live_source_not_verified")
        elif fresh_post is None or live_item is None:
            reasons.append("item_not_reproduced_from_live_caption")
    else:
        reasons.append("price_evidence_source_missing_or_unsupported")

    if price is None or price <= 0:
        reasons.append("price_missing_or_not_positive")
    else:
        bounds = policy["priceBoundsHKD"]
        if price < bounds["minimum"] or price > bounds["maximum"]:
            reasons.append("price_outside_policy_bounds")

    start_value = item.get("startDate")
    end_value = item.get("endDate")
    try:
        start_date = date.fromisoformat(start_value) if isinstance(start_value, str) else None
        end_date = date.fromisoformat(end_value) if isinstance(end_value, str) else None
    except ValueError:
        start_date, end_date = None, None
        reasons.append("promotion_date_invalid")
    if start_date is None:
        reasons.append("promotion_start_date_missing")
    if item.get("dateConfidence") not in {"explicit-section", "high-confidence-ocr"}:
        reasons.append("promotion_date_not_explicit")
    published = detector.parse_published_date(post.get("publishedAt"))
    if not _date_evidence_matches(item, published, as_of.date()):
        reasons.append("promotion_date_evidence_mismatch")
    live_item = _live_item_by_name(fresh_post, name)
    if live_item and (live_item.get("startDate") != start_value or live_item.get("endDate") != end_value):
        # OCR dates may enrich a caption that did not state item dates, but any
        # explicit caption dates that disagree are a hard conflict.
        if live_item.get("startDate") or live_item.get("endDate"):
            reasons.append("caption_date_conflict")
    if end_date and start_date and end_date < start_date:
        reasons.append("promotion_end_before_start")
    if end_date and end_date < as_of.date():
        reasons.append("promotion_expired")
    availability_text = str(item.get("availabilityEvidenceText") or item.get("dateEvidenceText") or "")
    supply_condition = item.get("supplyCondition")
    if supply_condition == "until-sold-out":
        if not UNTIL_SOLD_RE.search(availability_text):
            reasons.append("until_sold_out_not_supported_by_evidence")
    elif end_date is None and not OPEN_ENDED_RE.search(availability_text):
        reasons.append("stock_or_end_condition_unknown")
    elif end_date is not None and not availability_text.strip():
        reasons.append("availability_evidence_missing")

    branch_scope = item.get("branchScope")
    branch_evidence = str(item.get("branchEvidenceText") or "")
    if branch_scope != "all-hk" or not branch_evidence.strip() or "*" in branch_evidence or "＊" in branch_evidence:
        reasons.append("branch_scope_unknown_or_restricted")
    if RESTRICTION_RE.search(branch_evidence):
        reasons.append("branch_restriction_detected")
    if live_item:
        if item_type != live_item.get("itemType"):
            reasons.append("live_item_type_mismatch")
        if purchase != live_item.get("purchaseCondition"):
            reasons.append("live_purchase_condition_mismatch")
        if branch_scope != live_item.get("branchScope"):
            reasons.append("live_branch_scope_mismatch")
        if normalize_name(branch_evidence) != normalize_name(str(live_item.get("branchEvidenceText") or "")):
            reasons.append("live_branch_evidence_mismatch")
        if supply_condition != live_item.get("supplyCondition"):
            reasons.append("live_supply_condition_mismatch")

    if post.get("imageUrls") and policy.get("requireCompleteCarouselOcr"):
        if not ocr_post or ocr_post.get("coverageComplete") is not True:
            reasons.append("carousel_media_not_fully_ocr_verified")
    reasons.extend(_catalog_conflict(
        item,
        catalog_by_name=catalog_by_name,
        catalog_by_id=catalog_by_id,
        threshold=float(policy["fuzzyNameMatchThreshold"]),
    ))
    stable_item_id = stable_promotion_item_id(post_id, name)
    decision = {
        "postId": post_id,
        "sourcePostUrl": post.get("url"),
        "contentFingerprint": post.get("contentFingerprint"),
        "itemName": name,
        "itemType": item_type,
        "foodCategory": "drink" if item_type == "drink" else "food",
        "candidateItemId": item.get("id"),
        "verifiedItemId": stable_item_id,
        "price": price,
        "currency": item.get("currency"),
        "priceEvidenceSource": price_source,
        "priceEvidenceText": price_quote,
        "priceEvidenceProvider": item.get("ocrEvidence", {}).get("provider") if isinstance(item.get("ocrEvidence"), dict) else None,
        "startDate": start_value,
        "endDate": end_value,
        "dateEvidenceText": item.get("dateEvidenceText"),
        "branchScope": branch_scope,
        "branchEvidenceText": branch_evidence,
        "supplyCondition": supply_condition,
        "availabilityEvidenceText": availability_text,
        "sourceEvidenceUrl": verified_source_url or post.get("url"),
        "decision": "eligible" if not reasons else "quarantined",
        "reasons": sorted(set(reasons)),
    }
    if reasons or start_date is None or price is None:
        return decision, None
    return decision, {
        "post": post,
        "item": item,
        "postId": post_id,
        "itemId": stable_item_id,
        "name": name,
        "itemType": item_type,
        "price": int(price) if price.is_integer() else price,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat() if end_date else None,
        "supplyCondition": supply_condition,
        "branchScope": branch_scope,
        "sourceUrl": str(post.get("url")),
        "fingerprint": str(post.get("contentFingerprint") or ""),
        "publishedAt": post.get("publishedAt"),
        "observedAt": post.get("observedAt"),
        "priceEvidence": {
            "source": price_source,
            "text": price_quote,
            "sourceUrl": verified_source_url or post.get("url"),
            **({"provider": ocr_evidence.get("provider")} if price_source == "ocr" else {}),
        },
        "dateEvidenceText": item.get("dateEvidenceText"),
        "branchEvidenceText": branch_evidence,
        "availabilityEvidenceText": availability_text,
    }


def stable_promotion_item_id(post_id: str, name: str) -> str:
    digest = hashlib.sha256(normalize_name(name).encode("utf-8")).hexdigest()[:10]
    safe_post_id = re.sub(r"[^A-Za-z0-9_-]", "", post_id).lower()
    return f"ig_{safe_post_id}_{digest}"


def build_promotions(eligible: list[dict[str, object]], policy_version: str) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str | None, str | None, str], list[dict[str, object]]] = {}
    for record in eligible:
        key = (
            str(record["postId"]), str(record["startDate"]), record.get("endDate"),
            record.get("supplyCondition"), str(record["branchScope"]),
        )
        grouped.setdefault(key, []).append(record)
    promotions: list[dict[str, object]] = []
    for (post_id, start_date, end_date, supply, _branch_scope), records in sorted(grouped.items()):
        source_url = str(records[0]["sourceUrl"])
        promotion_id = f"instagram-{post_id.lower()}-{start_date}"
        category = f"期間限定・Instagram・{post_id}・{start_date}"
        items = []
        for record in sorted(records, key=lambda entry: (normalize_name(str(entry["name"])), str(entry["itemId"]))):
            items.append({
                "id": record["itemId"],
                "name": record["name"],
                "price": record["price"],
                "verificationStatus": "verified",
                "verificationEvidence": record["priceEvidence"],
            })
        published_dates = [record.get("publishedAt") for record in records if isinstance(record.get("publishedAt"), str)]
        promotion: dict[str, object] = {
            "id": promotion_id,
            "category": category,
            "verificationStatus": "verified",
            "sourceUrls": [source_url],
            "announcementDate": min(published_dates) if published_dates else None,
            "startDate": start_date,
            "endDate": end_date,
            "availability": "售完即止；分店供應以官方公告為準。" if supply == "until-sold-out" else "期間限定；分店供應以官方公告為準。",
            "items": items,
            "verificationAudit": {
                "method": "evidence-gated-instagram-policy",
                "policyVersion": policy_version,
                "sourcePostId": post_id,
                "sourceFingerprint": records[0]["fingerprint"],
                "observedAt": records[0].get("observedAt"),
                "dateEvidenceText": records[0].get("dateEvidenceText"),
                "branchEvidenceText": records[0].get("branchEvidenceText"),
                "availabilityEvidenceText": records[0].get("availabilityEvidenceText"),
            },
        }
        promotions.append(promotion)
    return promotions


def evaluate_candidates(
    candidates: dict[str, object],
    promotions: dict[str, object],
    menu: dict[str, object],
    policy: dict[str, object],
    *,
    as_of: datetime,
    ocr_document: dict[str, object] | None = None,
    instagram_fetcher: Callable[[str], str] = detector.fetch_with_retries,
    image_fetcher: Callable[[str], bytes] = fetch_public_image_bytes,
    official_fetcher: Callable[[str], str] = update_menu.fetch,
) -> dict[str, object]:
    validate_policy(policy)
    validate_candidate_document(candidates)
    update_menu.validate_promotion_data(promotions)
    ocr_by_post = validate_ocr_document(ocr_document)
    catalog_by_name, catalog_by_id = _verified_catalog(menu, promotions)
    summary_status = candidates.get("postDiscoveryStatus")
    scan_is_complete = (
        candidates.get("discoveryComplete") is True
        and candidates.get("discoveryCoverage") == "public-html-best-effort-not-exhaustive"
        and summary_status == "found"
    )
    live_cache: dict[str, tuple[dict[str, object] | None, str | None]] = {}
    eligible_records: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []

    for post in sorted(candidates["posts"], key=lambda value: (str(value.get("postId") or ""), str(value.get("url") or ""))):
        post_id = str(post.get("postId") or detector.canonical_post_id(str(post.get("url", ""))))
        global_reasons: list[str] = []
        if not scan_is_complete:
            global_reasons.append("scan_missing_or_incomplete")
        if candidates.get("profileFetchStatus") not in {None, "success"}:
            global_reasons.append("instagram_profile_unavailable")
        if candidates.get("sourceStatus") in {"partial", "unavailable"}:
            global_reasons.append("candidate_source_status_partial")
        if not isinstance(post.get("contentFingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(post.get("contentFingerprint"))):
            global_reasons.append("source_fingerprint_missing")
        age_reason = evidence_age_reason(post, as_of, int(policy["maximumEvidenceAgeHours"]))
        if age_reason:
            global_reasons.append(age_reason)
        if post_id in set(policy["protectedPostIds"]):
            global_reasons.append("protected_pr_10_source_post")

        ocr_post = ocr_by_post.get(post_id)
        items, ocr_reasons = attach_ocr_items(post, ocr_post, policy)
        ocr_reasons.extend(verify_ocr_image_bytes(post, ocr_post, image_fetcher=image_fetcher))
        ocr_reasons = sorted(set(ocr_reasons))
        viable_source_item = any(
            item.get("priceConfidence") == "explicit-hkd"
            or item.get("priceEvidenceSource") in {"ocr", "official-source"}
            for item in items
        )
        fresh_post: dict[str, object] | None = None
        live_error: str | None = None
        if viable_source_item and not global_reasons:
            url = str(post["url"])
            if url not in live_cache:
                live_cache[url] = build_live_snapshot(post, as_of=as_of, instagram_fetcher=instagram_fetcher)
            fresh_post, live_error = live_cache[url]
            if live_error:
                global_reasons.append(live_error)
            else:
                author = fresh_post.get("authorEvidence") if isinstance(fresh_post, dict) else None
                if not isinstance(author, dict) or author.get("account") != policy["sourceAccount"]:
                    global_reasons.append("public_post_author_not_verified")
                if fresh_post and fresh_post.get("contentFingerprint") != post.get("contentFingerprint"):
                    global_reasons.append("source_fingerprint_changed_since_scan")
                if fresh_post and post.get("publishedAt") != fresh_post.get("publishedAt"):
                    global_reasons.append("publication_date_changed_since_scan")
        elif viable_source_item and global_reasons:
            live_error = "live_source_not_checked_due_to_incomplete_scan"

        if not items:
            decisions.append({
                "postId": post_id,
                "sourcePostUrl": post.get("url"),
                "decision": "no_item_candidates",
                "reasons": sorted(set(global_reasons + ocr_reasons or ["no_item_candidates"])),
            })
            continue
        for item in items:
            decision, eligible_record = _evaluate_item(
                post,
                item,
                post_reasons=global_reasons,
                fresh_post=fresh_post,
                live_error=live_error,
                ocr_post=ocr_post,
                ocr_reasons=ocr_reasons,
                policy=policy,
                catalog_by_name=catalog_by_name,
                catalog_by_id=catalog_by_id,
                as_of=as_of,
                official_fetcher=official_fetcher,
            )
            decisions.append(decision)
            if eligible_record:
                eligible_records.append(eligible_record)

    eligible_records.sort(key=lambda item: (str(item["postId"]), str(item["startDate"]), normalize_name(str(item["name"]))))
    seen_names: set[str] = set()
    accepted: list[dict[str, object]] = []
    for record in eligible_records:
        key = normalize_name(str(record["name"]))
        if key in seen_names:
            for decision in decisions:
                if decision.get("candidateItemId") == record["item"].get("id") and decision.get("decision") == "eligible":
                    decision["decision"] = "quarantined"
                    decision["reasons"] = ["duplicate_candidate_name"]
                    break
            continue
        seen_names.add(key)
        accepted.append(record)

    proposed_promotions = build_promotions(accepted, str(policy["policyVersion"]))
    limits_exceeded = len(accepted) > int(policy["maximumItemsPerRun"]) or len(proposed_promotions) > int(policy["maximumPromotionsPerRun"])
    if limits_exceeded:
        for decision in decisions:
            if decision.get("decision") == "eligible":
                decision["decision"] = "quarantined"
                decision["reasons"] = ["batch_change_limit_exceeded"]
        accepted = []
        proposed_promotions = []

    existing_promotions = copy.deepcopy(promotions)
    existing_ids = {str(promotion.get("id")) for promotion in existing_promotions.get("promotions", []) if isinstance(promotion, dict)}
    existing_categories = {str(promotion.get("category")) for promotion in existing_promotions.get("promotions", []) if isinstance(promotion, dict)}
    conflict_ids = {str(item.get("id")) for promotion in existing_promotions.get("promotions", []) if isinstance(promotion, dict) for item in promotion.get("items", []) if isinstance(item, dict)}
    protected_promotion_ids = set(policy["protectedPromotionIds"])
    retained_promotions: list[dict[str, object]] = []
    rejected_source_posts: set[str] = set()
    for proposed in proposed_promotions:
        source_post_id = str(proposed["verificationAudit"]["sourcePostId"])
        protected_collision = proposed["id"] in protected_promotion_ids
        if proposed["id"] in existing_ids or proposed["category"] in existing_categories or protected_collision:
            for decision in decisions:
                if decision.get("postId") == source_post_id and decision.get("decision") == "eligible":
                    decision["decision"] = "quarantined"
                    decision["reasons"] = ["protected_promotion_id_collision" if protected_collision else "promotion_id_or_category_conflict"]
            rejected_source_posts.add(source_post_id)
            continue
        collision_found = False
        for candidate_item in proposed.get("items", []):
            if candidate_item["id"] in conflict_ids:
                collision_found = True
        if collision_found:
            rejected_source_posts.add(source_post_id)
            for decision in decisions:
                if decision.get("postId") == source_post_id and decision.get("decision") == "eligible":
                    decision["decision"] = "quarantined"
                    decision["reasons"] = ["promotion_item_id_conflict"]
        else:
            retained_promotions.append(proposed)
    proposed_promotions = retained_promotions
    if rejected_source_posts:
        accepted = [record for record in accepted if record["postId"] not in rejected_source_posts]

    next_promotions = copy.deepcopy(existing_promotions)
    next_promotions["promotions"] = list(next_promotions.get("promotions", [])) + proposed_promotions
    if proposed_promotions:
        next_promotions["updatedAt"] = as_of.date().isoformat()
    update_menu.validate_promotion_data(next_promotions)
    counts: dict[str, int] = {}
    for decision in decisions:
        for reason in decision.get("reasons", []):
            counts[reason] = counts.get(reason, 0) + 1
    preview = {
        "schemaVersion": 1,
        "mode": "dry-run",
        "dryRunOnly": True,
        "policyVersion": policy["policyVersion"],
        "productionAutoPublishEnabled": False,
        "killSwitch": policy["killSwitch"],
        "asOfDate": as_of.date().isoformat(),
        "sourceReport": {
            "postDiscoveryStatus": candidates.get("postDiscoveryStatus"),
            "discoveryComplete": scan_is_complete,
            "discoveryCoverage": candidates.get("discoveryCoverage", "unknown"),
            "postCount": len(candidates["posts"]),
        },
        "summary": {
            "candidateItems": sum(1 for decision in decisions if "itemName" in decision),
            "eligibleItems": len(accepted),
            "quarantinedItems": sum(decision.get("decision") == "quarantined" for decision in decisions),
            "postsWithoutItems": sum(decision.get("decision") == "no_item_candidates" for decision in decisions),
            "promotionPreviews": len(proposed_promotions),
            "batchChangeLimitExceeded": limits_exceeded,
        },
        "reasonCounts": dict(sorted(counts.items())),
        "decisions": sorted(decisions, key=lambda entry: (str(entry.get("postId", "")), str(entry.get("itemName", "")), str(entry.get("candidateItemId", "")))),
        "eligiblePromotions": proposed_promotions,
    }
    diff = {
        "schemaVersion": 1,
        "mode": "dry-run",
        "policyVersion": policy["policyVersion"],
        "promotionsPath": "data/promotions.json",
        "beforeSha256": json_digest(existing_promotions),
        "proposedSha256": json_digest(next_promotions),
        "wouldChangePromotions": bool(proposed_promotions),
        "addedPromotionIds": [promotion["id"] for promotion in proposed_promotions],
        "removedPromotionIds": [],
        "updatedVerifiedPromotionIds": [],
        "existingPromotionCount": len(existing_promotions.get("promotions", [])),
        "proposedPromotionCount": len(next_promotions.get("promotions", [])),
    }
    return {"preview": preview, "diff": diff, "nextPromotions": next_promotions}


def production_gate_reasons(policy: dict[str, object], environment: dict[str, str] | None = None) -> list[str]:
    if environment is None:
        environment = os.environ
    reasons: list[str] = []
    if policy.get("productionEnabled") is not True:
        reasons.append("policy_production_flag_disabled")
    if policy.get("killSwitch") is not False:
        reasons.append("policy_kill_switch_active")
    if environment.get("SUSHIRO_INSTAGRAM_AUTOPUBLISH_ENABLED", "").lower() != "true":
        reasons.append("environment_feature_flag_disabled")
    if environment.get("SUSHIRO_INSTAGRAM_AUTOPUBLISH_KILL_SWITCH", "true").lower() != "false":
        reasons.append("environment_kill_switch_active")
    if environment.get("SUSHIRO_INSTAGRAM_AUTOPUBLISH_APPROVED_POLICY_VERSION") != policy.get("policyVersion"):
        reasons.append("policy_version_not_explicitly_approved")
    if policy.get("approvedPolicyVersion") != policy.get("policyVersion"):
        reasons.append("policy_file_approval_version_mismatch")
    return reasons


def run(
    *,
    candidates_path: Path = CANDIDATES_PATH,
    policy_path: Path = POLICY_PATH,
    promotions_path: Path = PROMOTIONS_PATH,
    menu_path: Path = MENU_PATH,
    preview_path: Path = PREVIEW_PATH,
    diff_path: Path = DIFF_PATH,
    ocr_path: Path | None = None,
    auto_publish: bool = False,
    as_of: datetime | None = None,
    instagram_fetcher: Callable[[str], str] = detector.fetch_with_retries,
    image_fetcher: Callable[[str], bytes] = fetch_public_image_bytes,
    official_fetcher: Callable[[str], str] = update_menu.fetch,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    candidates = read_json(candidates_path)
    policy = read_json(policy_path)
    promotions = read_json(promotions_path)
    menu = read_json(menu_path)
    ocr_document = read_json(ocr_path) if ocr_path else None
    as_of = as_of or datetime.now(detector.HONG_KONG_TZ).replace(microsecond=0)
    result = evaluate_candidates(
        candidates,
        promotions,
        menu,
        policy,
        as_of=as_of,
        ocr_document=ocr_document,
        instagram_fetcher=instagram_fetcher,
        image_fetcher=image_fetcher,
        official_fetcher=official_fetcher,
    )
    result["preview"]["mode"] = "auto-publish" if auto_publish else "dry-run"
    result["preview"]["dryRunOnly"] = not auto_publish
    result["diff"]["mode"] = "auto-publish" if auto_publish else "dry-run"
    write_json(preview_path, result["preview"])
    write_json(diff_path, result["diff"])
    if not auto_publish:
        return result
    gate_reasons = production_gate_reasons(policy, environment)
    result["preview"]["productionAutoPublishEnabled"] = not gate_reasons
    result["preview"]["productionGateReasons"] = gate_reasons
    write_json(preview_path, result["preview"])
    if gate_reasons:
        raise PermissionError("Production auto-publish is disabled: " + ", ".join(gate_reasons))
    if result["preview"]["summary"]["batchChangeLimitExceeded"]:
        raise PermissionError("Production auto-publish stopped by the batch change limit")
    if result["diff"]["wouldChangePromotions"]:
        update_menu.validate_promotion_data(result["nextPromotions"])
        write_json(promotions_path, result["nextPromotions"])
        result["diff"]["promotionsWritten"] = True
        write_json(diff_path, result["diff"])
    return result


def validate_preview(path: Path) -> None:
    preview = read_json(path)
    if preview.get("schemaVersion") != 1 or not isinstance(preview.get("decisions"), list):
        raise ValueError("Invalid auto-publish preview schema")
    if preview.get("mode") not in {"dry-run", "auto-publish"}:
        raise ValueError("Invalid preview mode")
    promotions = preview.get("eligiblePromotions")
    if not isinstance(promotions, list):
        raise ValueError("Preview eligiblePromotions must be a list")
    update_menu.validate_promotion_data({"schemaVersion": 1, "promotions": promotions})


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview evidence-gated Instagram promotion additions")
    parser.add_argument("--candidates", type=Path, default=CANDIDATES_PATH, help="Candidate JSON; accepts the local Windows scanner output path")
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--promotions", type=Path, default=PROMOTIONS_PATH)
    parser.add_argument("--menu", type=Path, default=MENU_PATH)
    parser.add_argument("--preview", type=Path, default=PREVIEW_PATH)
    parser.add_argument("--diff", type=Path, default=DIFF_PATH)
    parser.add_argument("--ocr-evidence", type=Path, default=None, help="Optional complete-carousel OCR evidence sidecar")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="Evaluation date in YYYY-MM-DD for deterministic previews")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Write preview only (default)")
    mode.add_argument("--auto-publish", action="store_true", help="Append verified promotions only when every production gate is explicitly enabled")
    parser.add_argument("--check", action="store_true", help="Validate policy, candidate, promotion, OCR and preview inputs without fetching")
    args = parser.parse_args()

    try:
        policy = read_json(args.policy)
        validate_policy(policy)
        candidates = read_json(args.candidates)
        validate_candidate_document(candidates)
        promotions = read_json(args.promotions)
        update_menu.validate_promotion_data(promotions)
        if args.ocr_evidence:
            validate_ocr_document(read_json(args.ocr_evidence))
        if args.check:
            if args.preview.exists():
                validate_preview(args.preview)
            print("Instagram auto-publish inputs validated")
            return 0
        as_of = datetime.combine(args.as_of, datetime.min.time(), tzinfo=detector.HONG_KONG_TZ) if args.as_of else None
        result = run(
            candidates_path=args.candidates,
            policy_path=args.policy,
            promotions_path=args.promotions,
            menu_path=args.menu,
            preview_path=args.preview,
            diff_path=args.diff,
            ocr_path=args.ocr_evidence,
            auto_publish=args.auto_publish,
            as_of=as_of,
        )
    except (OSError, ValueError, PermissionError) as exc:
        print(f"Instagram auto-publish stopped: {exc}", file=sys.stderr)
        return 1
    summary = result["preview"]["summary"]
    print(
        f"Instagram promotion {result['preview']['mode']}: "
        f"{summary['candidateItems']} candidates, {summary['eligibleItems']} eligible, "
        f"{summary['quarantinedItems']} quarantined; preview={args.preview}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
