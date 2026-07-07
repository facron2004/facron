"""Regression tests for the fixes flagged in review_scraper_code_review.md.

Covers:
* Alembic migration chain has a single head (no two-head divergence).
* ``ReviewCollector.persist_to_database`` flushes new Review rows so the
  ``ScrapeTaskReview.review_id`` NOT NULL constraint is satisfied and
  failure rolls back the in-memory dedup keys.
* The legacy ``/download?format=`` route still resolves a real artifact
  for backwards-compatible UI links.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Force a shared file-backed SQLite BEFORE any review_scraper import so
# the API process and the test process resolve the same DB.
_TMP_DB_PATH = os.path.join(
    tempfile.gettempdir(), "review_scraper_fix_test.db"
)
if os.path.exists(_TMP_DB_PATH):
    os.unlink(_TMP_DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB_PATH}"

REPO_ROOT = Path(__file__).resolve().parents[1]


class AlembicSingleHeadTests(unittest.TestCase):
    def test_alembic_heads_contains_single_revision(self) -> None:
        """`alembic heads` must return exactly one revision for a clean upgrade path."""
        env = os.environ.copy()
        env.setdefault(
            "DATABASE_URL",
            f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}",
        )
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"alembic heads failed: stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        # Empty lines, column headers, and the revision line itself.
        head_lines = [
            line.strip()
            for line in proc.stdout.splitlines()
            if line.strip() and not line.lower().startswith("rev ")
        ]
        self.assertEqual(
            len(head_lines),
            1,
            msg=f"expected a single Alembic head, got: {head_lines!r}",
        )


class PersistToDatabaseTests(unittest.TestCase):
    """Cover the Review.id flush bug + transactional dedup marker reset."""

    def _make_collector(self):
        from review_scraper.modules.tmall_reviews.collector import ReviewCollector

        return ReviewCollector(task_id="task-persist")

    def setUp(self) -> None:
        import review_scraper.models  # noqa: F401
        from review_scraper.core.database import Base, get_engine

        Base.metadata.create_all(bind=get_engine())

    def tearDown(self) -> None:
        import review_scraper.models  # noqa: F401
        from review_scraper.core.database import Base, get_engine

        engine = get_engine()
        Base.metadata.drop_all(bind=engine)

    def _ingest(self, collector, rows, *, page_number=1, total_pages=1):
        """Push a synthetic payload into the collector and trigger ingest."""
        import json as _json

        from review_scraper.modules.tmall_reviews.collector import ReviewBatch

        payload = {
            "defaultModel": {
                "rateList": [
                    {"auctionNumId": 123456, "rateList": rows},
                ]
            }
        }
        fingerprint = _json.dumps(payload, ensure_ascii=False, sort_keys=True)
        batch = ReviewBatch(
            source_url="https://detail.tmall.com/item.htm?id=123456",
            page_number=page_number,
            total_pages=total_pages,
            review_count=len(rows),
            payload=payload,
            reviews=rows,
            fingerprint=fingerprint,
        )
        # Wire directly into the collector's internal stores — same shape
        # as ReviewCollector._ingest_payload produces.
        collector._batches.append(batch)
        collector._fingerprints.add(fingerprint)
        collector._batch_to_rows[fingerprint] = rows
        for row in rows:
            collector._rows.append(row)

    def test_persist_writes_review_and_association_link(self) -> None:
        """The original bug: ScrapeTaskReview inserted with review_id=None.

        Now the collector must flush so review.id is populated, and the
        scrape_task_reviews row must reference a real review.
        """
        collector = self._make_collector()
        self._ingest(
            collector,
            [
                {
                    "id": "r-1",
                    "displayUserNick": "买家",
                    "rateContent": "非常好",
                    "rateDate": "2026-01-01",
                    "skuInfo": "标准款",
                    "usefulCount": 1,
                }
            ],
        )

        inserted = collector.persist_to_database(
            item_id="123456",
            source_url="https://detail.tmall.com/item.htm?id=123456",
        )
        self.assertEqual(inserted, 1)

        from review_scraper.core.database import get_session
        from review_scraper.models import Review, ScrapeTaskReview

        db = get_session()
        try:
            reviews = db.query(Review).all()
            links = db.query(ScrapeTaskReview).all()
        finally:
            db.close()

        self.assertEqual(len(reviews), 1)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].review_id, reviews[0].id)
        self.assertEqual(links[0].task_id, "task-persist")

    def test_persist_failure_does_not_mark_keys_as_seen(self) -> None:
        """If commit fails, the next call must retry the same rows."""
        from unittest.mock import patch

        from sqlalchemy.orm import Session

        collector = self._make_collector()
        self._ingest(
            collector,
            [
                {
                    "id": "r-new",
                    "displayUserNick": "买家",
                    "rateContent": "一般般",
                    "rateDate": "2026-01-02",
                    "skuInfo": "标准款",
                    "usefulCount": 0,
                }
            ],
        )

        with patch.object(Session, "flush", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                collector.persist_to_database(
                    item_id="123456",
                    source_url="https://detail.tmall.com/item.htm?id=123456",
                )

        # Internal dedup keys must remain empty so a retry can re-attempt.
        self.assertEqual(len(collector._db_keys), 0)
        self.assertEqual(len(collector._persisted_fingerprints), 0)


class LegacyDownloadRouteTests(unittest.TestCase):
    """The UI historically hit ``/download?format=csv``. Confirm both forms resolve."""

    def setUp(self) -> None:
        # Models must be imported so Base.metadata knows about every table
        # before we call create_all — otherwise SQLAlchemy creates nothing.
        import review_scraper.models  # noqa: F401

        from review_scraper.core.database import Base, get_engine

        Base.metadata.create_all(bind=get_engine())
        from fastapi.testclient import TestClient

        from review_scraper.main import app

        self.client = TestClient(app)

    def tearDown(self) -> None:
        import review_scraper.models  # noqa: F401
        from review_scraper.core.database import Base, get_engine

        engine = get_engine()
        Base.metadata.drop_all(bind=engine)

    def _seed_task_with_csv(self) -> str:
        from review_scraper.core.database import get_session
        from review_scraper.models import Product, ScrapeTask
        import tempfile

        tmp = Path(tempfile.gettempdir()) / "review_scraper_legacy_csv.csv"
        tmp.write_text("review_id,content\nr1,ok\n", encoding="utf-8")

        db = get_session()
        try:
            product = Product(
                platform="tmall",
                source_url="https://detail.tmall.com/item.htm?id=legacy",
                normalized_url="https://detail.tmall.com/item.htm?id=legacy",
                external_product_id="legacy",
            )
            db.add(product)
            db.flush()
            task = ScrapeTask(
                task_id="task-legacy",
                product_id=product.id,
                platform="tmall",
                status="done",
                file_paths=str(
                    {
                        "csv_path": str(tmp),
                        "json_path": str(tmp),
                        "xlsx_path": str(tmp),
                    }
                ).replace("'", '"'),
            )
            db.add(task)
            db.commit()
        finally:
            db.close()
        return str(tmp)

    def test_legacy_download_route_returns_csv(self) -> None:
        self._seed_task_with_csv()
        legacy = self.client.get("/api/reviews/tmall/tasks/task-legacy/download?format=csv")
        canonical = self.client.get("/api/reviews/tmall/tasks/task-legacy/artifacts/csv")
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(canonical.status_code, 200)
        self.assertIn(b"review_id", legacy.content)
        self.assertEqual(legacy.content, canonical.content)


if __name__ == "__main__":
    unittest.main()