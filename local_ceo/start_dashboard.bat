@echo off
REM MAXIA — CEO Local Dashboard launcher
REM Uses the same Python interpreter as ceo_main.py to guarantee httpx + deps.
REM If you move Python, update the PYTHON_EXE variable below.

set "PYTHON_EXE=C:\Users\Mini pc\AppData\Local\Programs\Python\Python312\python.exe"
set "DASHBOARD_PY=%~dp0dashboard.py"

if not exist "%PYTHON_EXE%" (
  echo [start_dashboard] ERROR: Python not found at:
  echo   %PYTHON_EXE%
  echo Edit start_dashboard.bat and fix the PYTHON_EXE line.
  pause
  exit /b 1
)

if not exist "%DASHBOARD_PY%" (
  echo [start_dashboard] ERROR: dashboard.py not found at:
  echo   %DASHBOARD_PY%
  pause
  exit /b 1
)

echo [start_dashboard] Using Python: %PYTHON_EXE%
echo [start_dashboard] Running:      %DASHBOARD_PY%
echo.
"%PYTHON_EXE%" "%DASHBOARD_PY%"
pause
