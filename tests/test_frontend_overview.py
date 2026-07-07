from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from review_scraper.core.database import Base, get_engine
from review_scraper.main import app


class FrontendOverviewTests(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.create_all(bind=get_engine())
        self.client = TestClient(app)

    def test_frontend_html_page_works(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_frontend_overview_api_shape(self) -> None:
        response = self.client.get("/api/frontend/overview")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("title", payload)
        self.assertIn("modules", payload)
        self.assertIn("pipeline_steps", payload)
        self.assertIn("stats", payload)
        self.assertIn("total_tasks", payload["stats"])
        self.assertIn("total_reviews", payload["stats"])

    def test_reviews_page_works(self) -> None:
        response = self.client.get("/reviews")
        self.assertEqual(response.status_code, 200)

    def test_products_page_works(self) -> None:
        response = self.client.get("/products")
        self.assertEqual(response.status_code, 200)

    def test_tasks_page_works(self) -> None:
        response = self.client.get("/tasks")
        self.assertEqual(response.status_code, 200)

    def test_analysis_page_works(self) -> None:
        response = self.client.get("/analysis")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
