@echo off
echo Starting Review Scraper development server...
cd /d "%~dp0\.."
uvicorn review_scraper.main:app --reload --host 0.0.0.0 --port 8000
