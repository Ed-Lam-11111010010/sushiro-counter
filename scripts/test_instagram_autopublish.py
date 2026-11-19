import copy
import hashlib
import html
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import detect_instagram_menu as detector
import instagram_autopublish as autopublish
import update_menu


ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 11, 19, 12, 0, tzinfo=detector.HONG_KONG_TZ)
OBSERVED_AT = "2026-11-19T06:00:00+08:00"
URL = "https://www.instagram.com/p/SAFE123/"
CAPTION = "📅販售期間：2026-11-20至2026-11-30 🍣限定壽司 HK$22 全港各分店有售"


def make_fixture(caption=CAPTION, *, post_id="SAFE123", image_url=None):
    url = f"https://www.instagram.com/p/{post_id}/"
    metadata = {
        "og:description": caption,
        "og:title": "Sushiro HK (@sushiro.hk) on Instagram",
        "og:url": url,
        "article:published_time": "2026-11-18T12:00:00+08:00",
    }
    if image_url:
        metadata["og:image"] = image_url
    candidate = detector.make_candidate(url, metadata, today=AS_OF.date())
    candidate["observedAt"] = OBSERVED_AT
    candidate["discoverySources"] = ["instagram-profile"]
    document = {
        "schemaVersion": 1,
        "account": "sushiro.hk",
        "profileFetchStatus": "success",
        "postDiscoveryStatus": "found",
        "sourceStatus": "posts_found",
        "discoveryComplete": True,
        "discoveryCoverage": "public-html-best-effort-not-exhaustive",
        "posts": [candidate],
    }
    page = (
        "<html><head>"
        f'<meta property="og:description" content="{html.escape(caption, quote=True)}">'
        f'<meta property="og:title" content="{html.escape(metadata["og:title"], quote=True)}">'
        f'<meta property="og:url" content="{url}">'
        '<meta property="article:published_time" content="2026-11-18T12:00:00+08:00">'
    )
    if image_url:
        page += f'<meta property="og:image" content="{image_url}">'
    page += "</head><body></body></html>"
    return document, page


def read_base_inputs():
    policy = autopublish.read_json(ROOT / "data" / "instagram_autopublish_policy.json")
    promotions = autopublish.read_json(ROOT / "data" / "promotions.json")
    menu = autopublish.read_json(ROOT / "menu.json")
    return policy, promotions, menu


def evaluate(document, *, policy=None, promotions=None, menu=None, page=None, ocr=None, image_fetcher=None, as_of=AS_OF):
    base_policy, base_promotions, base_menu = read_base_inputs()
    return autopublish.evaluate_candidates(
        document,
        promotions if promotions is not None else base_promotions,
        menu if menu is not None else base_menu,
        policy if policy is not None else base_policy,
        as_of=as_of,
        ocr_document=ocr,
        instagram_fetcher=lambda _url: page if page is not None else "",
        image_fetcher=image_fetcher or (lambda _url: b"unused-image"),
    )


