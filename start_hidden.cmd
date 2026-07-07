@echo off
REM 静默启动（无终端闪动，等价于 start.bat 但不打开浏览器）
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
pushd "%ROOT%"

if not exist ".env" (
  echo DATABASE_URL=sqlite:///./data/app.db> .env
  echo REDIS_URL=redis://localhost:6379/0>> .env
)

REM 1) Redis
set "REDIS_EXE=%ROOT%tools\redis\redis-server.exe"
set "REDIS_CONF=%ROOT%tools\redis\redis.windows.conf"
if exist "%REDIS_EXE%" (
  netstat -ano | findstr :6379 | findstr LISTENING >nul
  if errorlevel 1 (
    start "ReviewCrawler-Redis" /B "%REDIS_EXE%" "%REDIS_CONF%" >> "%ROOT%logs\redis.log" 2>&1
    timeout /T 3 /NOBREAK >nul
  )
)

REM 2) alembic
call "%ROOT%.venv\Scripts\python.exe" -m alembic upgrade head >nul 2>&1
if errorlevel 1 (
  echo [ERROR] alembic upgrade head failed
  popd & exit /b 1
)

REM 3) Celery
start "ReviewCrawler-Celery" /B cmd /c "call \"%ROOT%.venv\Scripts\activate.bat\" && celery -A review_scraper.workers.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q scrape,default >> \"%ROOT%logs\celery_worker.log\" 2>&1"

REM 4) FastAPI
start "ReviewCrawler-API" /B cmd /c "call \"%ROOT%.venv\Scripts\activate.bat\" && python -m uvicorn review_scraper.main:app --host 127.0.0.1 --port 8000 >> \"%ROOT%logs\app.log\" 2>&1"

timeout /T 6 /NOBREAK >nul
popd
endlocal
exit /b 0