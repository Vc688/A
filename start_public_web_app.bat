@echo off
setlocal
cd /d "%~dp0"

set "BACKEND_DIR=%CD%\cloud_backend"
set "VENV_DIR=%BACKEND_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "PY311=C:\Users\vctg6\AppData\Local\Programs\Python\Python311\python.exe"
set "CLOUDFLARED_EXE=%CD%\cloudflared.exe"
set "SERVER_LOG=%CD%\public_server.log"
set "TUNNEL_LOG=%CD%\public_tunnel.log"

if not exist "%BACKEND_DIR%\.env" if exist "%BACKEND_DIR%\.env.example" copy /Y "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul

if not exist "%VENV_PY%" (
  if not exist "%PY311%" (
    echo Python 3.11 was not found at %PY311%.
    pause
    exit /b 1
  )
  "%PY311%" -m venv "%VENV_DIR%"
  if %errorlevel% neq 0 goto :fail
)

"%VENV_PY%" -m pip install --upgrade pip
if %errorlevel% neq 0 goto :fail
"%VENV_PY%" -m pip install -r "%BACKEND_DIR%\requirements.txt"
if %errorlevel% neq 0 goto :fail

if not exist "%CLOUDFLARED_EXE%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '%CLOUDFLARED_EXE%'"
  if %errorlevel% neq 0 goto :fail
)

del /q "%SERVER_LOG%" "%TUNNEL_LOG%" 2>nul

start "Torah Center Web Server" /min cmd /c ""%VENV_PY%" "%BACKEND_DIR%\serve_waitress.py" > "%SERVER_LOG%" 2>&1"
timeout /t 4 /nobreak >nul
start "Torah Center Public Tunnel" cmd /c ""%CLOUDFLARED_EXE%" tunnel --url http://127.0.0.1:8010 --no-autoupdate > "%TUNNEL_LOG%" 2>&1"

echo.
echo Torah Center public startup has begun.
echo Server log: %SERVER_LOG%
echo Tunnel log: %TUNNEL_LOG%
echo Wait a few seconds, then open %TUNNEL_LOG% to see the public URL.
echo.
pause
exit /b 0

:fail
echo.
echo Public web app startup failed. Read the errors above.
pause
exit /b 1
