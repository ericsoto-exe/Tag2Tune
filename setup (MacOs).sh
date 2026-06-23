#!/bin/bash
echo "===================================================="
echo "  Tag2Tune Environment Setup (Linux / macOS)"
echo "===================================================="

# Verify Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "[!] Python3 was not found! Please install Python 3.10+ first."
    exit 1
fi

# Linux-Specific Edge Case: Check and install Tkinter system package
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "[*] Linux system detected. Checking for Tkinter dependency..."
    if ! python3 -c "import tkinter" &> /dev/null; then
        echo "[!] Tkinter framework missing. Triggering system package install..."
        if command -v apt-get &> /dev/null; then
            sudo apt-get update && sudo apt-get install -y python3-tk
        elif command -v dnf &> /dev/null; then
            sudo dnf install -y python3-tkinter
        else
            echo "[!] Package manager not recognized. Please install your distribution's 'python3-tk' package manually."
        fi
    else
        echo "[✓] Tkinter system framework is already present."
    fi
fi

# Create virtual environment if missing
if [ ! -d "venv" ]; then
    echo "[*] Creating virtual environment (venv)..."
    python3 -m venv venv
fi

# Activate environment and install dependencies
echo "[*] Activating virtual environment..."
source venv/bin/activate

echo "[*] Upgrading pip and installing requirements..."
python3 -m pip install --upgrade pip
pip install -r requirements.txt

echo "===================================================="
echo "[✓] Success! Virtual environment is fully configured."
echo [*] To start the app, run: python Tag2Tune.py
echo "===================================================="