@echo off
schtasks /End /TN "ReviewCrawler-API" >nul 2>&1
schtasks /End /TN "ReviewCrawler-Celery" >nul 2>&1
schtasks /End /TN "ReviewCrawler-Redis" >nul 2>&1
exit /b 0
