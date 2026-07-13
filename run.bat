@echo off
title NYC Violations Scanner

:menu
cls
echo.
echo ============================================
echo   NYC VIOLATIONS MANAGEMENT SYSTEM
echo ============================================
echo.
echo   1. Run HPD scan only
echo   2. Run ECB scan (CityPay) only
echo   3. Run ALL scans (HPD + ECB)
echo   4. Sync existing violations to Airtable
echo   5. Install / update required tools
echo   6. Exit
echo.
set /p choice="Enter your choice (1-6): "

if "%choice%"=="1" goto hpd
if "%choice%"=="2" goto ecb
if "%choice%"=="3" goto all
if "%choice%"=="4" goto airtable
if "%choice%"=="5" goto install
if "%choice%"=="6" goto end
echo Invalid choice. Please try again.
timeout /t 2 >nul
goto menu

:hpd
cls
echo.
echo ============================================
echo  Running HPD scanner...
echo ============================================
python hpd_scanner.py
echo.
echo Done! Check your email for the HPD report.
pause
goto menu

:ecb
cls
echo.
echo ============================================
echo  Running ECB scanner (CityPay)...
echo ============================================
python live_scanner.py
echo.
echo Done! Check your email for the ECB report.
pause
goto menu

:all
cls
echo.
echo ============================================
echo  STEP 1: Running HPD scanner...
echo ============================================
python hpd_scanner.py
echo.
echo ============================================
echo  STEP 2: Running ECB scanner (CityPay)...
echo ============================================
python live_scanner.py
echo.
echo All done! Check your email for both reports.
pause
goto menu

:airtable
cls
echo.
echo ============================================
echo  Syncing violations to Airtable...
echo ============================================
python import_to_airtable.py
echo.
pause
goto menu

:install
cls
echo.
echo Installing required tools...
python -m pip install --quiet requests pandas python-dotenv playwright
python -m playwright install chromium
echo Done!
pause
goto menu

:end
exit