import json
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import detect_instagram_menu as detector


POST_URL = "https://www.instagram.com/p/ABC123/"
JUJUTSU_CAPTION = (
    "大切鹽味醬漬長鰭吞拿魚｜$17 附送插牌 📅販售期間：11月17日起～售完即止 "
    "特盛海灣貝海苔包配飛魚子｜$17 附送插牌 📅販售期間：12月1日起～售完即止 "
    "黑芝麻千層蛋糕｜$38 聯乘飲品套裝｜$59 亞加力場景立牌｜$129 "
    "襟章・第一彈｜$39（共5款，款式隨機） 📅販售期間：11月17日～售完即止 "
    "襟章・第二彈｜$39（共8款，款式隨機） 📅販售期間：12月1日～售完即止 "
    "襟章・第三彈｜$39（共2款，款式隨機） 📅販售期間：12月7日～售完即止"
)


class InstagramParserTests(unittest.TestCase):
    def test_owner_prefixed_public_profile_anchor_is_canonicalized(self):
        page = '<a href="/sushiro.hk/p/Dfn7uyiCgAt/">public post</a>'
        parsed = detector.parse_public_page(page, detector.PROFILE_URL)
        self.assertEqual(parsed["postUrls"], ["https://www.instagram.com/p/Dfn7uyiCgAt/"])

    def test_public_html_finds_post_links_and_images(self):
        page = """
        <html><head>
          <meta property="og:description" content="京都吞拿魚 HK$32" />
          <meta property="article:published_time" content="2026-11-17T01:00:00+08:00" />
        </head><body>
          <a href="/p/ABC123/?img_index=1">post</a>
          <a href="https://www.instagram.com/p/ABC123/">duplicate</a>
          <img src="/images/menu.jpg" />
        </body></html>
        """
        parsed = detector.parse_public_page(page, detector.PROFILE_URL)
        self.assertEqual(parsed["postUrls"], [POST_URL])
        self.assertEqual(parsed["imageUrls"], ["https://www.instagram.com/images/menu.jpg"])
        self.assertEqual(parsed["metadata"]["og:description"], "京都吞拿魚 HK$32")

    def test_post_html_pipeline_extracts_caption_and_exposed_carousel_urls(self):
        page = """
        <html><head>
          <meta property="og:description" content="第一款 HK$18 第二款 HKD 22" />
          <meta property="og:image" content="https://cdn.example.test/cover.jpg" />
        </head><body>
          <img src="https://cdn.example.test/slide-1.jpg" />
          <img src="https://cdn.example.test/slide-2.jpg" />
        </body></html>
        """
        parsed = detector.parse_public_page(page, POST_URL)
        candidate = detector.make_candidate(
            POST_URL,
            parsed["metadata"],
            parsed["imageUrls"],
            today=date(2026, 11, 17),
        )
        self.assertEqual([(item["name"], item["price"]) for item in candidate["items"]], [("第一款", 18), ("第二款", 22)])
        self.assertEqual(candidate["imageUrls"], [
            "https://cdn.example.test/cover.jpg",
            "https://cdn.example.test/slide-1.jpg",
            "https://cdn.example.test/slide-2.jpg",
        ])
        self.assertTrue(any("not OCRed" in reason for reason in candidate["reviewReasons"]))

    def test_caption_explicit_hkd_prices_are_extracted_with_stable_ids(self):
        metadata = {"og:description": "京都吞拿魚 HK$32 第二款海膽 HKD 25"}
        first = detector.make_candidate(POST_URL, metadata, today=date(2026, 11, 17))
        second = detector.make_candidate(POST_URL, metadata, today=date(2026, 11, 17))
        self.assertEqual(first["status"], "found")
        self.assertEqual([(item["name"], item["price"]) for item in first["items"]], [("京都吞拿魚", 32), ("第二款海膽", 25)])
        self.assertEqual([item["id"] for item in first["items"]], [item["id"] for item in second["items"]])
        self.assertTrue(first["requiresReview"])

    def test_ambiguous_price_is_not_imported_as_zero_or_hkd(self):
        candidate = detector.make_candidate(
            POST_URL,
            {"og:description": "新菜式 $32"},
            today=date(2026, 11, 17),
        )
        self.assertEqual(candidate["status"], "needs_review")
        self.assertEqual(candidate["items"][0]["name"], "新菜式")
        self.assertNotIn("price", candidate["items"][0])
        self.assertEqual(candidate["items"][0]["priceText"], "$32")
        self.assertTrue(any("numeric value was not imported" in reason for reason in candidate["reviewReasons"]))

    def test_future_and_expired_date_windows_are_explicit(self):
        future = detector.make_candidate(
            POST_URL,
            {"og:description": "新菜式 HK$22 2026年12月1日至2026年12月31日"},
            today=date(2026, 11, 17),
        )
        expired = detector.make_candidate(
            "https://www.instagram.com/p/OLD123/",
            {"og:description": "舊菜式 HK$22 2026年10月1日至2026年10月31日"},
            today=date(2026, 11, 17),
        )
        self.assertEqual(future["status"], "future")
        self.assertEqual(future["startDate"], "2026-12-01")
        self.assertEqual(future["endDate"], "2026-12-31")
        self.assertEqual(expired["status"], "expired")

        cross_year = detector.make_candidate(
            "https://www.instagram.com/p/CROSSYEAR/",
            {"og:description": "跨年限定 HK$22 12月24日至1月3日"},
            today=date(2026, 12, 1),
        )
        self.assertEqual(cross_year["startDate"], "2026-12-24")
        self.assertEqual(cross_year["endDate"], "2027-01-03")
        self.assertEqual(cross_year["status"], "future")

        repeated = detector.make_candidate(
            "https://www.instagram.com/p/REPEATED/",
            {"og:description": "活動 HK$22 11月17日至12月1日，另於11月17日及12月1日提醒"},
            today=date(2026, 11, 1),
        )
        self.assertEqual(repeated["startDate"], "2026-11-17")
        self.assertEqual(repeated["endDate"], "2026-12-01")

    def test_caption_items_are_classified_without_importing_ambiguous_prices(self):
        candidate = detector.make_candidate(
            POST_URL,
            {"og:description": "壽司 HK$22 飲品套裝 HK$59 亞加力場景立牌 HK$129 凡於堂食消費滿$200即可加購"},
            today=date(2026, 11, 17),
        )
        by_name = {item["name"]: item for item in candidate["items"]}
        self.assertEqual(by_name["壽司"]["itemType"], "dish")
        self.assertEqual(by_name["飲品套裝"]["itemType"], "drink")
        self.assertEqual(by_name["亞加力場景立牌"]["itemType"], "merchandise")
        self.assertEqual(by_name["亞加力場景立牌"]["purchaseCondition"], "conditional-add-on")
        self.assertEqual(by_name["亞加力場景立牌"]["price"], 129)

        steamed_egg = detector.make_candidate(
            POST_URL,
            {"og:description": "🍄菇菇羹茶碗蒸｜HK$22"},
            today=date(2026, 11, 17),
        )
        self.assertEqual(steamed_egg["items"][0]["name"], "菇菇羹茶碗蒸")
        self.assertEqual(steamed_egg["items"][0]["itemType"], "dish")

        existing = detector.make_candidate(POST_URL, {"og:description": "玉子燒 HK$12"}, today=date(2026, 11, 17))
        detector.annotate_existing_menu_matches(existing, {"玉子燒": "tamago"})
        self.assertEqual(existing["items"][0]["existingMenuId"], "tamago")

    def test_known_post_fixture_is_extracted_from_public_post_html(self):
        post_url = "https://www.instagram.com/p/Dfn7uyiCgAt/"
        page = '<meta property="og:description" content="新聯乘商品 ｜$17 2026年11月17日起～售完即止" />'
        parsed = detector.parse_public_page(page, post_url)
        candidate = detector.make_candidate(post_url, parsed["metadata"], today=date(2026, 11, 17))
        self.assertEqual(candidate["postId"], "Dfn7uyiCgAt")
        self.assertEqual(candidate["items"][0]["priceText"], "$17")
        self.assertNotIn("price", candidate["items"][0])
        self.assertEqual(candidate["supplyCondition"], "until-sold-out")

    def test_jjk_mixed_promotion_dates_stay_on_their_items(self):
        candidate = detector.make_candidate(
            "https://www.instagram.com/p/Dfn7uyiCgAt/",
            {"og:description": JUJUTSU_CAPTION},
            manual_post={"publishedAt": "2026-11-11", "caption": JUJUTSU_CAPTION},
            today=date(2026, 11, 17),
        )
        by_name = {item["name"]: item for item in candidate["items"]}
        self.assertEqual(candidate["dateScope"], "item-specific")
        self.assertIsNone(candidate["startDate"])
        self.assertIsNone(candidate["endDate"])
        self.assertEqual(by_name["大切鹽味醬漬長鰭吞拿魚"]["startDate"], "2026-11-17")
        self.assertEqual(by_name["特盛海灣貝海苔包配飛魚子"]["startDate"], "2026-12-01")
        self.assertEqual(by_name["襟章・第一彈"]["startDate"], "2026-11-17")
        self.assertEqual(by_name["襟章・第二彈"]["startDate"], "2026-12-01")
        self.assertEqual(by_name["襟章・第三彈"]["startDate"], "2026-12-07")
        self.assertTrue(all(item.get("supplyCondition") == "until-sold-out" for item in by_name.values() if item.get("startDate")))
        self.assertNotIn("2027", json.dumps(candidate, ensure_ascii=False))

        drink_package = by_name["聯乘飲品套裝"]
        self.assertEqual(drink_package["itemType"], "drink")
        self.assertIsNone(drink_package.get("startDate"))
        self.assertIsNone(drink_package.get("endDate"))
        self.assertEqual(drink_package["dateConfidence"], "not-stated")
        self.assertTrue(drink_package["dateReviewRequired"])

    def test_dfpq_caption_keeps_salmon_period_with_its_promotion_section(self):
        caption = (
            "🌱加上山葵莖Wasabi｜$6 粒粒口感，讓滋味升級！ "
            "🔸【京都伊根生黑鮪吞拿魚】限定商品11月3日（二）起，於全線分店有售。 "
            "🎬同場加映！美味壽司月中繼續有～ "
            "📅販售期間：11月24日〜售完即止 "
            "🐂鹽燒黑毛和牛肩胛小排｜$27 "
            "🐟喜之次雙味（炙燒・醬烤）｜$27 "
            "📅販售期間：11月24日〜12月1日 "
            "🐟生三文魚配蟹柳沙律｜$22"
        )
        candidate = detector.make_candidate(
            "https://www.instagram.com/p/DfPQcXcCZCx/",
            {"og:description": caption},
            manual_post={"publishedAt": "2026-11-02", "caption": caption},
            today=date(2026, 11, 17),
        )
        by_name = {item["name"]: item for item in candidate["items"]}
        self.assertEqual(by_name["生三文魚配蟹柳沙律"]["startDate"], "2026-11-24")
        self.assertEqual(by_name["生三文魚配蟹柳沙律"]["endDate"], "2026-12-01")
        self.assertNotIn("supplyCondition", by_name["生三文魚配蟹柳沙律"])
        self.assertNotIn("startDate", by_name["加上山葵莖Wasabi"])
        self.assertEqual(by_name["鹽燒黑毛和牛肩胛小排"]["startDate"], "2026-11-24")
        self.assertEqual(by_name["鹽燒黑毛和牛肩胛小排"]["supplyCondition"], "until-sold-out")

    def test_punctuation_only_and_marketing_paragraph_names_are_rejected(self):
        for caption in ("→ HK$22", "！HK$22", "推薦大家今天來香港旅遊並享受超級美味的限定商品！ HK$22"):
            with self.subTest(caption=caption):
                candidate = detector.make_candidate(
                    POST_URL,
                    {"og:description": caption},
                    today=date(2026, 11, 17),
                )
                self.assertEqual(candidate["items"], [])
                self.assertTrue(any("reliable item name" in reason for reason in candidate["reviewReasons"]))

    def test_manual_carousel_items_are_supported_but_remain_review_only(self):
        candidate = detector.make_candidate(
            POST_URL,
            manual_post={
                "caption": "官方貼文，多張圖片",
                "imageUrls": ["https://www.instagram.com/image-1.jpg", "https://www.instagram.com/image-2.jpg"],
                "items": [
                    {"name": "第一款", "price": 18},
                    {"name": "第一款", "price": 18},
                    {"name": "第二款"},
                ],
            },
            today=date(2026, 11, 17),
        )
        self.assertEqual(len(candidate["items"]), 2)
        self.assertTrue(all("price" in item or "priceSource" in item for item in candidate["items"]))
        self.assertTrue(candidate["requiresReview"])
        self.assertEqual(len(candidate["imageUrls"]), 2)
        self.assertEqual(candidate["status"], "needs_review")


