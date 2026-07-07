from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from review_scraper.core.database import Base, get_engine, get_session
from review_scraper.main import app
from review_scraper.models import Product, Review, ScrapeTask, ScrapeTaskReview, TaskArtifact


class ReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.create_all(bind=get_engine())
        self.client = TestClient(app)

    def test_validate_url_rejects_private_and_evil_hosts(self) -> None:
        bad_urls = [
            "file:///tmp/x",
            "https://localhost/item.htm?id=1",
            "https://127.0.0.1/item.htm?id=1",
            "https://10.0.0.1/item.htm?id=1",
            "https://detail.tmall.com.evil.com/item.htm?id=1",
        ]
        for url in bad_urls:
            resp = self.client.post("/api/reviews/tmall/scrape", json={"url": url})
            self.assertEqual(resp.status_code, 400, msg=url)

    def test_create_task_uses_internal_product_id_and_reuses_product(self) -> None:
        with patch("review_scraper.workers.celery_app.celery_app.send_task") as send_task:
            external_id = str(uuid4())
            payload = {"url": f"https://detail.tmall.com/item.htm?id={external_id}"}
            first = self.client.post("/api/reviews/tmall/scrape", json=payload)
            self.assertEqual(first.status_code, 200)
            first_task_id = first.json()["task_id"]

            second = self.client.post("/api/reviews/tmall/scrape", json=payload)
            self.assertEqual(second.status_code, 200)
            second_task_id = second.json()["task_id"]

            db = get_session()
            try:
                tasks = (
                    db.query(ScrapeTask)
                    .join(Product, ScrapeTask.product_id == Product.id)
                    .filter(Product.external_product_id == external_id)
                    .order_by(ScrapeTask.created_at.asc())
                    .all()
                )
                products = db.query(Product).filter(Product.external_product_id == external_id).all()
            finally:
                db.close()

            self.assertEqual(len(products), 1)
            self.assertEqual(tasks[0].product_id, products[0].id)
            self.assertEqual(tasks[1].product_id, products[0].id)
            self.assertNotEqual(first_task_id, second_task_id)
            self.assertEqual(send_task.call_count, 2)

    def test_task_reviews_are_joined_via_association_table(self) -> None:
        db = get_session()
        try:
            product = Product(
                platform="tmall",
                source_url="https://detail.tmall.com/item.htm?id=123456",
                normalized_url="https://detail.tmall.com/item.htm?id=123456",
                external_product_id="123456",
            )
            db.add(product)
            db.flush()
            task = ScrapeTask(task_id="task-1", product_id=product.id, platform="tmall", status="done")
            db.add(task)
            db.flush()
            review = Review(
                task_id="legacy-task",
                product_id=product.id,
                platform="tmall",
                external_product_id="123456",
                review_id="r1",
                content="很好",
            )
            db.add(review)
            db.flush()
            db.add(ScrapeTaskReview(task_id=task.task_id, review_id=review.id, product_id=product.id))
            db.commit()
        finally:
            db.close()

        resp = self.client.get("/api/reviews/tmall/tasks/task-1/reviews")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["review_count"], 1)
        self.assertEqual(payload["reviews"][0]["review_id"], "r1")

    def test_analysis_report_export_creates_artifact(self) -> None:
        db = get_session()
        try:
            product = Product(
                platform="tmall",
                source_url="https://detail.tmall.com/item.htm?id=123456",
                normalized_url="https://detail.tmall.com/item.htm?id=123456",
                external_product_id="123456",
            )
            db.add(product)
            db.flush()
            task = ScrapeTask(task_id="task-report", product_id=product.id, platform="tmall", status="done")
            db.add(task)
            db.flush()
            review = Review(
                task_id="task-report",
                product_id=product.id,
                platform="tmall",
                external_product_id="123456",
                review_id="r2",
                content="很好，值得推荐",
                sku="标准款",
            )
            db.add(review)
            db.flush()
            db.add(ScrapeTaskReview(task_id=task.task_id, review_id=review.id, product_id=product.id))
            db.commit()
        finally:
            db.close()

        resp = self.client.get("/api/analysis/tasks/task-report/report.md")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("评论洞察报告", resp.text)

        db = get_session()
        try:
            artifact = db.query(TaskArtifact).filter(TaskArtifact.task_id == "task-report").first()
        finally:
            db.close()
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.artifact_type, "analysis_report_md")

    def test_analysis_pdf_export_creates_artifact(self) -> None:
        db = get_session()
        try:
            product = Product(
                platform="tmall",
                source_url="https://detail.tmall.com/item.htm?id=123456",
                normalized_url="https://detail.tmall.com/item.htm?id=123456",
                external_product_id="123456",
            )
            db.add(product)
            db.flush()
            task = ScrapeTask(task_id="task-report-pdf", product_id=product.id, platform="tmall", status="done")
            db.add(task)
            db.flush()
            review = Review(
                task_id="task-report-pdf",
                product_id=product.id,
                platform="tmall",
                external_product_id="123456",
                review_id="r3",
                content="质量很好，推荐购买",
                sku="标准款",
            )
            db.add(review)
            db.flush()
            db.add(ScrapeTaskReview(task_id=task.task_id, review_id=review.id, product_id=product.id))
            db.commit()
        finally:
            db.close()

        resp = self.client.get("/api/analysis/tasks/task-report-pdf/report.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self.assertGreater(len(resp.content), 100)

        db = get_session()
        try:
            artifact = db.query(TaskArtifact).filter(TaskArtifact.task_id == "task-report-pdf").first()
        finally:
            db.close()
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.artifact_type, "analysis_report_pdf")


if __name__ == "__main__":
    unittest.main()
