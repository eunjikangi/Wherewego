import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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
                detail_checked INTEGER NOT NULL DEFAULT 0)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
            if "detail_checked" not in columns:
                db.execute("ALTER TABLE items ADD COLUMN detail_checked INTEGER NOT NULL DEFAULT 0")

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
        return result

    def all(self, q="", category="", source=""):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()
        items = [self.item(row) for row in rows]
        return [item for item in items
                if (not category or item["category"] == category)
                and (not source or source in item["sources"])
                and (not q or q.casefold() in " ".join(str(item[key]) for key in ("title", "text", "note", "url")).casefold())]

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
                source = record.get("source", "post")
                if source not in ("saved", "dm", "post"):
                    source = "post"
                existing = db.execute("SELECT * FROM items WHERE url = ?", (url,)).fetchone()
                title = str(record.get("title", "내용 확인이 필요한 게시물"))[:250]
                body = str(record.get("text", ""))[:2500]
                category = record.get("category", "분류 보류")
                classification = record.get("classification", "pending")
                if existing:
                    sources = list(dict.fromkeys(json.loads(existing["sources"]) + [source]))
                    checked = int(bool(record.get("detail_checked") or existing["detail_checked"]))
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
                    unchanged = (body == existing["text"] and title == existing["title"] and category == existing["category"]
                                 and classification == existing["classification"] and sources == json.loads(existing["sources"])
                                 and checked == existing["detail_checked"])
                    if unchanged:
                        continue
                    db.execute("UPDATE items SET title=?, text=?, category=?, sources=?, classification=?, updated_at=?, detail_checked=? WHERE id=?",
                               (title, body, category, json.dumps(sources), classification, now, checked, existing["id"]))
                    updated += 1
                else:
                    item_id = hashlib.sha256(url.encode()).hexdigest()[:24]
                    db.execute("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (item_id, url, title, body, "", category, json.dumps([source]), classification, now, now, int(bool(record.get("detail_checked")))))
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
                category = record.get("category", "분류 보류")
                classification = record.get("classification", "pending")
                body = record.get("text", "")[:2500]
                title = record.get("title", "")[:250]
                db.execute("UPDATE items SET category=?,classification=?,text=?,title=?,updated_at=?,detail_checked=? WHERE id=?",
                           (category, classification, body or row["text"], title or row["title"], now, int(bool(record.get("detail_checked") or row["detail_checked"])), record["id"]))
                updated += 1
        return updated
