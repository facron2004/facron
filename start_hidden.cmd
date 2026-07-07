@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "LOG=%ROOT%start.log"

call "%ROOT%.venv\Scripts\activate.bat" >> "%LOG%" 2>&1
python -c "from review_scraper.core.database import init_db; init_db()" >> "%LOG%" 2>&1

start "Redis - Review Crawler" /min cmd /c "cd /d "%ROOT%" && if exist tools\redis\redis-server.exe (tools\redis\redis-server.exe tools\redis\redis.conf)"
start "Celery Worker - Review Crawler" /min cmd /c "cd /d "%ROOT%" && call .venv\Scripts\activate.bat && celery -A review_scraper.workers.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q scrape,default >> logs\celery_worker.log 2>&1"

python -m uvicorn review_scraper.main:app --host 127.0.0.1 --port 8000 >> "%LOG%" 2>&1
