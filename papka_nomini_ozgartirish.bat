@echo off
chcp 65001 >nul
REM Platforma papkasini LumbertureHub deb qayta nomlash (server to'xtatiladi, avtozapusk yangilanadi, qayta ishga tushadi).
copy /y "%~dp0tools\papka_nomi.ps1" "%TEMP%\lh_papka_nomi.ps1" >nul
set "SRC=%~dp0"
start "Lumberture Hub" /d "%TEMP%" powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\lh_papka_nomi.ps1" -Old "%SRC:~0,-1%"
exit /b
