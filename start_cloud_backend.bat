@echo off
setlocal
cd /d "%~dp0\cloud_backend"

if not exist ".env" if exist ".env.example" copy /Y ".env.example" ".env" >nul

set "PY311=C:\Users\vctg6\AppData\Local\Programs\Python\Python311\python.exe"
set "VENV_DIR=%CD%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if exist "%PY311%" (
  if not exist "%VENV_PY%" (
    "%PY311%" -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 goto :fail
  )
  "%VENV_PY%" -m pip install --upgrade pip
  if %errorlevel% neq 0 goto :fail
  "%VENV_PY%" -m pip install -r requirements.txt
  if %errorlevel% neq 0 goto :fail
  "%VENV_PY%" app.py
  goto :end
)

where py >nul 2>&1
if %errorlevel%==0 (
  if not exist "%VENV_PY%" (
    py -3.11 -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 goto :fail
  )
  "%VENV_PY%" -m pip install --upgrade pip
  if %errorlevel% neq 0 goto :fail
  "%VENV_PY%" -m pip install -r requirements.txt
  if %errorlevel% neq 0 goto :fail
  "%VENV_PY%" app.py
  goto :end
)

where python >nul 2>&1
if %errorlevel%==0 (
  if not exist "%VENV_PY%" (
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 goto :fail
  )
  "%VENV_PY%" -m pip install --upgrade pip
  if %errorlevel% neq 0 goto :fail
  "%VENV_PY%" -m pip install -r requirements.txt
  if %errorlevel% neq 0 goto :fail
  "%VENV_PY%" app.py
  goto :end
)

echo Python 3.11 was not found. Install Python 3.11 or update this script to point at the correct interpreter.
pause
exit /b 1

:fail
echo.
echo Cloud backend startup failed. Read the errors above.
pause
exit /b 1

:end
pause
