@echo off
chcp 65001 >nul
REM Lumberture Hub serverini qayta ishga tushirish (yangilanishdan keyin). Ishlayotgan barcha nusxalarni to'xtatadi.
cd /d "%~dp0"
echo Lumberture Hub to'xtatilmoqda...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'serve\.py' -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'start\.bat') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul
echo Ishga tushirilmoqda...
wscript.exe "%~dp0start_hidden.vbs"
echo %date% %time% restart >> "%~dp0data\restart_log.txt"
timeout /t 2 /nobreak >nul
