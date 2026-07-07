"""Review analysis utilities - keyword extraction and sentiment analysis."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

try:
    import jieba
    import jieba.analyse
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


def extract_keywords(texts: list[str], top_n: int = 20) -> list[tuple[str, float]]:
    """Extract top keywords from review texts using TF-IDF.

    Args:
        texts: List of review text strings
        top_n: Number of top keywords to return

    Returns:
        List of (keyword, weight) tuples
    """
    if not JIEBA_AVAILABLE:
        return _extract_keywords_simple(texts, top_n)

    combined_text = " ".join(texts)
    keywords = jieba.analyse.extract_tags(combined_text, topK=top_n, withWeight=True)
    return keywords


def _extract_keywords_simple(texts: list[str], top_n: int = 20) -> list[tuple[str, float]]:
    """Simple keyword extraction without jieba (fallback)."""
    words = []
    for text in texts:
        # Simple word extraction
        words.extend(re.findall(r'\w+', text.lower()))

    # Filter short words and get top N
    words = [w for w in words if len(w) > 1]
    counter = Counter(words)
    total = sum(counter.values())

    return [(word, count / total) for word, count in counter.most_common(top_n)]


def analyze_sentiment(texts: list[str]) -> dict[str, Any]:
    """Analyze sentiment of reviews.

    Args:
        texts: List of review text strings

    Returns:
        Dictionary with sentiment statistics
    """
    positive_keywords = {
        "好", "很好", "不错", "满意", "喜欢", "推荐", "值得", "赞", "棒", "优秀",
        "good", "great", "excellent", "amazing", "love", "perfect", "best"
    }

    negative_keywords = {
        "差", "不好", "失望", "垃圾", "退货", "后悔", "糟糕", "问题", "坏",
        "bad", "poor", "terrible", "worst", "disappointed", "garbage"
    }

    positive_count = 0
    negative_count = 0
    neutral_count = 0

    for text in texts:
        text_lower = text.lower()
        has_positive = any(kw in text_lower for kw in positive_keywords)
        has_negative = any(kw in text_lower for kw in negative_keywords)

        if has_positive and not has_negative:
            positive_count += 1
        elif has_negative and not has_positive:
            negative_count += 1
        else:
            neutral_count += 1

    total = len(texts)
    if total == 0:
        return {
            "total": 0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "sentiment_score": 0.0,
        }

    positive_ratio = positive_count / total
    negative_ratio = negative_count / total
    sentiment_score = (positive_count - negative_count) / total

    return {
        "total": total,
        "positive": positive_count,
        "negative": negative_count,
        "neutral": neutral_count,
        "positive_ratio": round(positive_ratio, 3),
        "negative_ratio": round(negative_ratio, 3),
        "sentiment_score": round(sentiment_score, 3),
    }


def get_review_statistics(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate comprehensive review statistics.

    Args:
        reviews: List of review dictionaries

    Returns:
        Dictionary with statistics
    """
    if not reviews:
        return {
            "total_reviews": 0,
            "avg_rating": 0.0,
            "rating_distribution": {},
        }

    ratings = [r.get("rating", 0) for r in reviews if r.get("rating") is not None]
    rating_counts = Counter(ratings)

    return {
        "total_reviews": len(reviews),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0.0,
        "rating_distribution": dict(rating_counts),
        "has_images": sum(1 for r in reviews if r.get("pictures") or r.get("picture_urls")),
        "has_append": sum(1 for r in reviews if r.get("append_comment") or r.get("append_content")),
    }