class InstagramSafetyTests(unittest.TestCase):
    def test_unavailable_source_preserves_existing_candidates(self):
        existing = detector.make_candidate(
            POST_URL,
            {"og:description": "已知菜式 HK$18"},
            today=date(2026, 11, 17),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "instagram_candidates.json"
            output.write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "account": "sushiro.hk",
                    "profileUrl": detector.PROFILE_URL,
                    "sourceMethod": "public-html-best-effort",
                    "profileFetchStatus": "success",
                    "postDiscoveryStatus": "found",
                    "sourceStatus": "posts_found",
                    "requiresReview": True,
                    "limitations": [],
                    "posts": [existing],
                }),
                encoding="utf-8",
            )

            def unavailable(_url):
                raise RuntimeError("blocked")

            report, changed = detector.detect(
                output_path=output,
                sources_path=Path(directory) / "missing-sources.json",
                fetcher=unavailable,
                today=date(2026, 11, 17),
            )

        # A source outage changes diagnostics, not the preserved candidate posts;
        # it must not create a status-only review PR.
        self.assertFalse(changed)
        self.assertEqual(report["sourceStatus"], "unavailable")
        self.assertEqual(report["profileFetchStatus"], "unavailable")
        self.assertEqual(report["postDiscoveryStatus"], "unavailable")
        self.assertEqual(report["posts"][0]["url"], POST_URL)
        self.assertEqual(report["posts"][0]["items"][0]["price"], 18)

    def test_detect_deduplicates_profile_links_and_keeps_menu_data_separate(self):
        profile = '<a href="/p/ABC123/">one</a><a href="/p/ABC123/?img_index=2">two</a>'
        post = '<meta property="og:description" content="新菜式 HK$22" />'

        def fake_fetch(url):
            if url == detector.PROFILE_URL:
                return profile
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            return post

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "instagram_candidates.json"
            sources = Path(directory) / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            report, _ = detector.detect(
                output_path=output,
                sources_path=sources,
                fetcher=fake_fetch,
                today=date(2026, 11, 17),
            )

        self.assertEqual(len(report["posts"]), 1)
        self.assertEqual(report["sourceStatus"], "posts_found")
        self.assertEqual(report["postDiscoveryStatus"], "found")
        self.assertEqual(report["posts"][0]["items"][0]["price"], 22)
        self.assertNotIn("categories", report)

    def test_auto_profile_discovery_is_distinct_from_manual_url_retrieval(self):
        profile = '<a href="/sushiro.hk/p/AUTO123/">one</a>'
        post = '<meta property="og:description" content="自動發現 HK$22" />'

        def fake_fetch(url):
            if url == detector.PROFILE_URL:
                return profile
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            return post

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            sources = root / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            report, _ = detector.detect(
                output_path=output,
                state_path=state,
                sources_path=sources,
                fetcher=fake_fetch,
                today=date(2026, 11, 17),
                now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))),
            )
            saved_state = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(report["postDiscoveryStatus"], "found")
        self.assertEqual(report["posts"][0]["postId"], "AUTO123")
        self.assertEqual(saved_state["lastAttemptAt"], "2026-11-17T06:00:00+08:00")
        self.assertEqual(saved_state["lastSuccessfulScanAt"], "2026-11-17T06:00:00+08:00")
        self.assertIn("AUTO123", saved_state["discoveredPosts"])

    def test_http_200_unusable_post_does_not_advance_success_checkpoint(self):
        profile = '<a href="/sushiro.hk/p/EMPTYMETA/">one</a>'
        login_shell = (
            '<html><head><meta property="og:description" '
            'content="Log in to Instagram to see photos and videos from friends and discover accounts you will love." />'
            '</head><body>login shell</body></html>'
        )
        parsed_login_shell = detector.parse_public_page(login_shell, "https://www.instagram.com/p/EMPTYMETA/")
        self.assertFalse(detector.has_usable_post_metadata("https://www.instagram.com/p/EMPTYMETA/", parsed_login_shell))

        def unusable_fetch(url):
            return profile if url == detector.PROFILE_URL else login_shell

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            sources = root / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            report, _ = detector.detect(
                output_path=output,
                state_path=state,
                sources_path=sources,
                fetcher=unusable_fetch,
                today=date(2026, 11, 17),
                now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))),
            )
            saved_state = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(report["postDiscoveryStatus"], "partial")
        self.assertIsNone(saved_state["lastSuccessfulScanAt"])
        self.assertEqual(saved_state["lastAttemptAt"], "2026-11-17T06:00:00+08:00")
        self.assertEqual(report["posts"][0]["status"], "unavailable")
        self.assertTrue(any("unusable public metadata" in reason for reason in report["limitations"]))

    def test_over_max_profile_links_are_stably_ordered_and_marked_partial(self):
        links = ''.join(f'<a href="/sushiro.hk/p/POST{index:02d}/">post</a>' for index in (3, 1, 2))

        def fake_fetch(url):
            if url == detector.PROFILE_URL:
                return links
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            return '<meta property="og:description" content="菜式 HK$22" />'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            sources = root / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            with mock.patch.object(detector, "MAX_DISCOVERED_POSTS", 2):
                report, _ = detector.detect(
                    output_path=output,
                    state_path=state,
                    sources_path=sources,
                    fetcher=fake_fetch,
                    today=date(2026, 11, 17),
                    now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))),
                )
            saved_state = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual([post["postId"] for post in report["posts"]], ["POST01", "POST02"])
        self.assertEqual(report["sourceStatus"], "partial")
        self.assertEqual(report["postDiscoveryStatus"], "partial")
        self.assertIsNone(saved_state["lastSuccessfulScanAt"])
        self.assertTrue(any("3 post links" in limitation and "stable canonical URL order" in limitation for limitation in report["limitations"]))

    def test_machine_report_is_compact_and_contains_review_fields(self):
        candidate = detector.make_candidate(
            "https://www.instagram.com/p/REPORT123/",
            {"og:description": "報告菜式 HK$22"},
            today=date(2026, 11, 17),
        )
        report = {
            "schemaVersion": 1,
            "account": "sushiro.hk",
            "profileUrl": detector.PROFILE_URL,
            "sourceMethod": "public-html-best-effort",
            "profileFetchStatus": "success",
            "postDiscoveryStatus": "found",
            "sourceStatus": "posts_found",
            "limitations": [],
            "posts": [candidate],
        }
        compact = detector.machine_readable_report(report, "2026-11-17T06:00:00+08:00")
        item = compact["posts"][0]["items"][0]
        self.assertEqual(compact["postCount"], 1)
        self.assertEqual(compact["posts"][0]["postId"], "REPORT123")
        self.assertEqual(compact["posts"][0]["reviewStatus"], "review-only")
        self.assertEqual(item["priceConfidence"], "explicit-hkd")
        self.assertNotIn("caption", compact["posts"][0])

    def test_public_search_rss_discovers_only_direct_instagram_links(self):
        feed = """<rss version="2.0"><channel>
            <item><link>https://www.instagram.com/p/INDEX123/</link></item>
            <item><link>https://www.instagram.com/sushiro.hk/p/INDEX123/</link></item>
            <item><link>https://example.com/p/UNRELATED/</link></item>
            <item><link>https://www.bing.com/ck/a?redirect=1</link></item>
        </channel></rss>"""
        self.assertEqual(
            detector.discover_public_index_posts(feed),
            ["https://www.instagram.com/p/INDEX123/"],
        )
        with self.assertRaisesRegex(ValueError, "RSS"):
            detector.discover_public_index_posts("<html>not RSS</html>")

    def test_public_official_site_fallback_when_profile_has_no_posts(self):
        official = '<a href="https://www.instagram.com/sushiro.hk/p/OFCL123/">announcement</a>'
        post = '<meta property="og:description" content="新限定壽司 HK$22" />'

        def fetch(url):
            if url == detector.PROFILE_URL:
                return "<html><body>No exposed Instagram post links</body></html>"
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return official
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            if url == "https://www.instagram.com/p/OFCL123/":
                return post
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            report, changed = detector.detect(
                output_path=output,
                state_path=state,
                sources_path=root / "missing-sources.json",
                fetcher=fetch,
                today=date(2026, 11, 17),
                now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))),
            )
            checkpoint = json.loads(state.read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertEqual(report["postDiscoveryStatus"], "partial")
        self.assertEqual(report["posts"][0]["postId"], "OFCL123")
        self.assertEqual(report["discoverySourceCounts"]["officialWebsiteLinks"], 1)
        self.assertEqual(report["discoverySourceCounts"]["instagramProfileAnchors"], 0)
        self.assertTrue(report["posts"][0]["requiresReview"])
        self.assertEqual(report["posts"][0]["items"][0]["price"], 22)
        self.assertIsNone(checkpoint["lastSuccessfulScanAt"])

    def test_search_index_requires_independent_post_owner_evidence(self):
        feed = (
            "<rss><channel><item>"
            "<link>https://www.instagram.com/p/INDEX123/</link>"
            "</item></channel></rss>"
        )
        owner_post = '<meta property="og:description" content="Sushiro HK (@sushiro.hk) on Instagram: 新限定 HK$22" />'
        other_post = '<meta property="og:description" content="Other business on Instagram: 新限定 HK$22" />'

        for post_html, expected_posts in ((owner_post, 1), (other_post, 0)):
            def fetch(url):
                if url == detector.PROFILE_URL:
                    return "<html>Profile has no post anchors</html>"
                if url == detector.OFFICIAL_DISCOVERY_URL:
                    return "<html>No official post anchors</html>"
                if url == detector.PUBLIC_INDEX_FEED_URL:
                    return feed
                return post_html

            with self.subTest(expected_posts=expected_posts), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "instagram_candidates.json"
                report, changed = detector.detect(
                    output_path=output,
                    state_path=root / "instagram_scan_state.json",
                    sources_path=root / "missing-sources.json",
                    fetcher=fetch,
                    today=date(2026, 11, 17),
                )
                self.assertEqual(len(report["posts"]), expected_posts)
                self.assertEqual(changed, bool(expected_posts))
                self.assertEqual(output.exists(), bool(expected_posts))
                self.assertEqual(report["postDiscoveryStatus"], "partial")
                self.assertEqual(report["discoverySourceCounts"]["publicSearchRssLinks"], 1)

    def test_empty_discovery_does_not_open_status_only_candidate_change(self):
        def fetch(url):
            if url == detector.PROFILE_URL:
                return "<html><body>Account page without links</body></html>"
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>Official site without IG links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            existing = {
                "schemaVersion": 1,
                "account": "sushiro.hk",
                "profileFetchStatus": "success",
                "postDiscoveryStatus": "none",
                "sourceStatus": "profile_only",
                "posts": [],
            }
            original = json.dumps(existing)
            output.write_text(original, encoding="utf-8")
            report, changed = detector.detect(
                output_path=output,
                state_path=root / "instagram_scan_state.json",
                sources_path=root / "missing-sources.json",
                fetcher=fetch,
                today=date(2026, 11, 17),
            )
            self.assertEqual(output.read_text(encoding="utf-8"), original)

        self.assertFalse(changed)
        self.assertEqual(report["posts"], [])
        self.assertEqual(report["postDiscoveryStatus"], "none")

    def test_fallback_source_failure_is_distinct_from_successful_empty_scan(self):
        profile = '<a href="/sushiro.hk/p/PARTIAL123/">post</a>'

        def fetch(url):
            if url == detector.PROFILE_URL:
                return profile
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                raise RuntimeError("public index unavailable")
            return '<meta property="og:description" content="候選 HK$22" />'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _ = detector.detect(
                output_path=root / "candidates.json",
                state_path=root / "state.json",
                sources_path=root / "missing-sources.json",
                fetcher=fetch,
                today=date(2026, 11, 17),
                now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))),
            )
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(report["postDiscoveryStatus"], "partial")
        self.assertEqual(report["sourceFailures"], ["public-search-rss"])
        self.assertFalse(report["discoveryComplete"])
        self.assertIsNone(state["lastSuccessfulScanAt"])

    def test_no_new_posts_advances_success_checkpoint_without_candidate_churn(self):
        profile = '<a href="/sushiro.hk/p/STABLE123/">one</a>'
        post = '<meta property="og:description" content="穩定菜式 HK$22" />'

        def fake_fetch(url):
            if url == detector.PROFILE_URL:
                return profile
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            return post

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            sources = root / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            first, _ = detector.detect(
                output_path=output, state_path=state, sources_path=sources, fetcher=fake_fetch,
                today=date(2026, 11, 17), now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))),
            )
            second, changed = detector.detect(
                output_path=output, state_path=state, sources_path=sources, fetcher=fake_fetch,
                today=date(2026, 11, 18), now=datetime(2026, 11, 18, 6, tzinfo=timezone(timedelta(hours=8))),
            )
            saved_state = json.loads(state.read_text(encoding="utf-8"))

        self.assertFalse(changed)
        self.assertEqual(first["posts"], second["posts"])
        self.assertEqual(saved_state["lastSuccessfulScanAt"], "2026-11-18T06:00:00+08:00")

    def test_edited_caption_changes_fingerprint_and_reprocesses_items(self):
        profile = '<a href="/sushiro.hk/p/EDIT123/">one</a>'
        current = {"caption": "舊菜式 HK$18"}

        def fake_fetch(url):
            if url == detector.PROFILE_URL:
                return profile
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            return f'<meta property="og:description" content="{current["caption"]}" />'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            sources = root / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            detector.detect(output_path=output, state_path=state, sources_path=sources, fetcher=fake_fetch, today=date(2026, 11, 17), now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))))
            first = json.loads(output.read_text(encoding="utf-8"))
            current["caption"] = "新菜式 HK$22"
            second, changed = detector.detect(output_path=output, state_path=state, sources_path=sources, fetcher=fake_fetch, today=date(2026, 11, 18), now=datetime(2026, 11, 18, 6, tzinfo=timezone(timedelta(hours=8))))

        self.assertTrue(changed)
        self.assertNotEqual(first["posts"][0]["contentFingerprint"], second["posts"][0]["contentFingerprint"])
        self.assertEqual(second["posts"][0]["items"][0]["price"], 22)

    def test_seven_day_overlap_and_changed_fingerprint(self):
        self.assertTrue(detector.should_process_post(date(2026, 11, 12), "2026-11-17T06:00:00+08:00", "same", "same", today=date(2026, 11, 18)))
        self.assertFalse(detector.should_process_post(date(2026, 11, 1), "2026-11-17T06:00:00+08:00", "same", "same", today=date(2026, 11, 18)))
        self.assertTrue(detector.should_process_post(date(2026, 11, 1), "2026-11-17T06:00:00+08:00", "old", "new", today=date(2026, 11, 18)))

    def test_empty_or_failed_discovery_does_not_advance_success_checkpoint(self):
        profile = '<a href="/sushiro.hk/p/KEEP123/">one</a>'
        post = '<meta property="og:description" content="保留菜式 HK$18" />'

        def good_fetch(url):
                if url == detector.PROFILE_URL:
                    return profile
                if url == detector.OFFICIAL_DISCOVERY_URL:
                    return "<html><body>No links</body></html>"
                if url == detector.PUBLIC_INDEX_FEED_URL:
                    return "<rss><channel /></rss>"
                return post

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "instagram_candidates.json"
            state = root / "instagram_scan_state.json"
            sources = root / "instagram_sources.json"
            sources.write_text(json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": []}), encoding="utf-8")
            detector.detect(output_path=output, state_path=state, sources_path=sources, fetcher=good_fetch, today=date(2026, 11, 17), now=datetime(2026, 11, 17, 6, tzinfo=timezone(timedelta(hours=8))))
            successful = json.loads(state.read_text(encoding="utf-8"))["lastSuccessfulScanAt"]

            def empty_fetch(url):
                if url == detector.PROFILE_URL:
                    return "<html><body>profile loaded</body></html>"
                if url == detector.OFFICIAL_DISCOVERY_URL:
                    return "<html><body>No links</body></html>"
                if url == detector.PUBLIC_INDEX_FEED_URL:
                    return "<rss><channel /></rss>"
                raise AssertionError(url)

            empty, _ = detector.detect(output_path=output, state_path=state, sources_path=sources, fetcher=empty_fetch, today=date(2026, 11, 18), now=datetime(2026, 11, 18, 6, tzinfo=timezone(timedelta(hours=8))))
            after_empty = json.loads(state.read_text(encoding="utf-8"))

            def failed_post_fetch(url):
                if url == detector.PROFILE_URL:
                    return profile
                if url == detector.OFFICIAL_DISCOVERY_URL:
                    return "<html><body>No links</body></html>"
                if url == detector.PUBLIC_INDEX_FEED_URL:
                    return "<rss><channel /></rss>"
                raise RuntimeError("post blocked")

            failed, _ = detector.detect(output_path=output, state_path=state, sources_path=sources, fetcher=failed_post_fetch, today=date(2026, 11, 19), now=datetime(2026, 11, 19, 6, tzinfo=timezone(timedelta(hours=8))))

        self.assertEqual(empty["postDiscoveryStatus"], "none")
        self.assertEqual(after_empty["lastSuccessfulScanAt"], successful)
        self.assertEqual(failed["postDiscoveryStatus"], "partial")
        self.assertEqual(failed["posts"][0]["items"][0]["price"], 18)

    def test_manual_post_url_is_fetched_when_no_transcription_is_supplied(self):
        post = '<meta property="og:description" content="手動指定貼文 HK$18" />'

        def fake_fetch(url):
            if url == detector.PROFILE_URL:
                return "<html></html>"
            if url == detector.OFFICIAL_DISCOVERY_URL:
                return "<html><body>No links</body></html>"
            if url == detector.PUBLIC_INDEX_FEED_URL:
                return "<rss><channel /></rss>"
            return post

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "instagram_candidates.json"
            sources = Path(directory) / "instagram_sources.json"
            sources.write_text(
                json.dumps({"schemaVersion": 1, "account": "sushiro.hk", "posts": [{"url": POST_URL}]}),
                encoding="utf-8",
            )
            report, _ = detector.detect(
                output_path=output,
                sources_path=sources,
                fetcher=fake_fetch,
                today=date(2026, 11, 17),
            )

        self.assertEqual(report["posts"][0]["items"][0]["price"], 18)
        self.assertEqual(report["sourceStatus"], "manual_only")
        self.assertEqual(report["postDiscoveryStatus"], "manual_only")

    def test_candidate_validation_rejects_non_reviewable_report(self):
        candidate = detector.make_candidate(POST_URL, today=date(2026, 11, 17))
        candidate["requiresReview"] = False
        report = {
            "schemaVersion": 1,
            "profileFetchStatus": "success",
            "postDiscoveryStatus": "found",
            "sourceStatus": "posts_found",
            "posts": [candidate],
        }
        with self.assertRaisesRegex(ValueError, "not review-only"):
            detector.validate_candidates(report)


if __name__ == "__main__":
    unittest.main()
