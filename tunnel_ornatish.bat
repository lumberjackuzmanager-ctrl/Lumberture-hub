@echo off
chcp 65001 >nul
REM Cloudflare Tunnel: app.lumberture.com -> shu kompyuterdagi platforma (localhost:8081). Routerda port ochilmaydi.
REM Tashqaridan faqat /api/online/* ochiq — qolgan sahifalarni platformaning o'zi yopadi (404).
REM Ishga tushirish: o'ng tugma -> "Запуск от имени администратора".
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Administrator sifatida ishga tushiring: fayl ustida o'ng tugma -^> "Запуск от имени администратора".
  pause
  exit /b 1
)
if not exist "tools" mkdir "tools"
if not exist "data" mkdir "data"
set LOG=data\tunnel_install.log
echo %date% %time% start > "%LOG%"
if not exist "tools\cloudflared.exe" (
  echo cloudflared yuklanmoqda...
  curl -L --fail -o "tools\cloudflared.exe" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo  Yuklab bo'lmadi. Internetni tekshiring. Batafsil: %LOG%
    pause
    exit /b 1
  )
)
"tools\cloudflared.exe" --version >> "%LOG%" 2>&1
echo.
echo  Cloudflare'dagi tunnel tokenini shu yerga qo'ying (Zero Trust -^> Networks -^> Tunnels -^> tunnel -^> Windows buyrug'idagi uzun matn).
echo  Butun buyruqni qo'ysangiz ham bo'ladi — token o'zi ajratib olinadi.
set /p TOK=Token: 
for %%a in (%TOK%) do set LAST=%%a
if "%LAST%"=="" (
  echo  Token kiritilmadi.
  pause
  exit /b 1
)
"tools\cloudflared.exe" service uninstall >> "%LOG%" 2>&1
"tools\cloudflared.exe" service install %LAST% >> "%LOG%" 2>&1
if errorlevel 1 (
  echo  Xizmat o'rnatilmadi. Batafsil: %LOG%
  pause
  exit /b 1
)
sc config cloudflared start= auto >> "%LOG%" 2>&1
sc start cloudflared >> "%LOG%" 2>&1
set TOK=
set LAST=
echo exit=0 >> "%LOG%"
echo.
echo  Tayyor: tunnel Windows xizmati sifatida o'rnatildi (kompyuter yonganda o'zi ishga tushadi).
echo  Tekshirish: telefonda https://app.lumberture.com/api/online/catalog oching — JSON chiqishi kerak.
pause
