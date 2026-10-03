"""Independent boundary checks for device media -> analysis -> durable library."""

import base64
import io
import json
import struct
import time
import unittest
import zlib
from unittest.mock import AsyncMock, patch

from PIL import Image

import test_core
from app.main import app


def image_url(size=(24, 24), format="JPEG", noise=False):
    image = (Image.effect_noise(size, 100).convert("RGB") if noise
             else Image.new("RGB", size, (32, 94, 172)))
    stream = io.BytesIO()
    image.save(stream, format=format, quality=95)
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif"}[format]
    return "data:" + mime + ";base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def decompression_bomb_url():
    # A valid PNG header advertises huge dimensions without allocating that image.
    encoded = image_url(format="PNG").split(",", 1)[1]
    raw = bytearray(base64.b64decode(encoded))
    raw[16:24] = struct.pack(">II", 1_000_000, 1_000_000)
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]))
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class UpgradeIntegrationTests(test_core.APITests):
    test_auth_and_csrf_cover_api_and_browser = None
    test_collection_job_persists_results = None
    test_edit_export_and_validation = None
    headers = {"Origin": "http://testserver"}

    def record(self, **changes):
        result = {
            "url": "https://www.instagram.com/p/MEDIA_BOUNDARY/",
            "title": "사진으로 가져온 게시물", "text": "", "source": "post",
            "source_url": "https://www.instagram.com/p/MEDIA_BOUNDARY/",
        }
        result.update(changes)
        return result

    def submit(self, records, **changes):
        body = {"records": records, "use_ai": False}
        body.update(changes)
        return self.client.post("/api/import", json=body, headers=self.headers)

    def wait_for_job(self):
        for _ in range(100):
            status = self.client.get("/api/status").json()
            if not status["busy"]:
                self.assertEqual(status["job"]["status"], "done", status["job"])
                return status
            time.sleep(.01)
        self.fail("device import did not finish")

    def assert_rejected(self, record):
        response = self.submit([record])
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-image-marker", response.text)
        self.assertEqual(app.state.store.total(), 0)
        self.assertFalse(app.state.busy)

    def test_media_and_thumbnail_still_require_admin_and_same_origin(self):
        record = self.record(media=[{"data_url": image_url(), "kind": "image", "index": 1}],
                             thumbnail=image_url())
        self.assertEqual(self.submit([record]).status_code, 401)
        self.login()
        response = self.client.post("/api/import", json={"records": [record]},
                                    headers={"Origin": "https://elsewhere.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(app.state.store.total(), 0)

    def test_valid_png_is_accepted_but_source_media_never_reaches_storage(self):
        self.login()
        png = image_url(format="PNG")
        thumbnail = image_url()
        record = self.record(media=[{"data_url": png, "kind": "image", "index": 1}],
                             thumbnail=thumbnail)
        self.assertEqual(self.submit([record]).status_code, 200)
        self.wait_for_job()
        stored = app.state.store.all()[0]
        self.assertNotIn("media", stored)
        self.assertEqual(stored["thumbnail"], thumbnail)
        exported = self.client.get("/api/export").json()
        self.assertNotIn(png, json.dumps(exported))
        self.assertNotIn("media", exported["items"][0])

    def test_rejects_non_images_mismatched_mime_and_unsupported_image_formats(self):
        self.login()
        bad = [
            "data:image/jpeg;base64," + base64.b64encode(
                b"\xff\xd8\xffprivate-image-marker\xff\xd9").decode(),
            "data:image/jpeg;base64,not-valid-base64-private-image-marker",
            image_url(format="PNG").replace("image/png", "image/jpeg"),
            image_url().replace("image/jpeg", "image/png"),
            image_url(format="GIF"),
            "data:image/svg+xml;base64," + base64.b64encode(
                b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode(),
            "https://media.example/private-image-marker.jpg",
        ]
        for data_url in bad:
            with self.subTest(mime=data_url.split(",", 1)[0]):
                self.assert_rejected(self.record(media=[{
                    "data_url": data_url, "kind": "image", "index": 1,
                }]))

    def test_rejects_oversize_dimensions_and_decoded_image_budget(self):
        self.login()
        for data_url in (image_url((1281, 12), "PNG"), image_url((12, 1281), "PNG"),
                         image_url((9000, 4), "PNG"), decompression_bomb_url(),
                         image_url((400, 400), noise=True)):
            with self.subTest(encoded_bytes=len(data_url)):
                self.assert_rejected(self.record(media=[{
                    "data_url": data_url, "kind": "image", "index": 1,
                }]))

    def test_thumbnail_has_stricter_dimensions_mime_and_decoded_budget(self):
        self.login()
        for thumbnail in (image_url((321, 10)), image_url(format="PNG"),
                          image_url((320, 320), noise=True),
                          "data:image/jpeg;base64," + base64.b64encode(
                              b"\xff\xd8\xffprivate-image-marker\xff\xd9").decode()):
            with self.subTest(encoded_bytes=len(thumbnail)):
                self.assert_rejected(self.record(thumbnail=thumbnail))

    def test_media_lists_and_frame_metadata_are_bounded(self):
        self.login()
        media = {"data_url": image_url(), "kind": "video_frame", "index": 1,
                 "time_seconds": 0.25}
        for change in ({"kind": "movie"}, {"index": 0}, {"index": True},
                       {"time_seconds": -1}, {"time_seconds": 86401},
                       {"cookie": "private-image-marker"}):
            with self.subTest(change=change):
                self.assert_rejected(self.record(media=[{**media, **change}]))
        self.assert_rejected(self.record(media=[media] * 11))
        records = [self.record(url=f"https://www.instagram.com/p/P{index}/",
                               media=[media]) for index in range(13)]
        self.assertEqual(self.submit(records).status_code, 422)
        self.assertEqual(app.state.store.total(), 0)

    def test_global_overview_counts_remain_stable_when_library_is_filtered(self):
        self.assertEqual(self.client.get("/api/library/overview").status_code, 401)
        self.login()
        cafe = self.record(category="카페", classification="rules", source="saved",
                           text="성수 카페", details={"places": [{"name": "성수카페", "area": "성수"}]})
        travel = self.record(url="https://www.instagram.com/p/TRAVEL_BOUNDARY/",
                             category="여행", classification="rules", source="post",
                             details={"media_status": "failed", "places": [{"area": "제주"}]})
        pending = self.record(url="https://www.instagram.com/p/PENDING_BOUNDARY/", source="saved")
        app.state.store.upsert([cafe, travel, pending])
        app.state.store.upsert([{**cafe, "source": "dm"}])
        overview = self.client.get("/api/library/overview").json()
        self.assertEqual(overview["total"], 3)
        self.assertEqual(overview["source_counts"], {"saved": 2, "dm": 1, "post": 1})
        self.assertEqual({key: value for key, value in overview["category_counts"].items() if value},
                         {"카페": 1, "여행": 1, "분류 보류": 1})
        self.assertEqual(overview["needs_media"], 2)
        self.assertEqual(set(overview["areas"]), {"성수", "제주"})
        self.assertEqual(len(self.client.get("/api/items?category=카페&area=성수&source=dm").json()["items"]), 1)
        self.assertEqual(len(self.client.get("/api/items?needs_media=true").json()["items"]), 2)
        self.assertEqual(self.client.get("/api/library/overview?category=카페&area=성수").json(), overview)

    def test_media_calls_analyzer_and_enriches_manual_item_without_changing_category(self):
        self.login()
        original = self.record(text="성수 카페 커피", title="카페")
        app.state.store.upsert([original])
        item = app.state.store.all()[0]
        app.state.store.update(item["id"], "문화·취미", "친구와 방문")
        png = image_url(format="PNG")
        details = {"summary": "정원카페의 메뉴와 주소", "places": [{
            "name": "정원카페", "address": "서울 성동구 성수로 10", "area": "성수",
            "menus": [{"name": "바닐라 라떼", "price": "6,000원"}], "hours": "",
            "evidence": "정원카페 서울 성동구 성수로 10 바닐라 라떼 6,000원",
        }], "tags": ["성수"], "ocr_text": "정원카페 바닐라 라떼 6,000원",
            "media_count": 1, "media_status": "analyzed"}

        async def analyze(records, **settings):
            self.assertEqual(settings["provider"], "gemini")
            self.assertEqual(records[0]["media"][0]["data_url"], png)
            return [{**record, "category": "카페", "classification": "ai",
                     "details": details} for record in records], ""

        with patch.object(app.state.ai, "key", return_value="private-key-fixture"), \
                patch.object(app.state.ai, "provider", return_value="gemini"), \
                patch.object(app.state.ai, "model", return_value="gemini-flash-latest"), \
                patch("app.main.analyze_records", new=AsyncMock(side_effect=analyze)) as analyzer, \
                patch.object(app.state.browser, "start", new=AsyncMock()) as browser_start:
            response = self.submit([self.record(media=[{
                "data_url": png, "kind": "image", "index": 1,
            }])], use_ai=True, extract_details=True)
            self.assertEqual(response.status_code, 200)
            self.wait_for_job()
            analyzer.assert_awaited_once()
            browser_start.assert_not_awaited()
        stored = app.state.store.all()[0]
        self.assertEqual((stored["category"], stored["classification"], stored["note"]),
                         ("문화·취미", "manual", "친구와 방문"))
        self.assertEqual(stored["details"]["places"][0]["menus"][0]["name"], "바닐라 라떼")
        self.assertEqual(len(app.state.store.all(q="성수로 10")), 1)
        self.assertEqual(len(app.state.store.all(q="바닐라 라떼")), 1)
        self.assertNotIn("media", stored)
        self.assertNotIn(png, self.client.get("/api/export").text)


if __name__ == "__main__":
    unittest.main()
