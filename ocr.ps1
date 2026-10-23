# Rasmdagi matnni o'qish: Windows 10 ichidagi OCR (Windows.Media.Ocr). Natija — UTF-8 matn (har qator alohida).
# Ishlatish: powershell -NoProfile -ExecutionPolicy Bypass -File ocr.ps1 "C:\path\photo.jpg"
param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $task.Wait(60000)
    $task.Result
}

$full = (Resolve-Path -LiteralPath $Path).Path
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])

# Rus tili bo'lsa — rus, bo'lmasa — foydalanuvchi tillari (raqamlar har qanday tilda o'qiladi)
$engine = $null
foreach ($tag in @('ru', 'ru-RU', 'uz-Cyrl', 'en-US')) {
    try {
        $lang = New-Object Windows.Globalization.Language($tag)
        if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang); break }
    } catch {}
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Error 'OCR tili topilmadi'; exit 2 }
[Console]::Out.WriteLine('#lang ' + $engine.RecognizerLanguage.LanguageTag)

$max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
$w = $decoder.OrientedPixelWidth; $h = $decoder.OrientedPixelHeight
# Ishchi rasmni yon tomonga olishi mumkin — 4 tomonga burib o'qiymiz, «Накладная» topilsa to'xtaymiz
foreach ($rot in @('None', 'Clockwise90Degrees', 'Clockwise270Degrees', 'Clockwise180Degrees')) {
    $tr = New-Object Windows.Graphics.Imaging.BitmapTransform
    $tr.Rotation = [Windows.Graphics.Imaging.BitmapRotation]::$rot
    $m = [Math]::Max($w, $h)
    if ($m -gt $max) {
        $sc = $max / $m
        $tr.ScaledWidth = [uint32][Math]::Floor($decoder.PixelWidth * $sc)
        $tr.ScaledHeight = [uint32][Math]::Floor($decoder.PixelHeight * $sc)
    } elseif ($m -lt 1800) {
        # Telegram расмни кичрайтиради — майда ёзув яхши ўқилиши учун 2 баробар катталаштирамиз
        $sc = [Math]::Min(2.0, $max / $m)
        $tr.ScaledWidth = [uint32][Math]::Floor($decoder.PixelWidth * $sc)
        $tr.ScaledHeight = [uint32][Math]::Floor($decoder.PixelHeight * $sc)
        $tr.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Fant
    }
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $tr,
        [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation, [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    [Console]::Out.WriteLine('#rot ' + $rot)
    $txt = ''
    foreach ($line in $result.Lines) { [Console]::Out.WriteLine($line.Text); $txt += $line.Text + "`n" }
    # «Накладная» ва рақам топилса — тўғри томони шу; бўлмаса бошқа томонга буриб кўрамиз (ён, тескари)
    if ($txt -match '(?i)(накл|nakl|hakл)' -and $txt -match '\d{3,6}') { break }
}
