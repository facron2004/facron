@echo off
REM 关闭所有 ReviewCrawler-* 启动的子进程窗口和对应 PID
setlocal

echo Stopping API ...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
  taskkill /PID %%P /F >nul 2>&1
)

echo Stopping Celery worker ...
taskkill /FI "WINDOWTITLE eq ReviewCrawler-Celery*" /T /F >nul 2>&1
taskkill /IM celery.exe /F >nul 2>&1

echo Stopping Redis ...
taskkill /FI "WINDOWTITLE eq ReviewCrawler-Redis*" /T /F >nul 2>&1
taskkill /IM redis-server.exe /F >nul 2>&1

echo [OK] All services stopped
endlocal
exit /b 0