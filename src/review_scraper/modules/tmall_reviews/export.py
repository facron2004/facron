"""Tmall review export — CSV, Excel, and JSON file export."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from .collector import ReviewBatch


def export_reviews(
    rows: list[dict[str, Any]],
    batches: list[ReviewBatch],
    output_dir: Path,
    item_id: str,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    stem = f"tmall_reviews_{item_id}_{timestamp}"

    csv_path = output_dir / f"{stem}.csv"
    xlsx_path = output_dir / f"{stem}.xlsx"
    json_path = output_dir / f"{stem}.json"

    columns = [
        "review_id", "user_nick", "review_date", "sku", "content",
        "append_content", "append_date", "helpful_count",
        "page_number", "total_pages", "picture_urls", "source_url",
    ]

    _write_csv(csv_path, rows, columns)
    _write_xlsx(xlsx_path, rows, batches, columns)
    _write_json(json_path, rows, batches)

    return csv_path, xlsx_path, json_path


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_xlsx(path: Path, rows: list[dict[str, Any]], batches: list[ReviewBatch], columns: list[str]) -> None:
    try:
        import pandas as pd
    except ImportError:
        return
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="reviews", index=False)
        pd.DataFrame(
            [
                {
                    "batch_index": idx,
                    "page_number": b.page_number,
                    "total_pages": b.total_pages,
                    "review_count": b.review_count,
                    "source_url": b.source_url,
                }
                for idx, b in enumerate(batches, start=1)
            ]
        ).to_excel(writer, sheet_name="batches", index=False)


def _write_json(path: Path, rows: list[dict[str, Any]], batches: list[ReviewBatch]) -> None:
    path.write_text(
        json.dumps(
            {
                "review_count": len(rows),
                "batch_count": len(batches),
                "reviews": rows,
                "batches": [
                    {
                        "page_number": b.page_number,
                        "total_pages": b.total_pages,
                        "review_count": b.review_count,
                        "source_url": b.source_url,
                        "payload": b.payload,
                    }
                    for b in batches
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
