"""Firestore persistence with the same item semantics as the local Store."""

import hashlib
from datetime import datetime, timezone

from .details import normalize_details, normalize_thumbnail
from .store import item_search_text, merge_item, normalize_url, reclassified_item


ITEMS_COLLECTION = "instagram_organizer_items"
META_COLLECTION = "instagram_organizer_meta"


class FirestoreStore:
    def __init__(self, project_id=None, database="(default)", client=None):
        if client is None:
            try:
                from google.cloud import firestore
            except ImportError:
                raise RuntimeError("Firestore 저장소를 사용하려면 google-cloud-firestore를 설치해주세요.") from None
            client = firestore.Client(project=project_id, database=database)
        self.client = client
        self.items = client.collection(ITEMS_COLLECTION)
        self.summary = client.collection(META_COLLECTION).document("summary")

    def _transaction(self, operation):
        transaction = self.client.transaction()
        # Injected in-memory clients can provide an atomic run(callback) method.
        runner = getattr(transaction, "run", None)
        if callable(runner):
            return runner(operation)
        from google.cloud.firestore import transactional
        return transactional(operation)(transaction)

    @staticmethod
    def _item(snapshot):
        if not snapshot.exists:
            return None
        raw = snapshot.to_dict()
        # Old documents gain safe defaults; unsupported fields (including full
        # visual media payloads) are never returned or written back.
        values = {key: raw.get(key, "") if isinstance(raw.get(key, ""), str) else "" for key in (
            "url", "title", "text", "note", "category", "classification", "created_at", "updated_at")}
        values["category"] = values["category"] or "분류 보류"
        values["classification"] = values["classification"] or "pending"
        values["id"] = snapshot.id
        sources = raw.get("sources", [])
        values["sources"] = list(dict.fromkeys(source for source in sources if source in ("saved", "dm", "post"))) if isinstance(sources, list) else []
        values["detail_checked"] = bool(raw.get("detail_checked"))
        values["details"] = normalize_details(raw.get("details"))
        values["thumbnail"] = normalize_thumbnail(raw.get("thumbnail", ""))
        return values

    def _reference(self, item_id):
        if not isinstance(item_id, str) or not item_id or "/" in item_id:
            return None
        return self.items.document(item_id)

    def all(self, q="", category="", source=""):
        items = [self._item(snapshot) for snapshot in self.items.stream()]
        items = [item for item in items if item is not None
                 and (not category or item["category"] == category)
                 and (not source or source in item["sources"])
                 and (not q or q.casefold() in item_search_text(item).casefold())]
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def _count_items(self, transaction=None):
        try:
            snapshots = self.items.stream(transaction=transaction)
        except TypeError:
            # The in-memory transaction runner used in tests holds a lock.
            snapshots = self.items.stream()
        return sum(1 for snapshot in snapshots if snapshot.exists)

    def total(self):
        snapshot = self.summary.get()
        if snapshot.exists:
            return int(snapshot.to_dict().get("count", 0))
        if not self._count_items():
            return 0

        def operation(transaction):
            summary = self.summary.get(transaction=transaction)
            if summary.exists:
                return int(summary.to_dict().get("count", 0))
            count = self._count_items(transaction)
            transaction.set(self.summary, {"count": count}, merge=True)
            return count

        return self._transaction(operation)

    def upsert(self, records):
        added = updated = 0
        now = datetime.now(timezone.utc).isoformat()
        for record in records:
            url = normalize_url(record.get("url", ""))
            if not url:
                continue
            item_id = hashlib.sha256(url.encode()).hexdigest()[:24]
            reference = self.items.document(item_id)

            def operation(transaction):
                existing = self._item(reference.get(transaction=transaction))
                item, change = merge_item(existing, record, url, now)
                if change == "added":
                    summary = self.summary.get(transaction=transaction)
                    count = int(summary.to_dict().get("count", 0)) if summary.exists else self._count_items(transaction)
                    # Complete every read before the first write, as Firestore requires.
                    transaction.set(reference, item)
                    transaction.set(self.summary, {"count": count + 1}, merge=True)
                elif change == "updated":
                    transaction.set(reference, item)
                return change

            change = self._transaction(operation)
            added += change == "added"
            updated += change == "updated"
        return added, updated

    def update(self, item_id, category, note):
        reference = self._reference(item_id)
        if reference is None:
            return None
        now = datetime.now(timezone.utc).isoformat()

        def operation(transaction):
            existing = self._item(reference.get(transaction=transaction))
            if existing is None:
                return None
            item = {**existing, "category": category, "note": note,
                    "classification": "manual", "updated_at": now}
            transaction.set(reference, item)
            return item

        return self._transaction(operation)

    def reclassify(self, records):
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        for record in records:
            reference = self._reference(record["id"])
            if reference is None:
                continue

            def operation(transaction):
                existing = self._item(reference.get(transaction=transaction))
                if existing is None or existing["classification"] == "manual":
                    return False
                item = reclassified_item(existing, record, now)
                transaction.set(reference, item)
                return True

            updated += self._transaction(operation)
        return updated
