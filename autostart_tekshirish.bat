@echo off
chcp 65001 >nul
set "OUT=%~dp0data\autostart_check.txt"
echo === schtasks > "%OUT%"
schtasks /Query /TN LumbertureHub /V /FO LIST >> "%OUT%" 2>&1
schtasks /Query /TN RentPlatform /V /FO LIST >> "%OUT%" 2>&1
echo === startup >> "%OUT%"
dir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup" >> "%OUT%" 2>&1
dir "%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp" >> "%OUT%" 2>&1
echo === autologon >> "%OUT%"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon >> "%OUT%" 2>&1
echo Tayyor. Oynani yoping.
pause
