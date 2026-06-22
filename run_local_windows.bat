@echo off
setlocal

cd /d "%~dp0"

echo Starting Data Security Local...

where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 --version >nul 2>nul
  if %errorlevel%==0 (
    set PYTHON_CMD=py -3.11
  ) else (
    py -3.12 --version >nul 2>nul
    if %errorlevel%==0 (
      set PYTHON_CMD=py -3.12
    )
  )
) else (
  where python3.11 >nul 2>nul
  if %errorlevel%==0 (
    set PYTHON_CMD=python3.11
  )
  where python3.12 >nul 2>nul
  if %errorlevel%==0 if "%PYTHON_CMD%"=="" (
    set PYTHON_CMD=python3.12
  )
)

if "%PYTHON_CMD%"=="" (
  echo Python 3.11 or 3.12 is required. Presidio does not support Python 3.14 yet.
  pause
  exit /b 1
)

if not exist ".venv_local\Scripts\python.exe" (
  echo Creating local Python environment...
  %PYTHON_CMD% -m venv .venv_local
)

call ".venv_local\Scripts\activate.bat"

echo Installing/updating app dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

where tesseract >nul 2>nul
if not %errorlevel%==0 (
  echo.
  echo Note: Tesseract OCR is not installed.
  echo PDF text redaction can still run, but scanned images/PDF OCR may not work.
  echo On Windows, install Tesseract OCR and add it to PATH.
  echo.
)

echo Opening local desktop app
python desktop_app.py

pause