class InstagramAutoPublishTests(unittest.TestCase):
    def test_policy_defaults_to_dry_run_and_requires_independent_gates(self):
        policy, _, _ = read_base_inputs()
        self.assertFalse(policy["productionEnabled"])
        self.assertTrue(policy["killSwitch"])
        self.assertTrue(autopublish.production_gate_reasons(policy, {}))
        opened = copy.deepcopy(policy)
        opened.update({"productionEnabled": True, "killSwitch": False, "approvedPolicyVersion": opened["policyVersion"]})
        environment = {
            "SUSHIRO_INSTAGRAM_AUTOPUBLISH_ENABLED": "true",
            "SUSHIRO_INSTAGRAM_AUTOPUBLISH_KILL_SWITCH": "false",
            "SUSHIRO_INSTAGRAM_AUTOPUBLISH_APPROVED_POLICY_VERSION": opened["policyVersion"],
        }
        self.assertEqual(autopublish.production_gate_reasons(opened, environment), [])

    def test_input_candidates_cannot_bypass_the_review_only_contract(self):
        document, _ = make_fixture()
        tampered = copy.deepcopy(document)
        tampered["posts"][0]["reviewStatus"] = "verified"
        with self.assertRaisesRegex(ValueError, "review-only"):
            autopublish.validate_candidate_document(tampered)
        tampered = copy.deepcopy(document)
        tampered["posts"][0]["items"][0]["verificationStatus"] = "verified"
        with self.assertRaisesRegex(ValueError, "unverified"):
            autopublish.validate_candidate_document(tampered)

    def test_explicit_hkd_item_produces_schema_valid_deterministic_preview(self):
        document, page = make_fixture()
        first = evaluate(document, page=page)
        second = evaluate(document, page=page)
        self.assertEqual(first["preview"], second["preview"])
        self.assertEqual(first["preview"]["summary"]["eligibleItems"], 1)
        self.assertEqual(first["preview"]["summary"]["promotionPreviews"], 1)
        promotion = first["preview"]["eligiblePromotions"][0]
        update_menu.validate_promotion_data({"schemaVersion": 1, "promotions": [promotion]})
        self.assertEqual(promotion["startDate"], "2026-11-20")
        self.assertEqual(promotion["endDate"], "2026-11-30")
        self.assertEqual(promotion["items"][0]["price"], 22)
        self.assertEqual(promotion["items"][0]["verificationStatus"], "verified")
        generated_catalog = {"schemaVersion": 1, "promotions": [promotion]}
        self.assertEqual(update_menu.active_promotion_categories(generated_catalog, date(2026, 11, 19)), {})
        active = update_menu.active_promotion_categories(generated_catalog, date(2026, 11, 20))
        self.assertEqual(active[promotion["category"]][0]["id"], promotion["items"][0]["id"])
        self.assertEqual(active[promotion["category"]][0]["price"], 22)

    def test_dry_run_writes_preview_but_never_changes_promotions(self):
        document, page = make_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            policy = root / "policy.json"
            promotions = root / "promotions.json"
            menu = root / "menu.json"
            preview = root / "preview.json"
            diff = root / "diff.json"
            candidates.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            base_policy, base_promotions, base_menu = read_base_inputs()
            policy.write_text(json.dumps(base_policy), encoding="utf-8")
            promotions.write_text(json.dumps(base_promotions, ensure_ascii=False), encoding="utf-8")
            menu.write_text(json.dumps(base_menu, ensure_ascii=False), encoding="utf-8")
            original = promotions.read_bytes()
            result = autopublish.run(
                candidates_path=candidates,
                policy_path=policy,
                promotions_path=promotions,
                menu_path=menu,
                preview_path=preview,
                diff_path=diff,
                as_of=AS_OF,
                instagram_fetcher=lambda _url: page,
            )
            autopublish.validate_preview(preview)
            self.assertEqual(promotions.read_bytes(), original)
            self.assertTrue(result["diff"]["wouldChangePromotions"])
            self.assertFalse(json.loads(preview.read_text(encoding="utf-8"))["productionAutoPublishEnabled"])

    def test_real_twelve_post_34_candidate_report_never_auto_publishes_ambiguous_dollars(self):
        report = autopublish.read_json(ROOT / "data" / "instagram_scan_report_review2.json")
        self.assertEqual(report["postCount"], 12)
        self.assertEqual(sum(len(post.get("items", [])) for post in report["posts"]), 34)
        result = evaluate(report, page="unused")
        summary = result["preview"]["summary"]
        self.assertEqual(summary["candidateItems"], 34)
        self.assertEqual(summary["eligibleItems"], 0)
        self.assertEqual(summary["promotionPreviews"], 0)
        self.assertEqual(result["preview"]["reasonCounts"].get("ambiguous_price_currency"), 34)
        self.assertTrue(all(post.get("reviewStatus") == "review-only" for post in report["posts"]))

    def test_jjk_item_specific_dates_are_preserved_and_post_10_is_protected(self):
        from test_instagram_menu import JUJUTSU_CAPTION

        document, page = make_fixture(
            JUJUTSU_CAPTION,
            post_id="Dfn7uyiCgAt",
        )
        candidate = document["posts"][0]
        items = {item["name"]: item for item in candidate["items"]}
        self.assertEqual(items["大切鹽味醬漬長鰭吞拿魚"]["startDate"], "2026-11-17")
        self.assertEqual(items["特盛海灣貝海苔包配飛魚子"]["startDate"], "2026-12-01")
        self.assertEqual(items["襟章・第三彈"]["startDate"], "2026-12-07")
        self.assertNotIn("endDate", items["聯乘飲品套裝"])
        result = evaluate(document, page=page)
        self.assertEqual(result["preview"]["summary"]["eligibleItems"], 0)
        reasons = {reason for decision in result["preview"]["decisions"] for reason in decision.get("reasons", [])}
        self.assertIn("protected_pr_10_source_post", reasons)
        drink = next(item for item in result["preview"]["decisions"] if item.get("itemName") == "聯乘飲品套裝")
        self.assertIn("promotion_start_date_missing", drink["reasons"])

    def test_changed_caption_fingerprint_and_unusable_live_metadata_are_quarantined(self):
        document, changed_page = make_fixture(CAPTION.replace("HK$22", "HK$23"))
        original, _ = make_fixture()
        document["posts"] = original["posts"]
        result = evaluate(document, page=changed_page)
        reasons = result["preview"]["decisions"][0]["reasons"]
        self.assertIn("source_fingerprint_changed_since_scan", reasons)
        self.assertEqual(result["preview"]["summary"]["eligibleItems"], 0)

        document, _ = make_fixture()
        login_shell = '<meta property="og:description" content="Log in to Instagram to see photos and videos from friends and discover accounts you will love.">'
        result = evaluate(document, page=login_shell)
        reasons = result["preview"]["decisions"][0]["reasons"]
        self.assertIn("live_source_metadata_unusable", reasons)

    def test_incomplete_scan_missing_checkpoint_or_missing_fingerprint_fails_closed(self):
        cases = []
        document, page = make_fixture()
        incomplete = copy.deepcopy(document)
        incomplete["discoveryComplete"] = False
        incomplete["postDiscoveryStatus"] = "partial"
        cases.append((incomplete, page, "scan_missing_or_incomplete"))
        missing_fingerprint = copy.deepcopy(document)
        missing_fingerprint["posts"][0]["contentFingerprint"] = None
        cases.append((missing_fingerprint, page, "source_fingerprint_missing"))
        missing_observation = copy.deepcopy(document)
        missing_observation["posts"][0].pop("observedAt")
        cases.append((missing_observation, page, "candidate_observation_time_missing"))
        for candidate, source_page, expected in cases:
            with self.subTest(reason=expected):
                result = evaluate(candidate, page=source_page)
                self.assertEqual(result["preview"]["summary"]["eligibleItems"], 0)
                self.assertIn(expected, result["preview"]["decisions"][0]["reasons"])

        same_day_future, _ = make_fixture()
        same_day_future["posts"][0]["observedAt"] = "2026-11-19T23:55:00+08:00"
        future_result = evaluate(same_day_future, page="unused")
        self.assertIn("candidate_observation_time_in_future", future_result["preview"]["decisions"][0]["reasons"])

    def test_expired_items_reject_but_future_promotions_keep_their_start_date(self):
        document, page = make_fixture()
        future = evaluate(document, page=page)
        self.assertEqual(future["preview"]["eligiblePromotions"][0]["startDate"], "2026-11-20")
        expired_document, expired_page = make_fixture(
            "📅販售期間：2026-11-01至2026-11-18 🍣限定壽司 HK$22 全港各分店有售",
            post_id="EXPIRED1",
        )
        expired = evaluate(expired_document, page=expired_page)
        self.assertEqual(expired["preview"]["summary"]["eligibleItems"], 0)
        self.assertIn("promotion_expired", expired["preview"]["decisions"][0]["reasons"])

        open_ended, open_page = make_fixture(
            "📅販售期間：2026-11-20 🍣限定壽司 HK$22 全港各分店有售",
            post_id="OPENEND1",
        )
        unknown_stock = evaluate(open_ended, page=open_page)
        self.assertIn("stock_or_end_condition_unknown", unknown_stock["preview"]["decisions"][0]["reasons"])

    def test_missing_hkd_price_and_spend_threshold_names_are_not_eligible(self):
        document, page = make_fixture()
        item = document["posts"][0]["items"][0]
        for field in ("price", "currency", "priceConfidence", "priceSource"):
            item.pop(field, None)
        missing_price = evaluate(document, page=page)
        self.assertEqual(missing_price["preview"]["summary"]["eligibleItems"], 0)
        self.assertIn("price_missing_or_not_positive", missing_price["preview"]["decisions"][0]["reasons"])

        threshold, threshold_page = make_fixture(
            "📅販售期間：2026-11-20至2026-11-30 🍣消費滿200即可加購 HK$22 全港各分店有售",
            post_id="THRESHOLD1",
        )
        if not threshold["posts"][0]["items"]:
            # The detector intentionally omits a threshold as a menu item. Keep
            # one review fixture to prove a mistakenly supplied row is blocked.
            threshold["posts"][0]["items"].append({
                "id": "threshold-row",
                "name": "消費滿200即可加購",
                "itemType": "dish",
                "purchaseCondition": "conditional-add-on",
                "price": 22,
                "currency": "HKD",
                "priceConfidence": "explicit-hkd",
                "priceEvidenceSource": "caption",
                "priceEvidenceQuote": "HK$22",
                "priceText": "HK$22",
                "startDate": "2026-11-20",
                "endDate": "2026-11-30",
                "dateConfidence": "explicit-section",
                "dateEvidenceText": "2026-11-20至2026-11-30",
                "availabilityEvidenceText": "2026-11-20至2026-11-30",
                "branchScope": "all-hk",
                "branchEvidenceText": "全港各分店有售",
            })
        blocked = evaluate(threshold, page=threshold_page)
        reasons = blocked["preview"]["decisions"][0]["reasons"]
        self.assertIn("bundle_threshold_or_gift_not_a_standalone_menu_item", reasons)

    def test_merchandise_add_ons_thresholds_and_branch_restrictions_are_quarantined(self):
        for caption, expected in (
            ("📅販售期間：2026-11-20至2026-11-30 🍣亞加力立牌 HK$22 全港各分店有售", "merchandise_or_unsupported_item_type"),
            ("📅販售期間：2026-11-20至2026-11-30 🍣限定壽司 HK$22 只限指定分店", "branch_scope_unknown_or_restricted"),
        ):
            document, page = make_fixture(caption, post_id="RESTRICT1")
            result = evaluate(document, page=page)
            self.assertIn(expected, result["preview"]["decisions"][0]["reasons"])

        document, page = make_fixture()
        document["posts"][0]["items"][0]["purchaseCondition"] = "conditional-add-on"
        result = evaluate(document, page=page)
        self.assertIn("conditional_add_on_or_unknown_purchase_condition", result["preview"]["decisions"][0]["reasons"])

    def test_item_classification_and_branch_claims_must_match_live_caption(self):
        merchandise, merchandise_page = make_fixture(
            "📅販售期間：2026-11-20至2026-11-30 🍣亞加力立牌加購 HK$22 全港各分店有售",
            post_id="TAMPER01",
        )
        item = merchandise["posts"][0]["items"][0]
        item["itemType"] = "dish"
        item["purchaseCondition"] = "standard"
        result = evaluate(merchandise, page=merchandise_page)
        reasons = result["preview"]["decisions"][0]["reasons"]
        self.assertIn("live_item_type_mismatch", reasons)
        self.assertIn("live_purchase_condition_mismatch", reasons)

        no_scope, no_scope_page = make_fixture(
            "📅販售期間：2026-11-20至2026-11-30 🍣限定壽司 HK$22",
            post_id="TAMPER02",
        )
        no_scope_item = no_scope["posts"][0]["items"][0]
        no_scope_item["branchScope"] = "all-hk"
        no_scope_item["branchEvidenceText"] = "全港各分店有售"
        branch_result = evaluate(no_scope, page=no_scope_page)
        self.assertIn("live_branch_scope_mismatch", branch_result["preview"]["decisions"][0]["reasons"])

    def test_verified_catalog_duplicates_price_conflicts_and_id_collisions_are_quarantined(self):
        document, page = make_fixture()
        policy, promotions, menu = read_base_inputs()
        menu["categories"]["test"] = [{"id": "existing", "name": "限定壽司", "price": 22}]
        duplicate = evaluate(document, policy=policy, promotions=promotions, menu=menu, page=page)
        self.assertIn("duplicate_verified_item", duplicate["preview"]["decisions"][0]["reasons"])

        conflicting_menu = read_base_inputs()[2]
        conflicting_menu["categories"]["test"] = [{"id": "existing", "name": "限定壽司", "price": 21}]
        price_conflict = evaluate(document, policy=policy, promotions=promotions, menu=conflicting_menu, page=page)
        self.assertIn("verified_price_conflict", price_conflict["preview"]["decisions"][0]["reasons"])

        id_collision_menu = read_base_inputs()[2]
        id_collision_menu["categories"]["test"] = [{"id": "instagram_safe123_08ca7e", "name": "其他菜式", "price": 18}]
        # Use a known candidate ID to exercise the catalog ID collision path.
        document_with_collision, collision_page = make_fixture()
        document_with_collision["posts"][0]["items"][0]["id"] = "existing-collision"
        id_collision_menu["categories"]["test"][0]["id"] = "existing-collision"
        id_collision = evaluate(document_with_collision, policy=policy, promotions=promotions, menu=id_collision_menu, page=collision_page)
        self.assertIn("menu_item_id_collision", id_collision["preview"]["decisions"][0]["reasons"])

        policy["protectedPromotionIds"] = ["instagram-safe123-2026-11-20"]
        protected = evaluate(document, policy=policy, promotions=promotions, menu=read_base_inputs()[2], page=page)
        self.assertIn("protected_promotion_id_collision", protected["preview"]["decisions"][0]["reasons"])
        self.assertEqual(protected["preview"]["eligiblePromotions"], [])

        # Repeating an already generated update sees the stable item ID in the
        # verified catalog and cannot append a second copy.
        clean_policy, base_promotions, base_menu = read_base_inputs()
        first = evaluate(document, policy=clean_policy, promotions=base_promotions, menu=base_menu, page=page)
        updated = first["nextPromotions"]
        second = evaluate(document, policy=clean_policy, promotions=updated, menu=base_menu, page=page)
        self.assertEqual(first["preview"]["summary"]["promotionPreviews"], 1)
        self.assertEqual(second["preview"]["summary"]["promotionPreviews"], 0)
        self.assertEqual(len(second["nextPromotions"]["promotions"]), len(updated["promotions"]))

    def test_ocr_requires_complete_provenance_and_conflicting_caption_price_is_rejected(self):
        image = "https://cdn.example.test/menu-slide.jpg"
        caption = "📅販售期間：2026-11-20至2026-11-30 🍣限定壽司 全港各分店有售"
        document, page = make_fixture(caption, post_id="OCRSAFE1", image_url=image)
        post = document["posts"][0]
        sha = hashlib.sha256(b"source-image").hexdigest()
        ocr = {
            "schemaVersion": 1,
            "posts": [{
                "postId": "OCRSAFE1",
                "provider": "test-ocr-v1",
                "fingerprint": post["contentFingerprint"],
                "coverageComplete": True,
                "images": [{"url": image, "status": "complete", "confidence": 0.999, "sha256": sha}],
                "items": [{
                    "name": "限定壽司",
                    "itemType": "dish",
                    "purchaseCondition": "standard",
                    "confidence": 0.999,
                    "nameEvidenceText": "限定壽司",
                    "price": 22,
                    "priceEvidenceText": "HK$22",
                    "imageUrl": image,
                    "imageSha256": sha,
                    "startDate": "2026-11-20",
                    "endDate": "2026-11-30",
                    "dateConfidence": "high-confidence-ocr",
                    "dateEvidenceText": "2026-11-20至2026-11-30",
                    "branchScope": "all-hk",
                    "branchEvidenceText": "全港各分店有售",
                }],
            }],
        }
        trusted_policy = read_base_inputs()[0]
        trusted_policy["trustedOcrProviders"] = ["test-ocr-v1"]
        default_policy_result = evaluate(document, page=page, ocr=ocr, image_fetcher=lambda _url: b"source-image")
        self.assertIn("ocr_provider_not_trusted", default_policy_result["preview"]["decisions"][0]["reasons"])
        accepted = evaluate(document, policy=trusted_policy, page=page, ocr=ocr, image_fetcher=lambda _url: b"source-image")
        self.assertEqual(accepted["preview"]["summary"]["eligibleItems"], 1)

        bad_ocr = copy.deepcopy(ocr)
        bad_ocr["posts"][0]["items"][0]["imageSha256"] = "b" * 64
        rejected = evaluate(document, policy=trusted_policy, page=page, ocr=bad_ocr, image_fetcher=lambda _url: b"source-image")
        self.assertIn("ocr_item_image_provenance_mismatch", rejected["preview"]["decisions"][0]["reasons"])

        source_hash_mismatch = copy.deepcopy(ocr)
        source_hash_mismatch["posts"][0]["images"][0]["sha256"] = "b" * 64
        source_hash_mismatch["posts"][0]["items"][0]["imageSha256"] = "b" * 64
        mismatch = evaluate(document, policy=trusted_policy, page=page, ocr=source_hash_mismatch, image_fetcher=lambda _url: b"source-image")
        self.assertIn("ocr_source_image_hash_mismatch", mismatch["preview"]["decisions"][0]["reasons"])

        conflict_document, conflict_page = make_fixture(
            "📅販售期間：2026-11-20至2026-11-30 🍣限定壽司 HK$24 全港各分店有售",
            post_id="OCRSAFE2",
            image_url=image,
        )
        conflict_ocr = copy.deepcopy(ocr)
        conflict_ocr["posts"][0]["postId"] = "OCRSAFE2"
        conflict_ocr["posts"][0]["fingerprint"] = conflict_document["posts"][0]["contentFingerprint"]
        conflict = evaluate(conflict_document, policy=trusted_policy, page=conflict_page, ocr=conflict_ocr, image_fetcher=lambda _url: b"source-image")
        self.assertIn("caption_ocr_price_conflict", conflict["preview"]["decisions"][0]["reasons"])

    def test_public_ocr_image_fetch_rejects_non_instagram_hosts(self):
        with self.assertRaisesRegex(ValueError, "allowlist"):
            autopublish.fetch_public_image_bytes("https://example.test/not-a-public-instagram-image.jpg")

    def test_stale_evidence_and_feature_disabled_auto_publish_do_not_write_promotions(self):
        document, page = make_fixture()
        stale = copy.deepcopy(document)
        stale["posts"][0]["observedAt"] = "2026-11-17T06:00:00+08:00"
        result = evaluate(stale, page=page)
        self.assertIn("candidate_evidence_stale", result["preview"]["decisions"][0]["reasons"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy, promotions, menu = read_base_inputs()
            paths = {
                "candidates_path": root / "candidates.json",
                "policy_path": root / "policy.json",
                "promotions_path": root / "promotions.json",
                "menu_path": root / "menu.json",
                "preview_path": root / "preview.json",
                "diff_path": root / "diff.json",
            }
            paths["candidates_path"].write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            paths["policy_path"].write_text(json.dumps(policy), encoding="utf-8")
            paths["promotions_path"].write_text(json.dumps(promotions, ensure_ascii=False), encoding="utf-8")
            paths["menu_path"].write_text(json.dumps(menu, ensure_ascii=False), encoding="utf-8")
            original = paths["promotions_path"].read_bytes()
            with self.assertRaises(PermissionError):
                autopublish.run(
                    **paths,
                    auto_publish=True,
                    as_of=AS_OF,
                    instagram_fetcher=lambda _url: page,
                    environment={
                        "SUSHIRO_INSTAGRAM_AUTOPUBLISH_ENABLED": "true",
                        "SUSHIRO_INSTAGRAM_AUTOPUBLISH_KILL_SWITCH": "false",
                        "SUSHIRO_INSTAGRAM_AUTOPUBLISH_APPROVED_POLICY_VERSION": policy["policyVersion"],
                    },
                )
            self.assertEqual(paths["promotions_path"].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
