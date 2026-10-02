"""Firestore persistence with the same item semantics as the local Store."""

import hashlib
from datetime import datetime, timezone

from .store import normalize_url


ITEMS_COLLECTION = "instagram_organizer_items"
META_COLLECTION = "instagram_organizer_meta"


def merge_item(existing, record, url, now):
    """Return the merged item and its change type without altering either input."""
    source = record.get("source", "post")
    if source not in ("saved", "dm", "post"):
        source = "post"
    title = str(record.get("title", "내용 확인이 필요한 게시물"))[:250]
    body = str(record.get("text", ""))[:2500]
    category = record.get("category", "분류 보류")
    classification = record.get("classification", "pending")
    checked = bool(record.get("detail_checked"))
    if existing is None:
        return {
            "id": hashlib.sha256(url.encode()).hexdigest()[:24],
            "url": url,
            "title": title,
            "text": body,
            "note": "",
            "category": category,
            "sources": [source],
            "classification": classification,
            "created_at": now,
            "updated_at": now,
            "detail_checked": checked,
        }, "added"

    sources = list(dict.fromkeys(existing["sources"] + [source]))
    checked = checked or bool(existing.get("detail_checked"))
    if len(body) < len(existing["text"]) and not record.get("detail_checked"):
        body = existing["text"]
        title = existing["title"]
        category = existing["category"]
        classification = existing["classification"]
    if existing["classification"] == "manual":
        category = existing["category"]
        classification = "manual"
    if not title or title.startswith("내용 확인"):
        title = existing["title"]
    changes = {
        "title": title, "text": body, "category": category,
        "classification": classification, "sources": sources,
        "detail_checked": checked,
    }
    if all(existing.get(key, False if key == "detail_checked" else None) == value for key, value in changes.items()):
        return dict(existing), "unchanged"
    return {**existing, **changes, "updated_at": now}, "updated"


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
        values = snapshot.to_dict()
        values["id"] = snapshot.id
        values["sources"] = list(values.get("sources", []))
        values["detail_checked"] = bool(values.get("detail_checked"))
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
                 and (not q or q.casefold() in " ".join(str(item[key]) for key in ("title", "text", "note", "url")).casefold())]
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def total(self):
        snapshot = self.summary.get()
        return int(snapshot.to_dict().get("count", 0)) if snapshot.exists else 0

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
                    count = int(summary.to_dict().get("count", 0)) if summary.exists else 0
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
                item = {
                    **existing,
                    "category": record.get("category", "분류 보류"),
                    "classification": record.get("classification", "pending"),
                    "text": record.get("text", "")[:2500] or existing["text"],
                    "title": record.get("title", "")[:250] or existing["title"],
                    "detail_checked": bool(record.get("detail_checked") or existing.get("detail_checked")),
                    "updated_at": now,
                }
                transaction.set(reference, item)
                return True

            updated += self._transaction(operation)
        return updated
