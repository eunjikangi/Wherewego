import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .details import details_search_text, has_details, merge_details, normalize_details, normalize_thumbnail


def normalize_url(raw):
    try:
        parts = urlsplit(raw.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower()
        if host == "l.instagram.com":
            return normalize_url(dict(parse_qsl(parts.query)).get("u", ""))
        if host in ("instagram.com", "www.instagram.com"):
            host = "www.instagram.com"
            query = ""
        else:
            query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                               if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "igsh", "igshid")])
        # Preserve non-default ports and meaningful external query parameters.
        port = parts.port
        netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port and not (parts.scheme == "http" and port == 80 or parts.scheme == "https" and port == 443) else "")
        return urlunsplit((parts.scheme, netloc, parts.path or "/", query, ""))
    except (ValueError, AttributeError):
        return ""


class Store:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "library.sqlite3"
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                text TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', category TEXT NOT NULL,
                sources TEXT NOT NULL, classification TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                detail_checked INTEGER NOT NULL DEFAULT 0,
                details TEXT NOT NULL DEFAULT '{}', thumbnail TEXT NOT NULL DEFAULT '')""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
            if "detail_checked" not in columns:
                db.execute("ALTER TABLE items ADD COLUMN detail_checked INTEGER NOT NULL DEFAULT 0")
            if "details" not in columns:
                db.execute("ALTER TABLE items ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
            if "thumbnail" not in columns:
                db.execute("ALTER TABLE items ADD COLUMN thumbnail TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def item(row):
        result = dict(row)
        result["sources"] = json.loads(result["sources"])
        result["detail_checked"] = bool(result["detail_checked"])
        try:
            details = json.loads(result.get("details", "{}"))
        except (TypeError, ValueError):
            details = {}
        result["details"] = normalize_details(details)
        result["thumbnail"] = normalize_thumbnail(result.get("thumbnail", ""))
        return result

    def all(self, q="", category="", source=""):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()
        items = [self.item(row) for row in rows]
        return [item for item in items
                if (not category or item["category"] == category)
                and (not source or source in item["sources"])
                and (not q or q.casefold() in item_search_text(item).casefold())]

    def total(self):
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def upsert(self, records):
        added = updated = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            for record in records:
                url = normalize_url(record.get("url", ""))
                if not url:
                    continue
                row = db.execute("SELECT * FROM items WHERE url = ?", (url,)).fetchone()
                item, change = merge_item(self.item(row) if row else None, record, url, now)
                if change == "unchanged":
                    continue
                values = (item["title"], item["text"], item["category"], json.dumps(item["sources"]), item["classification"],
                          now, int(item["detail_checked"]), json.dumps(item["details"], ensure_ascii=False), item["thumbnail"])
                if change == "updated":
                    db.execute("UPDATE items SET title=?, text=?, category=?, sources=?, classification=?, updated_at=?, detail_checked=?, details=?, thumbnail=? WHERE id=?",
                               (*values, item["id"]))
                    updated += 1
                else:
                    db.execute("INSERT INTO items (id,url,title,text,note,category,sources,classification,created_at,updated_at,detail_checked,details,thumbnail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (item["id"], url, item["title"], item["text"], "", item["category"], json.dumps(item["sources"]), item["classification"],
                                now, now, int(item["detail_checked"]), json.dumps(item["details"], ensure_ascii=False), item["thumbnail"]))
                    added += 1
        return added, updated

    def update(self, item_id, category, note):
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            cursor = db.execute("UPDATE items SET category=?, note=?, classification='manual', updated_at=? WHERE id=?",
                                (category, note, now, item_id))
            row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self.item(row) if cursor.rowcount else None

    def reclassify(self, records):
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            for record in records:
                row = db.execute("SELECT * FROM items WHERE id=?", (record["id"],)).fetchone()
                if not row or row["classification"] == "manual":
                    continue
                existing = self.item(row)
                item = reclassified_item(existing, record, now)
                db.execute("UPDATE items SET category=?,classification=?,text=?,title=?,updated_at=?,detail_checked=?,details=?,thumbnail=? WHERE id=?",
                           (item["category"], item["classification"], item["text"], item["title"], now, int(item["detail_checked"]),
                            json.dumps(item["details"], ensure_ascii=False), item["thumbnail"], record["id"]))
                updated += 1
        return updated


def item_search_text(item):
    return " ".join([*(str(item.get(key, "")) for key in ("title", "text", "note", "url")), details_search_text(item.get("details"))])


def merge_item(existing, record, url, now):
    """Shared item merging for SQLite and Firestore, without changing inputs."""
    source = record.get("source", "post")
    if source not in ("saved", "dm", "post"):
        source = "post"
    title = str(record.get("title", "내용 확인이 필요한 게시물"))[:250]
    body = str(record.get("text", ""))[:2500]
    category = record.get("category", "분류 보류")
    classification = record.get("classification", "pending")
    details = normalize_details(record.get("details"))
    thumbnail = normalize_thumbnail(record.get("thumbnail", ""))
    checked = bool(record.get("detail_checked"))
    if existing is None:
        return {
            "id": hashlib.sha256(url.encode()).hexdigest()[:24], "url": url, "title": title, "text": body,
            "note": "", "category": category, "sources": [source], "classification": classification,
            "created_at": now, "updated_at": now, "detail_checked": checked,
            "details": details, "thumbnail": thumbnail,
        }, "added"
    sources = list(dict.fromkeys(existing["sources"] + [source]))
    checked = checked or bool(existing.get("detail_checked"))
    if not body or (len(body) < len(existing["text"]) and (not record.get("detail_checked") or has_details(details))):
        body = existing["text"]
        title = existing["title"]
        if not has_details(details):
            category = existing["category"]
            classification = existing["classification"]
    if classification == "pending" and existing["classification"] != "pending":
        category = existing["category"]
        classification = existing["classification"]
    if existing["classification"] == "manual":
        category = existing["category"]
        classification = "manual"
    if not title or title.startswith("내용 확인"):
        title = existing["title"]
    changes = {
        "title": title, "text": body, "category": category, "classification": classification,
        "sources": sources, "detail_checked": checked,
        "details": merge_details(existing.get("details"), details),
        "thumbnail": thumbnail or normalize_thumbnail(existing.get("thumbnail", "")),
    }
    comparable = {**existing, "details": normalize_details(existing.get("details")), "thumbnail": normalize_thumbnail(existing.get("thumbnail", ""))}
    if all(comparable.get(key) == value for key, value in changes.items()):
        return comparable, "unchanged"
    return {**comparable, **changes, "updated_at": now}, "updated"


def reclassified_item(existing, record, now):
    """Reclassification keeps its historical category behavior and enriches metadata."""
    body = str(record.get("text", ""))[:2500]
    if not body or (len(body) < len(existing["text"]) and has_details(record.get("details"))):
        body = existing["text"]
    return {
        **existing,
        "category": record.get("category", "분류 보류"),
        "classification": record.get("classification", "pending"),
        "text": body,
        "title": str(record.get("title", ""))[:250] or existing["title"],
        "detail_checked": bool(record.get("detail_checked") or existing.get("detail_checked")),
        "details": merge_details(existing.get("details"), record.get("details")),
        "thumbnail": normalize_thumbnail(record.get("thumbnail", "")) or normalize_thumbnail(existing.get("thumbnail", "")),
        "updated_at": now,
    }
