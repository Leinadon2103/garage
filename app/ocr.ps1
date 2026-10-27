# Text recognition with the built-in Windows OCR (offline). Used by app/ocr.py.
# Input: an image (PNG/JPG) as base64 on stdin, never as a file: a temporary copy of an invoice could stay
# behind after a crash (P8). Output (JSON): language, width, height and per word text + box.
param([switch]$Languages)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]

if ($Languages) {
    @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }) | ConvertTo-Json -Compress
    exit
}

# Await WinRT async calls in Windows PowerShell 5.1
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1" } |
    Select-Object -First 1
function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    $task.Result
}

# Dutch if available, otherwise the language of the user profile
$engine = $null
foreach ($tag in @("nl-NL", "nl")) {
    $language = New-Object Windows.Globalization.Language $tag
    if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($language)) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language); break }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { throw "No OCR language available in Windows" }

$bytes = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())  # base64: stdin is text in PowerShell
$stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream((New-Object IO.MemoryStream (, $bytes)))
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$words = foreach ($line in $result.Lines) {
    $nr = [array]::IndexOf(@($result.Lines), $line)
    foreach ($w in $line.Words) {
        [pscustomobject]@{ t = $w.Text; l = $nr; x = $w.BoundingRect.X; y = $w.BoundingRect.Y; w = $w.BoundingRect.Width; h = $w.BoundingRect.Height }
    }
}
$stream.Dispose()
[pscustomobject]@{ language = $engine.RecognizerLanguage.LanguageTag; width = $bitmap.PixelWidth; height = $bitmap.PixelHeight; words = @($words) } |
    ConvertTo-Json -Depth 4 -Compress
