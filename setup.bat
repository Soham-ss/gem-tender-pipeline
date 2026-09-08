@echo off
echo ===================================================
echo   CRYOCORP TENDER BOT - 1-CLICK ENVIRONMENT SETUP
echo ===================================================
echo.

echo 1. Installing Python libraries...
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install pip requirements. Make sure Python is installed and added to PATH.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo 2. Installing Playwright Chromium browser...
python -m playwright install chromium
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install Playwright browser.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo   SETUP COMPLETE! You can now run run_tender_bot.bat
echo ===================================================
pause
