@echo off
cd /d "%~dp0"
python -m pip install -r requirements-build.txt || goto :error
python -m PyInstaller --noconfirm --clean --onefile --windowed --name MCLite --version-file version-info.txt --add-data "LICENSE;." --add-data "LICENSE-APPLICATION.md;." --add-data "licenses;licenses" run.pyw || goto :error
echo.
echo Built: dist\MCLite.exe
goto :eof
:error
echo Build failed.
exit /b 1
