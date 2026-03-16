@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
  py -3 -m pip install -r requirements.txt
  if %errorlevel% neq 0 goto :fail
  py -3 app.py
  goto :end
)

where python >nul 2>&1
if %errorlevel%==0 (
  python -m pip install -r requirements.txt
  if %errorlevel% neq 0 goto :fail
  python app.py
  goto :end
)

echo Python was not found on PATH.
echo Please reinstall Python and check "Add Python to PATH".
pause
exit /b 1

:fail
echo.
echo Startup failed. Read the errors above.
pause
exit /b 1

:end
pause
