#!/bin/bash
echo "Starting Review Scraper development server..."
cd "$(dirname "$0")/.."
uvicorn review_scraper.main:app --reload --host 0.0.0.0 --port 8000
