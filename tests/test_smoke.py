from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from review_scraper.core.database import Base, get_engine, get_session
from review_scraper.main import app
from review_scraper.models import Product, Review, ScrapeTask, ScrapeTaskReview, TaskArtifact


class SmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.create_all(bind=get_engine())
        self.client = TestClient(app)

    def test_task_creation_and_export_smoke(self) -> None:
        external_id = str(uuid4())
        with patch("review_scraper.workers.celery_app.celery_app.send_task"):
            create = self.client.post("/api/reviews/tmall/scrape", json={"url": f"https://detail.tmall.com/item.htm?id={external_id}"})
            self.assertEqual(create.status_code, 200)
            task_id = create.json()["task_id"]

        db = get_session()
        try:
            product = db.query(Product).filter(Product.external_product_id == external_id).first()
            task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
        finally:
            db.close()
        self.assertIsNotNone(product)
        self.assertIsNotNone(task)
        self.assertEqual(task.product_id, product.id)

    def test_analysis_export_and_artifact_smoke(self) -> None:
        db = get_session()
        try:
            product = Product(platform="tmall", source_url="https://detail.tmall.com/item.htm?id=smoke", normalized_url="https://detail.tmall.com/item.htm?id=smoke", external_product_id="smoke")
            db.add(product)
            db.flush()
            task = ScrapeTask(task_id="smoke-task", product_id=product.id, platform="tmall", status="done")
            db.add(task)
            db.flush()
            review = Review(task_id="smoke-task", product_id=product.id, platform="tmall", external_product_id="smoke", review_id="r-smoke", content="很好，推荐")
            db.add(review)
            db.flush()
            db.add(ScrapeTaskReview(task_id=task.task_id, review_id=review.id, product_id=product.id))
            db.commit()
        finally:
            db.close()

        md = self.client.get("/api/analysis/tasks/smoke-task/report.md")
        pdf = self.client.get("/api/analysis/tasks/smoke-task/report.pdf")
        self.assertEqual(md.status_code, 200)
        self.assertEqual(pdf.status_code, 200)

        db = get_session()
        try:
            artifacts = db.query(TaskArtifact).filter(TaskArtifact.task_id == "smoke-task").all()
        finally:
            db.close()
        self.assertGreaterEqual(len(artifacts), 2)

    def test_failure_evidence_route_smoke(self) -> None:
        db = get_session()
        try:
            product = Product(platform="tmall", source_url="https://detail.tmall.com/item.htm?id=failure", normalized_url="https://detail.tmall.com/item.htm?id=failure", external_product_id="failure")
            db.add(product)
            db.flush()
            task = ScrapeTask(task_id="failed-task", product_id=product.id, platform="tmall", status="failed", error_message="boom", file_paths='{"task_error": "x", "error_html": "y", "console_log": "z", "screenshot": "w"}')
            db.add(task)
            db.flush()
            db.commit()
        finally:
            db.close()

        resp = self.client.get("/api/reviews/tmall/tasks/failed-task")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "failed")
        self.assertIn("artifacts", payload)


if __name__ == "__main__":
    unittest.main()
