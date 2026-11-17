import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import detect_instagram_menu as detector


POST_URL = "https://www.instagram.com/p/ABC123/"


class InstagramParserTests(unittest.TestCase):
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
        self.assertEqual(candidate["items"], [])
        self.assertTrue(any("explicit HKD" in reason for reason in candidate["reviewReasons"]))

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

        self.assertTrue(changed)
        self.assertEqual(report["sourceStatus"], "unavailable")
        self.assertEqual(report["profileFetchStatus"], "unavailable")
        self.assertEqual(report["postDiscoveryStatus"], "unavailable")
        self.assertEqual(report["posts"][0]["url"], POST_URL)
        self.assertEqual(report["posts"][0]["items"][0]["price"], 18)

    def test_detect_deduplicates_profile_links_and_keeps_menu_data_separate(self):
        profile = '<a href="/p/ABC123/">one</a><a href="/p/ABC123/?img_index=2">two</a>'
        post = '<meta property="og:description" content="新菜式 HK$22" />'

        def fake_fetch(url):
            return profile if url == detector.PROFILE_URL else post

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

    def test_manual_post_url_is_fetched_when_no_transcription_is_supplied(self):
        post = '<meta property="og:description" content="手動指定貼文 HK$18" />'

        def fake_fetch(url):
            return "<html></html>" if url == detector.PROFILE_URL else post

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
