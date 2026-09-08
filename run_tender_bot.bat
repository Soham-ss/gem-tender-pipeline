@echo off
echo ===================================================
echo   CRYOCORP TENDER BOT - RUNNING LIVE GEM SYNC
echo ===================================================
echo.

python pipeline.py --keywords "cooling tower" "oxygen" "cryogenic"

echo.
echo ===================================================
echo   SYNC FINISHED! Check your Google Sheet.
echo ===================================================
pause
