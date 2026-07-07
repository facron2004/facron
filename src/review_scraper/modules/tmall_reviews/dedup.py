"""Review deduplication utilities."""

from __future__ import annotations

from typing import Any


def deduplicate_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate reviews based on review_id and content.

    Args:
        reviews: List of review dictionaries

    Returns:
        Deduplicated list of reviews
    """
    seen_keys: set[tuple[str, str, str, str]] = set()
    unique_reviews = []

    for review in reviews:
        # Create a unique key from review_id, user_nick, date, and content
        key = (
            review.get("review_id", ""),
            review.get("user_nick", ""),
            review.get("review_date", ""),
            review.get("content", ""),
        )

        if key not in seen_keys:
            seen_keys.add(key)
            unique_reviews.append(review)

    return unique_reviews


def merge_duplicate_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate reviews, keeping the most complete version.

    For reviews with the same review_id, keeps the one with the most data.

    Args:
        reviews: List of review dictionaries

    Returns:
        Merged list of reviews
    """
    review_map: dict[str, dict[str, Any]] = {}

    for review in reviews:
        review_id = review.get("review_id")
        if not review_id:
            continue

        if review_id not in review_map:
            review_map[review_id] = review
        else:
            # Keep the review with more non-empty fields
            existing = review_map[review_id]
            existing_count = sum(1 for v in existing.values() if v)
            new_count = sum(1 for v in review.values() if v)

            if new_count > existing_count:
                review_map[review_id] = review

    return list(review_map.values())


def calculate_review_hash(review: dict[str, Any]) -> str:
    """Calculate a hash for a review to detect duplicates.

    Args:
        review: Review dictionary

    Returns:
        Hash string
    """
    import hashlib

    # Use key fields to generate hash
    key_fields = [
        str(review.get("review_id", "")),
        str(review.get("user_nick", "")),
        str(review.get("review_date", "")),
        str(review.get("content", "")),
    ]

    content = "|".join(key_fields)
    return hashlib.sha256(content.encode()).hexdigest()
