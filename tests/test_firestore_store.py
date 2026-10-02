import copy
import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from app.firestore_store import FirestoreStore, ITEMS_COLLECTION, META_COLLECTION
from app.store import Store, normalize_url


class FakeSnapshot:
    def __init__(self, reference, values):
        self.id = reference.id
        self.exists = values is not None
        self.values = copy.deepcopy(values)

    def to_dict(self):
        return copy.deepcopy(self.values)


class FakeReference:
    def __init__(self, client, collection, item_id):
        self.client = client
        self.collection = collection
        self.id = item_id

    def get(self, transaction=None):
        if transaction is not None and transaction.writes:
            raise AssertionError("Firestore cannot read after writing in a transaction")
        self.client.reads.append((self.collection, self.id))
        return FakeSnapshot(self, self.client.values.get((self.collection, self.id)))


class FakeCollection:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def document(self, item_id):
        return FakeReference(self.client, self.name, item_id)

    def stream(self):
        self.client.streams.append(self.name)
        for (collection, item_id), values in list(self.client.values.items()):
            if collection == self.name:
                yield FakeSnapshot(self.document(item_id), values)


class FakeTransaction:
    def __init__(self, client):
        self.client = client
        self.writes = []

    def set(self, reference, values, merge=False):
        self.writes.append((reference, copy.deepcopy(values), merge))

    def run(self, operation):
        with self.client.lock:
            result = operation(self)
            for reference, values, merge in self.writes:
                key = (reference.collection, reference.id)
                self.client.values[key] = {**self.client.values.get(key, {}), **values} if merge else values
            self.client.commits.append(len(self.writes))
            return result


class FakeClient:
    def __init__(self):
        self.values = {}
        self.lock = threading.RLock()
        self.reads = []
        self.streams = []
        self.commits = []

    def collection(self, name):
        if name not in (ITEMS_COLLECTION, META_COLLECTION):
            raise AssertionError("An unrelated collection was accessed")
        return FakeCollection(self, name)

    def transaction(self):
        return FakeTransaction(self)


class FirestoreStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.store = FirestoreStore(client=self.client)

    @staticmethod
    def record(**changes):
        return {"url": "https://www.instagram.com/p/ABC/?igsh=tracking",
                "title": "카페 추천", "text": "성수 카페 커피 디저트", "category": "카페",
                "source": "saved", "classification": "rules", **changes}

    def test_count_and_item_creation_are_atomic_and_deduplicated(self):
        self.assertEqual(self.store.total(), 0)
        self.assertEqual(self.store.upsert([self.record(), self.record()]), (1, 0))
        self.assertEqual(self.client.commits, [2, 0])
        item = self.store.all()[0]
        expected = hashlib.sha256(normalize_url(self.record()["url"]).encode()).hexdigest()[:24]
        self.assertEqual(item["id"], expected)
        self.assertEqual(item["url"], "https://www.instagram.com/p/ABC/")
        self.assertEqual(self.store.total(), 1)
        self.client.streams.clear()
        self.client.reads.clear()
        self.assertEqual(self.store.total(), 1)
        self.assertEqual(self.client.streams, [])
        self.assertEqual(self.client.reads, [(META_COLLECTION, "summary")])

    def test_concurrent_new_url_does_not_double_count(self):
        other = FirestoreStore(client=self.client)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda pair: pair[0].upsert([pair[1]]),
                                        [(self.store, self.record()), (other, self.record(source="dm"))]))
        self.assertEqual(sum(result[0] for result in results), 1)
        self.assertEqual(self.store.total(), 1)
        self.assertEqual(set(self.store.all()[0]["sources"]), {"saved", "dm"})

    def test_manual_edits_sources_and_detail_heuristics_match_local_store(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Store(directory)
            for store in (local, self.store):
                self.assertEqual(store.upsert([self.record()]), (1, 0))
                item = store.all()[0]
                store.update(item["id"], "문화·취미", "친구에게 추천")
                self.assertEqual(store.upsert([self.record(source="dm", text="커피", title="짧은 제목")]), (0, 1))
                self.assertEqual(store.upsert([self.record(source="dm", text="요리", title="상세 제목", category="맛집", detail_checked=True)]), (0, 1))
                self.assertEqual(store.upsert([self.record(source="dm", text="", title="내용 확인이 필요한 게시물")]), (0, 0))
                item = store.all()[0]
                self.assertEqual(item["text"], "요리")
                self.assertEqual((item["category"], item["note"], item["classification"]), ("문화·취미", "친구에게 추천", "manual"))
                self.assertTrue(item["detail_checked"])
            excluded = {"created_at", "updated_at"}
            self.assertEqual({k: v for k, v in local.all()[0].items() if k not in excluded},
                             {k: v for k, v in self.store.all()[0].items() if k not in excluded})

    def test_reclassification_preserves_manual_and_updates_eligible_items(self):
        self.store.upsert([self.record(), self.record(url="https://example.com/?id=2", source="dm")])
        manual, automatic = self.store.all()
        self.store.update(manual["id"], "여행", "보존할 메모")
        records = [{**item, "category": "맛집", "classification": "ai", "text": "레시피", "title": "요리", "detail_checked": True}
                   for item in (manual, automatic)]
        records.append({"id": "missing"})
        self.assertEqual(self.store.reclassify(records), 1)
        self.assertEqual(self.store.total(), 2)
        manual_item = next(item for item in self.store.all() if item["id"] == manual["id"])
        self.assertEqual((manual_item["category"], manual_item["note"], manual_item["classification"]), ("여행", "보존할 메모", "manual"))
        automatic_item = next(item for item in self.store.all() if item["id"] == automatic["id"])
        self.assertEqual((automatic_item["text"], automatic_item["category"], automatic_item["classification"]), ("레시피", "맛집", "ai"))
        self.assertTrue(automatic_item["detail_checked"])
        self.assertEqual(self.store.reclassify([{**automatic_item, "text": "", "title": ""}]), 1)
        self.assertEqual(next(item for item in self.store.all() if item["id"] == automatic["id"])["text"], "레시피")

    def test_filters_invalid_links_and_missing_updates(self):
        self.assertEqual(self.store.upsert([self.record(url="javascript:alert(1)")]), (0, 0))
        self.store.upsert([self.record(), self.record(url="https://example.com/?id=3", title="제주 숙소", text="여행", category="여행", source="dm")])
        self.assertEqual(len(self.store.all(q="제주", category="여행", source="dm")), 1)
        self.assertEqual(self.store.all(category="카페", source="dm"), [])
        self.assertEqual(self.store.all(q="없는 내용"), [])
        self.assertIsNone(self.store.update("missing", "맛집", "메모"))
        self.assertIsNone(self.store.update("nested/path", "맛집", "메모"))
        self.assertEqual(set(self.client.streams), {ITEMS_COLLECTION})
        self.assertEqual(self.store.total(), 2)


if __name__ == "__main__":
    unittest.main()
