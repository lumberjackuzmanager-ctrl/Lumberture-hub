@echo off
chcp 65001 >nul
REM Lumberture Hub: Windows ochilganda platforma o'zi ishga tushsin (Startup papkasiga yorliq).
schtasks /Query /TN LumbertureHub >nul 2>&1 && (echo Avtozapusk allaqachon bor - Planirovshik zadach. Hech narsa qilinmadi. & pause & exit /b)
schtasks /Query /TN RentPlatform >nul 2>&1 && (echo Avtozapusk allaqachon bor - Planirovshik zadach. Hech narsa qilinmadi. & pause & exit /b)
set "D=%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\LumbertureHub.lnk');$s.TargetPath='%D%start_hidden.vbs';$s.WorkingDirectory='%D:~0,-1%';$s.Save()"
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LumbertureHub.lnk" (echo TAYYOR: kompyuter yonib Windows ochilganda platforma o'zi ishga tushadi.) else (echo XATO: yorliq yaratilmadi.)
pause
