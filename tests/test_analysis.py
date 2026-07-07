"""Tests for review analysis module."""

from __future__ import annotations

import unittest

from review_scraper.modules.tmall_reviews.analysis import (
    analyze_sentiment,
    extract_keywords,
    get_review_statistics,
)


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_reviews = [
            {"content": "很好的产品，质量不错，推荐购买", "rating": 5, "pictures": ["pic1.jpg"]},
            {"content": "一般般，没有想象中好", "rating": 3, "pictures": None},
            {"content": "非常差，质量很糟糕，不推荐", "rating": 1, "pictures": None},
            {"content": "还可以，价格合理", "rating": 4, "pictures": None},
        ]

    def test_extract_keywords_returns_list(self) -> None:
        """Test keyword extraction returns a list."""
        texts = [r["content"] for r in self.sample_reviews]
        keywords = extract_keywords(texts, top_n=5)
        self.assertIsInstance(keywords, list)
        self.assertLessEqual(len(keywords), 5)

    def test_extract_keywords_format(self) -> None:
        """Test keyword extraction returns tuples with word and weight."""
        texts = ["这是一个测试文本", "另一个测试"]
        keywords = extract_keywords(texts, top_n=3)
        for item in keywords:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)
            self.assertIsInstance(item[0], str)
            self.assertIsInstance(item[1], float)

    def test_analyze_sentiment_structure(self) -> None:
        """Test sentiment analysis returns correct structure."""
        texts = [r["content"] for r in self.sample_reviews]
        sentiment = analyze_sentiment(texts)

        self.assertIn("total", sentiment)
        self.assertIn("positive", sentiment)
        self.assertIn("negative", sentiment)
        self.assertIn("neutral", sentiment)
        self.assertIn("sentiment_score", sentiment)

    def test_analyze_sentiment_counts(self) -> None:
        """Test sentiment counts add up to total."""
        texts = [r["content"] for r in self.sample_reviews]
        sentiment = analyze_sentiment(texts)

        total = sentiment["positive"] + sentiment["negative"] + sentiment["neutral"]
        self.assertEqual(total, sentiment["total"])
        self.assertEqual(sentiment["total"], len(texts))

    def test_analyze_sentiment_empty(self) -> None:
        """Test sentiment analysis with empty list."""
        sentiment = analyze_sentiment([])
        self.assertEqual(sentiment["total"], 0)
        self.assertEqual(sentiment["sentiment_score"], 0.0)

    def test_get_review_statistics_structure(self) -> None:
        """Test review statistics returns correct structure."""
        stats = get_review_statistics(self.sample_reviews)

        self.assertIn("total_reviews", stats)
        self.assertIn("avg_rating", stats)
        self.assertIn("rating_distribution", stats)
        self.assertIn("has_images", stats)

    def test_get_review_statistics_values(self) -> None:
        """Test review statistics calculates correct values."""
        stats = get_review_statistics(self.sample_reviews)

        self.assertEqual(stats["total_reviews"], 4)
        self.assertEqual(stats["avg_rating"], 3.25)
        self.assertEqual(stats["has_images"], 1)

    def test_get_review_statistics_empty(self) -> None:
        """Test review statistics with empty list."""
        stats = get_review_statistics([])
        self.assertEqual(stats["total_reviews"], 0)
        self.assertEqual(stats["avg_rating"], 0.0)


if __name__ == "__main__":
    unittest.main()
