# Windows.Media.Ocr → UTF-8 file
param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

function Await($WinRtTask, $ResultType) {
    $candidates = @(
        [System.WindowsRuntimeSystemExtensions].GetMethods() |
            Where-Object {
                $_.Name -eq "AsTask" -and
                $_.GetParameters().Count -eq 1 -and
                $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation`1"
            }
    )
    if ($candidates.Count -lt 1) {
        $candidates = @(
            [System.WindowsRuntimeSystemExtensions].GetMethods() |
                Where-Object { $_.Name -eq "AsTask" -and $_.IsGenericMethodDefinition }
        )
    }
    if ($candidates.Count -lt 1) {
        throw "AsTask not found"
    }
    $asTask = $candidates[0].MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
    [void]$asTask.Wait(-1)
    return $asTask.Result
}

if (-not (Test-Path -LiteralPath $ImagePath)) {
    throw "File not found: $ImagePath"
}

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media, ContentType = WindowsRuntime]

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { throw "OCR engine unavailable" }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[System.IO.File]::WriteAllText($OutFile, $result.Text, (New-Object System.Text.UTF8Encoding $false))
Write-Output ("OK len=" + $result.Text.Length)
