@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==== Python ====
python --version || goto :fail
echo.

echo ==== Doctor ====
python -m mclite doctor || echo [WARN] doctor reported problems
echo.

echo ==== Unit tests ====
python -m unittest discover -s tests
echo.

echo ==== Done ====
echo If the GUI fails, send: %APPDATA%\MCLite\logs\mclite.log
pause
exit /b 0

:fail
echo Python not found. Install Python 3.10+ and enable "Add python.exe to PATH".
pause
exit /b 1
