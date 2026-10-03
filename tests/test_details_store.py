import base64
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.details import merge_details, normalize_details, normalize_thumbnail
from app.firestore_store import FirestoreStore, ITEMS_COLLECTION, META_COLLECTION
from app.store import Store
from tests.test_firestore_store import FakeClient


THUMBNAIL = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8bounded-thumbnail\xff\xd9").decode()
DETAILS = {
    "summary": "성수에서 커피와 디저트를 먹을 수 있는 공간",
    "places": [{"name": "모아카페", "address": "서울 성동구 성수이로 10", "area": "성수",
                "menus": [{"name": "바닐라라떼", "price": "6,500원"}],
                "hours": "매일 11:00–20:00", "evidence": "사진 2의 메뉴판과 주소 안내"}],
    "tags": ["테라스", "반려견 동반"], "ocr_text": "성수이로 10\n바닐라라떼 6,500원",
    "media_count": 3, "media_status": "analyzed",
}


class DetailsValidationTests(unittest.TestCase):
    def test_bounds_and_unknown_provider_fields_never_survive_normalization(self):
        value = {**copy.deepcopy(DETAILS), "summary": "s" * 1500, "ocr_text": "o" * 6000,
                 "tags": ["t" * 90] * 15, "api_key": "secret", "images": ["raw-data"]}
        value["places"] = [{**copy.deepcopy(DETAILS["places"][0]), "raw_media": "private",
                            "menus": [{"name": "m" * 160, "price": "p" * 100, "secret": "x"}] * 25}] * 10
        safe = normalize_details(value)
        self.assertEqual(len(safe["summary"]), 1000)
        self.assertEqual(len(safe["ocr_text"]), 4000)
        self.assertEqual(len(safe["places"]), 8)
        self.assertEqual(len(safe["places"][0]["menus"]), 20)
        self.assertEqual(len(safe["places"][0]["menus"][0]["name"]), 120)
        self.assertEqual(len(safe["places"][0]["menus"][0]["price"]), 80)
        self.assertEqual(safe["tags"], ["t" * 40])
        self.assertNotIn("secret", json.dumps(safe))
        self.assertNotIn("raw_media", json.dumps(safe))
        self.assertEqual(normalize_details("invalid"), normalize_details())
        self.assertEqual(normalize_details({"places": [None], "tags": [None], "media_count": True, "media_status": []}), normalize_details())

    def test_strict_api_inputs_reject_unknown_nested_fields_types_and_bounds(self):
        cases = [
            {"unexpected": "x"}, {"summary": "x" * 1001}, {"places": [None]},
            {"places": [{"name": "x", "url": "https://example.com"}]},
            {"places": [{"menus": [{"name": "x", "api_key": "secret"}]}]},
            {"places": [{}] * 9}, {"places": [{"menus": [{}] * 21}]},
            {"tags": ["x"] * 13}, {"tags": [1]}, {"media_count": True}, {"media_count": -1},
            {"media_status": "complete"}, {"ocr_text": "x" * 4001},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_details(value, strict=True)
        self.assertEqual(normalize_details(DETAILS, strict=True), DETAILS)

    def test_only_small_jpeg_data_urls_are_eligible_for_thumbnail_storage(self):
        self.assertEqual(normalize_thumbnail(THUMBNAIL, strict=True), THUMBNAIL)
        invalid = ["https://example.com/picture.jpg", "data:image/svg+xml;base64,PHN2Zz4=",
                   "data:image/jpeg;base64,invalid", "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8" + b"x" * 25000 + b"\xff\xd9").decode(), []]
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(normalize_thumbnail(value), "")
                with self.assertRaises(ValueError):
                    normalize_thumbnail(value, strict=True)

    def test_details_merge_preserves_evidence_and_combines_menu_information(self):
        original = copy.deepcopy(DETAILS)
        richer = {"places": [{"name": "모아카페", "menus": [{"name": "바닐라라떼", "price": ""},
                                                               {"name": "치즈케이크", "price": "7,000원"}]}],
                  "tags": ["성수", "테라스"], "ocr_text": "치즈케이크 7,000원", "media_status": "partial"}
        merged = merge_details(original, richer)
        self.assertEqual(len(merged["places"]), 1)
        self.assertEqual(merged["places"][0]["address"], original["places"][0]["address"])
        self.assertEqual(merged["places"][0]["menus"], original["places"][0]["menus"] + [{"name": "치즈케이크", "price": "7,000원"}])
        self.assertEqual(merged["media_status"], "analyzed")
        self.assertIn("치즈케이크", merged["ocr_text"])
        self.assertEqual(merge_details(original, {"media_status": "failed"}), original)
        expanded = merge_details(original, {"media_count": 5, "media_status": "failed"})
        self.assertEqual(expanded["media_status"], "partial")
        self.assertEqual(expanded["media_count"], 5)
        self.assertEqual(expanded["places"], original["places"])
        incomplete_retry = merge_details({"media_count": 5, "media_status": "partial"}, original)
        self.assertEqual(incomplete_retry["media_status"], "partial")
        self.assertEqual(incomplete_retry["media_count"], 5)
        self.assertEqual(original, DETAILS)


class DetailsStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = FakeClient()
        self.stores = (Store(self.directory.name), FirestoreStore(client=self.client))

    @staticmethod
    def record(**changes):
        return {"url": "https://www.instagram.com/p/RICH/", "title": "성수 카페", "text": "설명이 충분히 있는 원래 게시물입니다.",
                "category": "카페", "classification": "ai", "source": "saved", "details": copy.deepcopy(DETAILS),
                "thumbnail": THUMBNAIL, **changes}

    def test_new_details_and_search_have_the_same_semantics_in_both_stores(self):
        for store in self.stores:
            with self.subTest(store=type(store).__name__):
                self.assertEqual(store.upsert([self.record()]), (1, 0))
                self.assertEqual(store.all()[0]["details"], DETAILS)
                self.assertEqual(store.all()[0]["thumbnail"], THUMBNAIL)
                for term in ("모아카페", "성수이로", "성수", "바닐라라떼", "6,500", "11:00", "메뉴판", "테라스", "디저트"):
                    self.assertEqual(len(store.all(q=term, category="카페", source="saved")), 1, term)
                self.assertEqual(store.all(q="바닐라라떼", source="dm"), [])
                self.assertEqual(store.upsert([self.record()]), (0, 0))
        self.assertEqual(Store(self.directory.name).all()[0]["details"], DETAILS)
        self.assertEqual(FirestoreStore(client=self.client).all()[0]["details"], DETAILS)

    def test_manual_edits_and_details_survive_metadata_only_reimport_and_failed_ai(self):
        for store in self.stores:
            with self.subTest(store=type(store).__name__):
                store.upsert([self.record()])
                original = store.all()[0]
                store.update(original["id"], "여행", "꼭 다시 갈 곳")
                self.assertEqual(store.upsert([self.record(text="", title="", category="분류 보류", classification="pending",
                                                         source="dm", details={}, thumbnail="", detail_checked=True)]), (0, 1))
                item = store.all()[0]
                self.assertEqual((item["category"], item["classification"], item["note"]), ("여행", "manual", "꼭 다시 갈 곳"))
                self.assertEqual(item["text"], original["text"])
                self.assertEqual(item["details"], DETAILS)
                self.assertEqual(item["thumbnail"], THUMBNAIL)
                self.assertEqual(set(item["sources"]), {"saved", "dm"})
                self.assertEqual(store.upsert([self.record(text="", title="", category="분류 보류", classification="pending",
                                                         source="dm", details={"media_status": "failed"}, thumbnail="")]), (0, 0))
                store.upsert([self.record(details={"tags": ["예약 가능"]}, source="dm")])
                self.assertIn("예약 가능", store.all()[0]["details"]["tags"])
                self.assertEqual(store.all()[0]["category"], "여행")

    def test_short_caption_can_gain_ai_category_and_details_without_losing_existing_caption(self):
        for store in self.stores:
            with self.subTest(store=type(store).__name__):
                store.upsert([self.record(category="분류 보류", classification="pending", details={}, thumbnail="")])
                original = store.all()[0]
                store.upsert([self.record(text="커피", title="짧음", detail_checked=True)])
                item = store.all()[0]
                self.assertEqual(item["text"], original["text"])
                self.assertEqual((item["category"], item["classification"]), ("카페", "ai"))
                self.assertEqual(item["details"], DETAILS)

    def test_reclassification_preserves_annotations_on_failed_retry_and_manual_records(self):
        for store in self.stores:
            with self.subTest(store=type(store).__name__):
                store.upsert([self.record()])
                item = store.all()[0]
                self.assertEqual(store.reclassify([{**item, "details": {"media_status": "failed"}, "thumbnail": "", "text": ""}]), 1)
                self.assertEqual(store.all()[0]["details"], DETAILS)
                self.assertEqual(store.all()[0]["thumbnail"], THUMBNAIL)
                store.update(item["id"], "여행", "내 메모")
                self.assertEqual(store.reclassify([{**item, "category": "맛집", "details": {"tags": ["다른 태그"]}}]), 0)
                self.assertEqual(store.all()[0]["category"], "여행")
                self.assertEqual(store.total(), 1)

    def test_raw_media_and_unknown_detail_fields_are_never_persisted(self):
        for store in self.stores:
            store.upsert([self.record(media=[{"data": "sensitive-full-image"}], frames=["full-video-frame"],
                                     details={**copy.deepcopy(DETAILS), "api_key": "private-provider-key", "images": ["full-payload"]})])
            persisted = json.dumps(store.all(), ensure_ascii=False)
            for secret in ("sensitive-full-image", "full-video-frame", "private-provider-key", "full-payload"):
                self.assertNotIn(secret, persisted)
        self.assertNotIn("sensitive-full-image", json.dumps(list(self.client.values.values())))

    def test_existing_sqlite_schema_is_migrated_without_losing_manual_items(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(Path(directory) / "library.sqlite3")
            connection.execute("""CREATE TABLE items (id TEXT PRIMARY KEY, url TEXT UNIQUE, title TEXT,
                text TEXT, note TEXT, category TEXT, sources TEXT, classification TEXT, created_at TEXT, updated_at TEXT)""")
            connection.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)", ("legacy", "https://example.com/legacy", "이전 제목",
                               "이전 설명", "중요 메모", "여행", '["saved"]', "manual", "2025-01-01", "2025-01-01"))
            connection.commit()
            connection.close()
            store = Store(directory)
            item = store.all()[0]
            self.assertEqual((item["id"], item["note"], item["classification"]), ("legacy", "중요 메모", "manual"))
            self.assertEqual(item["details"], normalize_details())
            self.assertEqual(item["thumbnail"], "")
            store.upsert([self.record(url="https://example.com/legacy", source="dm")])
            self.assertEqual(store.total(), 1)
            self.assertEqual(Store(directory).all()[0]["details"], DETAILS)
            self.assertEqual(Store(directory).all()[0]["category"], "여행")
            self.assertEqual(store.upsert([self.record()]), (1, 0))

    def test_old_firestore_docs_receive_safe_defaults_and_missing_summary_is_repaired(self):
        legacy = self.record()
        legacy.pop("details")
        legacy.pop("thumbnail")
        legacy.pop("source")
        legacy.update(id="legacy", sources=["dm"], note="중요 메모", created_at="2025", updated_at="2025", media=["secret-media"])
        self.client.values[(ITEMS_COLLECTION, "legacy")] = legacy
        store = self.stores[1]
        item = store.all()[0]
        self.assertEqual(item["details"], normalize_details())
        self.assertEqual(item["thumbnail"], "")
        self.assertNotIn("media", item)
        self.assertEqual(store.total(), 1)
        self.assertEqual(self.client.values[(META_COLLECTION, "summary")]["count"], 1)
        self.assertEqual(self.client.values[(ITEMS_COLLECTION, "legacy")], legacy)
        store.upsert([self.record(url="https://example.com/new")])
        self.assertEqual(store.total(), 2)

    def test_first_insert_with_existing_firestore_docs_includes_them_in_summary(self):
        legacy = self.record(url="https://example.com/legacy")
        legacy.pop("source")
        legacy.update(sources=["saved"], note="", created_at="2025", updated_at="2025")
        self.client.values[(ITEMS_COLLECTION, "legacy")] = legacy
        self.stores[1].upsert([self.record()])
        self.assertEqual(self.stores[1].total(), 2)


if __name__ == "__main__":
    unittest.main()
