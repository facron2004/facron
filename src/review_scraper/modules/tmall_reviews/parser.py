"""Tmall review parser — pure functions for JSONP parsing, review extraction, and flattening."""

from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

LIKELY_REVIEW_KEYS = {
    "rateContent",
    "rateDate",
    "auctionSku",
    "displayUserNick",
    "appendComment",
    "pics",
    "useful",
    "userNick",
    "comment",
    "content",
}
LIKELY_LIST_KEYS = {
    "rateList",
    "reviewList",
    "itemReviewList",
    "comments",
    "list",
    "items",
}
LIKELY_TOTAL_KEYS = {
    "lastPage",
    "pageCount",
    "pages",
    "totalPage",
    "totalPages",
    "totalCount",
    "count",
    "currentPage",
    "pageNo",
}


def extract_item_id(item_url: str) -> str:
    parsed = urlparse(item_url)
    item_id = parse_qs(parsed.query).get("id", [""])[0].strip()
    if not item_id:
        raise ValueError("Could not find item id in the supplied URL.")
    return item_id


def parse_jsonp(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Cannot parse empty JSONP payload.")
    if stripped[0] in "[{":
        return json.loads(stripped)
    start = stripped.find("(")
    end = stripped.rfind(")")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Not a JSON or JSONP payload.")
    return json.loads(stripped[start + 1 : end])


def walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def score_review_record(record: dict[str, Any]) -> int:
    score = 0
    for key in LIKELY_REVIEW_KEYS:
        if key in record and record[key] not in (None, "", [], {}):
            score += 1
    return score


def locate_review_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    best_score = 0
    best_list: list[dict[str, Any]] = []

    for node in walk_json(payload):
        if not isinstance(node, list) or not node:
            continue
        if not all(isinstance(item, dict) for item in node):
            continue

        score = sum(score_review_record(item) for item in node)
        if score <= 0:
            continue

        if score > best_score:
            best_score = score
            best_list = [dict(item) for item in node]

    return best_list


def extract_pagination_info(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    current_page: int | None = None
    total_pages: int | None = None

    for node in walk_json(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if key not in LIKELY_TOTAL_KEYS:
                continue
            # Tmall's mtop responses ship pagination numbers as STRINGS
            # (e.g. totalPage='67', total='1000', totalFuzzy='1000+'). Skip
            # the noisy fuzzy strings (trailing '+') but accept plain digit
            # strings and ints.
            parsed = _coerce_int(value)
            if parsed is None:
                continue

            lower_key = key.lower()
            if current_page is None and lower_key in {"currentpage", "pageno"}:
                current_page = parsed
            elif total_pages is None and lower_key in {"lastpage", "pagecount", "pages", "totalpage", "totalpages"}:
                total_pages = parsed

    return current_page, total_pages


def _coerce_int(value: Any) -> int | None:
    """Best-effort coerce a value to int. Returns None for fuzzy markers
    like ``'1000+'`` or unparseable strings. Tmall uses stringified numbers
    in some mtop response variants, so we accept both int and digit strings.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.isdigit():
            return None
        return int(stripped)
    return None


def extract_page_number_from_url(url: str) -> int | None:
    query = parse_qs(urlparse(url).query)
    for key in ("currentPage", "pageNo", "page", "pageno"):
        values = query.get(key)
        if not values:
            continue
        try:
            return int(values[0])
        except ValueError:
            continue
    return None


def flatten_review(review: dict[str, Any], batch, index: int) -> dict[str, Any]:
    append_comment = review.get("appendComment")
    append_content = ""
    append_date = ""
    if isinstance(append_comment, dict):
        append_content = str(append_comment.get("content", "") or append_comment.get("comment", "") or "")
        append_date = str(append_comment.get("commentTime", "") or append_comment.get("time", "") or "")

    pictures = review.get("pics") or review.get("images") or []
    picture_urls = []
    if isinstance(pictures, list):
        for picture in pictures:
            if isinstance(picture, str):
                picture_urls.append(picture)
            elif isinstance(picture, dict):
                value = picture.get("url") or picture.get("picUrl") or picture.get("thumbnail")
                if value:
                    picture_urls.append(str(value))

    review_id = (
        review.get("id")
        or review.get("rateId")
        or review.get("reviewId")
        or review.get("feedbackId")
        or f"batch-{batch.page_number or 1}-{index}"
    )

    content = (
        review.get("rateContent")
        or review.get("content")
        or review.get("comment")
        or review.get("feedback")
        or ""
    )

    return {
        "review_id": str(review_id),
        "user_nick": str(review.get("displayUserNick") or review.get("userNick") or review.get("nick") or ""),
        "review_date": str(review.get("rateDate") or review.get("date") or review.get("time") or ""),
        "sku": str(review.get("auctionSku") or review.get("sku") or ""),
        "content": str(content),
        "append_content": append_content,
        "append_date": append_date,
        "helpful_count": review.get("useful") or review.get("helpfulCount") or 0,
        "page_number": batch.page_number,
        "total_pages": batch.total_pages,
        "picture_urls": "\n".join(picture_urls),
        "source_url": batch.source_url,
    }


def review_key(flattened_review: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        flattened_review.get("review_id", ""),
        flattened_review.get("user_nick", ""),
        flattened_review.get("review_date", ""),
        flattened_review.get("content", ""),
    )
