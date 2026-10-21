# Tekstherkenning met de ingebouwde Windows OCR (offline). Gebruikt door app/ocr.py.
# Invoer: pad naar een afbeelding (PNG/JPG). Uitvoer (JSON): taal, breedte, hoogte en per woord tekst + kader.
param([Parameter(Mandatory)][string]$Pad, [switch]$Talen)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]

if ($Talen) {
    @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }) | ConvertTo-Json -Compress
    exit
}

# WinRT-async in Windows PowerShell 5.1 afwachten
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1" } |
    Select-Object -First 1
function Wacht($operatie, [Type]$type) {
    $taak = $asTask.MakeGenericMethod($type).Invoke($null, @($operatie))
    $null = $taak.Wait(-1)
    $taak.Result
}

# Nederlands als dat er is, anders de taal van het gebruikersprofiel
$engine = $null
foreach ($tag in @("nl-NL", "nl")) {
    $taal = New-Object Windows.Globalization.Language $tag
    if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($taal)) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($taal); break }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { throw "Geen OCR-taal beschikbaar in Windows" }

$bestand = Wacht ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Pad).Path)) ([Windows.Storage.StorageFile])
$stroom = Wacht ($bestand.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Wacht ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stroom)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Wacht ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$resultaat = Wacht ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$woorden = foreach ($regel in $resultaat.Lines) {
    $nr = [array]::IndexOf(@($resultaat.Lines), $regel)
    foreach ($w in $regel.Words) {
        [pscustomobject]@{ t = $w.Text; r = $nr; x = $w.BoundingRect.X; y = $w.BoundingRect.Y; b = $w.BoundingRect.Width; h = $w.BoundingRect.Height }
    }
}
$stroom.Dispose()
[pscustomobject]@{ taal = $engine.RecognizerLanguage.LanguageTag; breedte = $bitmap.PixelWidth; hoogte = $bitmap.PixelHeight; woorden = @($woorden) } |
    ConvertTo-Json -Depth 4 -Compress
