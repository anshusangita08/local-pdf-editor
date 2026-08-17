@echo off
setlocal

echo ==============================================
echo  Local PDF Editor - Setup
echo ==============================================

REM --- Create virtual environment ---
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment. Make sure Python is installed and on PATH.
        exit /b 1
    )
) else (
    echo Virtual environment already exists, skipping creation.
)

REM --- Activate virtual environment ---
call "%~dp0venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate virtual environment.
    exit /b 1
)

REM --- Install Python dependencies ---
echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing required packages: fastapi, uvicorn, pymupdf, python-multipart, pytesseract, Pillow...
pip install fastapi uvicorn pymupdf python-multipart pytesseract Pillow
if errorlevel 1 (
    echo Failed to install one or more required packages.
    exit /b 1
)

echo Freezing dependencies to requirements.txt...
pip freeze > requirements.txt

REM --- Install Tesseract OCR (best-effort, needed for later milestones) ---
echo Installing Tesseract OCR via winget (this may prompt or take a moment)...
where winget >nul 2>nul
if errorlevel 1 (
    echo winget not found on this system. Skipping Tesseract install.
    echo You can install it manually later from https://github.com/UB-Mannheim/tesseract/wiki
) else (
    winget install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo Tesseract install via winget did not complete successfully.
        echo You can retry later with: winget install -e --id UB-Mannheim.TesseractOCR
    )
)

REM --- Create required folders ---
if not exist temp mkdir temp
if not exist static\vendor mkdir static\vendor

REM --- Download local (offline) copies of the frontend libraries so the ---
REM --- app never needs to reach a CDN at runtime. ---
echo Downloading Tailwind CSS standalone script...
curl -L -o "static\vendor\tailwindcss.js" "https://cdn.tailwindcss.com"
if errorlevel 1 (
    echo Failed to download Tailwind CSS. Check your internet connection and retry setup.bat.
)

echo Downloading Fabric.js v5.3.0...
curl -L -o "static\vendor\fabric.min.js" "https://cdn.jsdelivr.net/npm/fabric@5.3.0/dist/fabric.min.js"
if errorlevel 1 (
    echo Failed to download Fabric.js. Check your internet connection and retry setup.bat.
)

echo.
echo ==============================================
echo  Setup complete. Run start.bat to launch the app.
echo ==============================================

endlocal
