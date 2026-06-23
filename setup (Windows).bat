@echo off
echo ====================================================
echo   Tag2Tune Environment Setup (Windows)
echo ====================================================

:: Verify Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python was not found! Please install Python 3.10+ and check "Add to PATH".
    pause
    exit /b
)

:: Create virtual environment if missing
if not exist venv (
    echo [*] Creating virtual environment (venv)...
    python -m venv venv
)

:: Activate environment and install dependencies
echo [*] Activating virtual environment...
call venv\Scripts\activate

echo [*] Upgrading pip and installing requirements...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ====================================================
echo [✓] Success! Virtual environment is fully configured.
echo [*] To start the app, run: python Tag2Tune.py
echo ====================================================
pause