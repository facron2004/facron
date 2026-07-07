@echo off
setlocal
if not exist "%~dp0service_check.bat" exit /b 1
call "%~dp0service_check.bat" >nul 2>&1
if errorlevel 1 (
  call "%~dp0service_install.bat" >nul 2>&1
)
schtasks /Run /TN "ReviewCrawler-Redis" >nul 2>&1
schtasks /Run /TN "ReviewCrawler-Celery" >nul 2>&1
schtasks /Run /TN "ReviewCrawler-API" >nul 2>&1
start "" http://127.0.0.1:8000/
exit /b 0
