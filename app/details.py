"""Bounded, public annotations extracted from captions and visual media.

Only these small, structured values are persisted. Original image/video payloads
and provider response fields are deliberately excluded.
"""

import base64
import binascii


DETAIL_FIELDS = {"summary", "places", "tags", "ocr_text", "media_count", "media_status"}
PLACE_FIELDS = {"name", "address", "area", "menus", "hours", "evidence"}
MENU_FIELDS = {"name", "price"}
MEDIA_STATUSES = {"none", "analyzed", "partial", "failed"}
MAX_THUMBNAIL_BYTES = 24 * 1024
MAX_THUMBNAIL_TEXT = 40 * 1024
THUMBNAIL_PREFIX = "data:image/jpeg;base64,"


def _text(value, limit, strict, field):
    if not isinstance(value, str):
        if strict:
            raise ValueError(f"{field} must be a string")
        return ""
    value = value.strip()
    if strict and len(value) > limit:
        raise ValueError(f"{field} is too long")
    return value[:limit]


def _objects(value, maximum, strict, field):
    if not isinstance(value, list):
        if strict:
            raise ValueError(f"{field} must be a list")
        return []
    if strict and len(value) > maximum:
        raise ValueError(f"{field} has too many entries")
    return value[:maximum]


def _keys(value, allowed, strict, field):
    if not isinstance(value, dict):
        if strict:
            raise ValueError(f"{field} must be an object")
        return {}
    if strict and set(value) - allowed:
        raise ValueError(f"{field} has unsupported fields")
    return value


def normalize_details(value=None, *, strict=False):
    """Return a safe annotation shape; strict mode validates API inputs."""
    if value is None:
        value = {}
    value = _keys(value, DETAIL_FIELDS, strict, "details")
    places = []
    for raw_place in _objects(value.get("places", []), 8, strict, "places"):
        place = _keys(raw_place, PLACE_FIELDS, strict, "place")
        menus = []
        for raw_menu in _objects(place.get("menus", []), 20, strict, "menus"):
            menu = _keys(raw_menu, MENU_FIELDS, strict, "menu")
            normalized_menu = {
                "name": _text(menu.get("name", ""), 120, strict, "menu.name"),
                "price": _text(menu.get("price", ""), 80, strict, "menu.price"),
            }
            if any(normalized_menu.values()):
                menus.append(normalized_menu)
        normalized_place = {
            "name": _text(place.get("name", ""), 120, strict, "place.name"),
            "address": _text(place.get("address", ""), 250, strict, "place.address"),
            "area": _text(place.get("area", ""), 80, strict, "place.area"),
            "menus": menus,
            "hours": _text(place.get("hours", ""), 250, strict, "place.hours"),
            "evidence": _text(place.get("evidence", ""), 500, strict, "place.evidence"),
        }
        if any(normalized_place.values()):
            places.append(normalized_place)
    tags = []
    for raw_tag in _objects(value.get("tags", []), 12, strict, "tags"):
        tag = _text(raw_tag, 40, strict, "tag")
        if tag and tag not in tags:
            tags.append(tag)
    media_count = value.get("media_count", 0)
    if isinstance(media_count, bool) or not isinstance(media_count, int) or not 0 <= media_count <= 1000:
        if strict:
            raise ValueError("media_count must be an integer between 0 and 1000")
        media_count = 0
    status = value.get("media_status", "none")
    if not isinstance(status, str) or status not in MEDIA_STATUSES:
        if strict:
            raise ValueError("media_status is invalid")
        status = "none"
    return {
        "summary": _text(value.get("summary", ""), 1000, strict, "summary"),
        "places": places,
        "tags": tags,
        "ocr_text": _text(value.get("ocr_text", ""), 4000, strict, "ocr_text"),
        "media_count": media_count,
        "media_status": status,
    }


def normalize_thumbnail(value="", *, strict=False):
    """Accept only a tiny JPEG data URL, never a remote URL or full media file."""
    if value == "" or value is None:
        return ""
    valid = isinstance(value, str) and len(value) <= MAX_THUMBNAIL_TEXT and value.startswith(THUMBNAIL_PREFIX)
    raw = b""
    if valid:
        try:
            raw = base64.b64decode(value[len(THUMBNAIL_PREFIX):], validate=True)
        except (ValueError, binascii.Error):
            valid = False
    valid = valid and 4 <= len(raw) <= MAX_THUMBNAIL_BYTES and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
    if not valid:
        if strict:
            raise ValueError("thumbnail must be a JPEG data URL of at most 24 KiB")
        return ""
    return value


def has_details(value):
    details = normalize_details(value)
    return bool(details["summary"] or details["places"] or details["tags"] or details["ocr_text"]
                or details["media_status"] in ("analyzed", "partial"))


def _richer_text(old, new):
    return new if len(new) > len(old) else old


def _merge_menus(old, new):
    merged = [dict(menu) for menu in old]
    for menu in new:
        match = next((item for item in merged if item["name"].casefold() == menu["name"].casefold()), None)
        if match is None:
            merged.append(dict(menu))
        elif menu["price"]:
            match["price"] = menu["price"]
    return merged[:20]


def merge_details(old, new):
    """Enrich duplicates while preserving successful analysis on empty/failed retries."""
    old, new = normalize_details(old), normalize_details(new)
    places = [{**place, "menus": [dict(menu) for menu in place["menus"]]} for place in old["places"]]
    for place in new["places"]:
        match = next((item for item in places
                      if (item["name"] and item["name"].casefold() == place["name"].casefold()
                          and (not item["address"] or not place["address"] or item["address"].casefold() == place["address"].casefold()))
                      or (item["address"] and item["address"].casefold() == place["address"].casefold()
                          and (not item["name"] or not place["name"]))), None)
        if match is None:
            places.append({**place, "menus": [dict(menu) for menu in place["menus"]]})
        else:
            for key in ("name", "address", "area", "hours", "evidence"):
                match[key] = _richer_text(match[key], place[key])
            match["menus"] = _merge_menus(match["menus"], place["menus"])
    rank = {"none": 0, "failed": 1, "partial": 2, "analyzed": 3}
    status = max((old["media_status"], new["media_status"]), key=rank.__getitem__)
    known_count = max(old["media_count"], new["media_count"])
    analyzed_count = max(details["media_count"] if details["media_status"] == "analyzed" else 0 for details in (old, new))
    if status == "analyzed" and known_count > analyzed_count:
        # Useful older analysis survives, but newly discovered unprocessed
        # slides must not be presented as completely analyzed.
        status = "partial"
    old_ocr, new_ocr = old["ocr_text"], new["ocr_text"]
    if not old_ocr or old_ocr in new_ocr:
        ocr = new_ocr
    elif not new_ocr or new_ocr in old_ocr:
        ocr = old_ocr
    else:
        ocr = "\n".join(dict.fromkeys(old_ocr.splitlines() + new_ocr.splitlines()))[:4000]
    return normalize_details({
        "summary": _richer_text(old["summary"], new["summary"]),
        "places": places[:8],
        "tags": list(dict.fromkeys(old["tags"] + new["tags"]))[:12],
        "ocr_text": ocr,
        "media_count": known_count,
        "media_status": status,
    })


def details_search_text(value):
    details = normalize_details(value)
    terms = [details["summary"], details["ocr_text"], *details["tags"]]
    for place in details["places"]:
        terms.extend(place[key] for key in ("name", "address", "area", "hours", "evidence"))
        for menu in place["menus"]:
            terms.extend((menu["name"], menu["price"]))
    return " ".join(terms)
