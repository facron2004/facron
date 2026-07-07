@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\activate.bat" exit /b 1

schtasks /Create /TN "ReviewCrawler-Redis" /SC ONSTART /RL HIGHEST /F /TR "\"%ROOT%tools\redis\redis-server.exe\" \"%ROOT%tools\redis\redis.windows-service.conf\"" >nul
schtasks /Create /TN "ReviewCrawler-Celery" /SC ONSTART /RL HIGHEST /F /TR "cmd /c \"cd /d %ROOT% && call .venv\Scripts\activate.bat && celery -A review_scraper.workers.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q scrape,default >> logs\celery_worker.log 2>&1\"" >nul
schtasks /Create /TN "ReviewCrawler-API" /SC ONSTART /RL HIGHEST /F /TR "cmd /c \"cd /d %ROOT% && call .venv\Scripts\activate.bat && python -m uvicorn review_scraper.main:app --host 127.0.0.1 --port 8000 >> logs\api.log 2>&1\"" >nul
exit /b 0
