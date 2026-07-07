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

REM 3) Celery — write a tiny launcher .cmd and run it via start /B so we
REM    avoid the cmd /c nesting-quote hell (%ROOT% has trailing \ and a
REM    space, which breaks the original "cmd /c \"...\" && ..." pattern).
set "CB=%ROOT%scripts\_run_celery.cmd"
> "%CB%" echo @echo off
>>"%CB%" echo setlocal
>>"%CB%" echo call "%ROOT%.venv\Scripts\activate.bat"
>>"%CB%" echo celery -A review_scraper.workers.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q scrape,default
>>"%CB%" echo exit /b 0
start "ReviewCrawler-Celery" /B "%CB%"

REM 4) FastAPI — same pattern: launcher .cmd keeps the activation
REM    call inside the child context where quoting is unambiguous.
set "AB=%ROOT%scripts\_run_api.cmd"
> "%AB%" echo @echo off
>>"%AB%" echo setlocal
>>"%AB%" echo call "%ROOT%.venv\Scripts\activate.bat"
>>"%AB%" echo python -m uvicorn review_scraper.main:app --host 127.0.0.1 --port 8000
>>"%AB%" echo exit /b 0
start "ReviewCrawler-API" /B "%AB%"

timeout /T 6 /NOBREAK >nul
popd
endlocal
exit /b 0