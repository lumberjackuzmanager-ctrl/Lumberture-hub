param([string]$Old)
# Lumberture Hub: platforma papkasini qayta nomlash. papka_nomini_ozgartirish.bat ishga tushiradi (TEMP dan, papka band bo'lmasligi uchun).
$ErrorActionPreference = 'Continue'
$log = Join-Path $env:TEMP 'lh_papka_nomi.log'
function L($m) { $t = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m; Add-Content -Path $log -Value $t -Encoding UTF8; Write-Host $m }
$Old = $Old.TrimEnd('\')
$New = Join-Path (Split-Path $Old -Parent) 'LumbertureHub'
L "Papka: $Old -> $New"
if ($Old -eq $New) { L 'Nomi allaqachon LumbertureHub.'; exit 0 }
if (Test-Path $New) { L "XATO: $New allaqachon bor. Hech narsa qilinmadi."; Read-Host 'Enter'; exit 1 }
Set-Location $env:TEMP
L 'Server to''xtatilmoqda...'
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'serve\.py' -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'start\.bat') } |
  ForEach-Object { L ("  stop " + $_.ProcessId + " " + $_.Name); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$ok = $false
for ($i = 0; $i -lt 3 -and -not $ok; $i++) {
  Start-Sleep -Seconds 2
  try { Rename-Item -LiteralPath $Old -NewName 'LumbertureHub' -ErrorAction Stop; $ok = $true } catch { L ("  band: " + $_.Exception.Message) }
}
if ($ok) { L 'Papka qayta nomlandi.' }
else {
  # papka band (masalan, Claude ilovasi unga ulangan) — nusxa ko'chiramiz, eski papka faqat yangi joyga yo'naltiradi
  L 'Papka band — yangi papkaga nusxa ko''chirilmoqda...'
  robocopy $Old $New /E /COPY:DAT /R:2 /W:1 /XD __pycache__ /NFL /NDL /NJH /NP | Out-Null
  $rc = $LASTEXITCODE
  $a = Get-Item (Join-Path $Old 'data\rent.db') -ErrorAction SilentlyContinue
  $b = Get-Item (Join-Path $New 'data\rent.db') -ErrorAction SilentlyContinue
  if ($rc -ge 8 -or -not $b -or $a.Length -ne $b.Length) {
    L "XATO: nusxa to'liq emas (robocopy $rc). Eski joyida qayta ishga tushirildi."
    Start-Process wscript.exe -ArgumentList ('"' + $Old + '\start_hidden.vbs"')
    Read-Host 'Enter'; exit 1
  }
  L "Nusxa tayyor (robocopy $rc)."
  $fw = "@echo off`r`nREM Platforma ko'chirildi: $New`r`ncall `"$New\start.bat`"`r`n"
  Set-Content -LiteralPath (Join-Path $Old 'start.bat') -Value $fw -Encoding ASCII
  $vb = "' Platforma ko'chirildi: $New`r`nCreateObject(`"WScript.Shell`").Run `"wscript.exe `"`"$New\start_hidden.vbs`"`"`", 0, False`r`n"
  Set-Content -LiteralPath (Join-Path $Old 'start_hidden.vbs') -Value $vb -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $Old 'ESKI_PAPKA_ishlatilmaydi.txt') -Value "Platforma endi $New papkasida ishlaydi. Bu papka eski nusxa: tekshirib bo'lgach o'chirish mumkin." -Encoding UTF8
  L 'Eski papka yangi joyga yo''naltirildi.'
}
$sh = New-Object -ComObject WScript.Shell
# Startup va ish stolidagi yorliqlar
$dirs = @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup'), [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'))
foreach ($d in $dirs) {
  if (-not $d -or -not (Test-Path $d)) { continue }
  Get-ChildItem -LiteralPath $d -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      $l = $sh.CreateShortcut($_.FullName); $ch = $false
      foreach ($p in 'TargetPath', 'Arguments', 'WorkingDirectory', 'IconLocation') {
        $v = $l.$p
        if ($v -and $v.IndexOf($Old, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $l.$p = $v -ireplace [regex]::Escape($Old), $New; $ch = $true }
      }
      if ($ch) {
        $l.Save(); L ("  yorliq yangilandi: " + $_.FullName)
        if ($_.BaseName -eq 'RentPlatform') { Rename-Item -LiteralPath $_.FullName -NewName 'LumbertureHub.lnk' -ErrorAction SilentlyContinue }
      }
    } catch { L ("  yorliq: " + $_.Exception.Message) }
  }
}
# Planirovshik zadach
foreach ($tn in 'RentPlatform', 'LumbertureHub') {
  $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
  if ($t) {
    try {
      $acts = @(); foreach ($a in $t.Actions) {
        $acts += New-ScheduledTaskAction -Execute ($a.Execute -ireplace [regex]::Escape($Old), $New) -Argument (('' + $a.Arguments) -ireplace [regex]::Escape($Old), $New) -WorkingDirectory (('' + $a.WorkingDirectory) -ireplace [regex]::Escape($Old), $New)
      }
      Set-ScheduledTask -TaskName $tn -Action $acts -ErrorAction Stop | Out-Null; L "  vazifa yangilandi: $tn"
    } catch { L ("  vazifa $tn : " + $_.Exception.Message) }
  }
}
Start-Process wscript.exe -ArgumentList ('"' + $New + '\start_hidden.vbs"')
L 'Lumberture Hub yangi papkadan ishga tushirildi.'
Copy-Item $log (Join-Path $New 'data\papka_nomi_log.txt') -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
