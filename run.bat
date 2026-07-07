@echo off
REM ============================================================
REM Review Scraper 一键启动脚本（无需管理员权限）
REM
REM 启动顺序：
REM   1. bundled Redis (tools/redis/redis-server.exe)
REM   2. alembic upgrade head
REM   3. Celery worker
REM   4. FastAPI (uvicorn)
REM
REM 关闭请用 stop.bat
REM ============================================================
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
pushd "%ROOT%"

REM ---------- 0. 基础环境 ----------
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run: python -m venv .venv ^&^& .venv\Scripts\pip install -e .
  popd & exit /b 1
)

if not exist "logs" mkdir "logs"

REM 若 .env 缺失则自动创建 SQLite 的最小 .env，方便本地一键跑通
if not exist ".env" (
  echo DATABASE_URL=sqlite:///./data/app.db> .env
  echo REDIS_URL=redis://localhost:6379/0>> .env
  echo [INFO] Created default .env (SQLite + local Redis)
)

REM ---------- 1. Redis ----------
echo [1/4] Checking Redis ...
set "REDIS_EXE=%ROOT%tools\redis\redis-server.exe"
set "REDIS_CONF=%ROOT%tools\redis\redis.windows.conf"
if not exist "%REDIS_EXE%" (
  echo [WARN] Bundled redis-server.exe missing, trying redis-py only
) else (
  REM 检查 6379 是否已被占用
  netstat -ano | findstr :6379 | findstr LISTENING >nul
  if errorlevel 1 (
    echo [INFO] Starting Redis on 127.0.0.1:6379 ...
    start "ReviewCrawler-Redis" /B "%REDIS_EXE%" "%REDIS_CONF%" >> "%ROOT%logs\redis.log" 2>&1
    REM 等 Redis 就绪（最多 10s）
    set "REDIS_READY=0"
    for /L %%i in (1,1,20) do (
      timeout /T 1 /NOBREAK >nul
      call "%ROOT%.venv\Scripts\python.exe" -c "import socket;s=socket.socket();s.settimeout(0.5);s.connect(('127.0.0.1',6379));s.close()" >nul 2>&1 && (
        set "REDIS_READY=1"
        goto :redis_ready
      )
    )
    :redis_ready
    if "!REDIS_READY!"=="1" (
      echo [OK] Redis ready
    ) else (
      echo [WARN] Redis did not respond within 10s; app will fall back to in-memory cache
    )
  ) else (
    echo [OK] Port 6379 already in use, reusing
  )
)

REM ---------- 2. Alembic ----------
echo [2/4] Running alembic upgrade head ...
call "%ROOT%.venv\Scripts\python.exe" -m alembic upgrade head
if errorlevel 1 (
  echo [ERROR] alembic upgrade head failed. See logs\app.log
  popd & exit /b 1
)
echo [OK] DB schema ready

REM ---------- 3. Celery worker ----------
echo [3/4] Starting Celery worker ...
REM 先确保没有遗留 worker 在跑（端口/进程）
taskkill /FI "WINDOWTITLE eq ReviewCrawler-Celery*" /T /F >nul 2>&1
start "ReviewCrawler-Celery" /B cmd /c "call \"%ROOT%.venv\Scripts\activate.bat\" && celery -A review_scraper.workers.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q scrape,default >> \"%ROOT%logs\celery_worker.log\" 2>&1"
echo [OK] Celery worker launched

REM ---------- 4. FastAPI ----------
echo [4/4] Starting FastAPI on http://127.0.0.1:8000 ...
REM 若端口已被占用就停掉
netstat -ano | findstr :8000 | findstr LISTENING >nul
if not errorlevel 1 (
  echo [WARN] Port 8000 in use; killing old uvicorn
  for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%P /F >nul 2>&1
)

start "ReviewCrawler-API" /B cmd /c "call \"%ROOT%.venv\Scripts\activate.bat\" && python -m uvicorn review_scraper.main:app --host 127.0.0.1 --port 8000 >> \"%ROOT%logs\app.log\" 2>&1"

REM 等待端口起来再开浏览器（最多 15s）
set "API_READY=0"
for /L %%i in (1,1,30) do (
  timeout /T 1 /NOBREAK >nul
  netstat -ano | findstr :8000 | findstr LISTENING >nul && (
    set "API_READY=1"
    goto :api_ready
  )
)
:api_ready
if "!API_READY!"=="1" (
  echo [OK] API ready
  start "" http://127.0.0.1:8000/
) else (
  echo [ERROR] API did not start within 15s. Check logs\app.log
  popd & exit /b 1
)

popd
endlocal
exit /b 0