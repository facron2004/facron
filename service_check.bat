@echo off
setlocal
schtasks /Query /TN "ReviewCrawler-Redis" >nul 2>&1 || exit /b 1
schtasks /Query /TN "ReviewCrawler-Celery" >nul 2>&1 || exit /b 1
schtasks /Query /TN "ReviewCrawler-API" >nul 2>&1 || exit /b 1
exit /b 0
