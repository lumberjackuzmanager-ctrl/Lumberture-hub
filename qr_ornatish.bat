@echo off
chcp 65001 >nul
REM QR o'qish kutubxonasi (OpenCV) — alohida o'rnatish, natija: data\pip_qr.log
cd /d "%~dp0"
echo %date% %time% start > "data\pip_qr.log"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check opencv-python-headless >> "data\pip_qr.log" 2>&1
echo exit=%errorlevel% >> "data\pip_qr.log"
".venv\Scripts\python.exe" -c "import cv2;print('cv2', cv2.__version__)" >> "data\pip_qr.log" 2>&1
