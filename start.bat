@echo off
REM ============================================================
REM  Lumberture Hub - zapusk na Windows 10/11
REM  1) sozdaet .venv pri pervom zapuske  2) stavit zavisimosti
REM  3) zapuskaet server waitress na 127.0.0.1:8081 (tolko etot PK)
REM ============================================================
setlocal
cd /d "%~dp0"
if "%RENT_PORT%"=="" set RENT_PORT=8081
REM Ofis seti: server 0.0.0.0 da (2026-10-14). Kirish faqat Bezopasnost - Dostup po IP dagi adreslardan.
REM Chtoby otkryt dostup kompyuteram v seti ofisa: zamenite 127.0.0.1 na 0.0.0.0 nizhe,
REM vklyuchite pravilo brandmauera "Rent 8081" i dobavte adresa v programme: Bezopasnost - Dostup po IP (README, razdel 13.3).
if "%HOST%"=="" set HOST=0.0.0.0
title Lumberture Hub - port %RENT_PORT%
REM Server uzhe zapushchen? Vtoroy ne nuzhen (dva servera meshayut drug drugu).
netstat -ano | findstr /r /c:":%RENT_PORT% .*LISTENING" >nul && (echo Lumberture Hub uzhe rabotaet na portu %RENT_PORT%. & exit /b 0)

if exist ".venv\Scripts\python.exe" goto deps

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during installation.
  pause
  exit /b 1
)
echo Creating virtual environment...
%PY% -m venv .venv
if errorlevel 1 (
  echo [ERROR] Could not create .venv
  pause
  exit /b 1
)

:deps
if exist ".venv\installed.ok" (
  fc /b requirements.txt ".venv\installed.ok" >nul 2>nul && goto run
)
echo Installing requirements (needs internet only the first time)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed. Check internet connection and run start.bat again.
  pause
  exit /b 1
)
copy /y requirements.txt ".venv\installed.ok" >nul

:run
if not exist data mkdir data
".venv\Scripts\python.exe" serve.py
echo.
echo Server stopped.
pause
