@echo off
REM Build one-file Windows executable.
REM Requires: python -m pip install pyinstaller pyahocorasick

setlocal

if not exist .venv (
    echo Creating venv...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

pyinstaller ^
    --noconfirm ^
    --onefile ^
    --noconsole ^
    --name FilterGUI ^
    --add-data "countries.py;." ^
    filter_gui.py

echo.
echo Done. Executable: dist\FilterGUI.exe
endlocal
