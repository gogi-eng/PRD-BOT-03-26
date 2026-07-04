# Freqtrade dry-run lab для сравнения с PRD-BOT (без боевых ключей)
$ErrorActionPreference = "Stop"
$LabRoot = "C:\Temp\freqtrade-lab"
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = "https://github.com/freqtrade/freqtrade.git"

if (-not (Test-Path $LabRoot)) {
    Write-Host "Cloning freqtrade to $LabRoot ..."
    git clone --depth 1 --branch stable $Repo $LabRoot
}

Set-Location $LabRoot
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    python -m venv venv
    .\venv\Scripts\pip install -U pip wheel
}
.\venv\Scripts\pip install -e .
.\venv\Scripts\pip install scipy TA-Lib

New-Item -ItemType Directory -Force -Path "$LabRoot\user_data\strategies" | Out-Null
Copy-Item "$Src\config_dryrun.json" "$LabRoot\user_data\config.json" -Force
Copy-Item "$Src\PrdMirrorStrategy.py" "$LabRoot\user_data\strategies\PrdMirrorStrategy.py" -Force

Write-Host "Downloading 15m futures data (BTC/ETH/SOL)..."
.\venv\Scripts\freqtrade.exe download-data `
    --config user_data\config.json `
    --timeframes 15m `
    --timerange 20250601-20250701 `
    --trading-mode futures

Write-Host "Running PRD compare backtests..."
Set-Location (Split-Path -Parent (Split-Path -Parent $Src))
python "$Src\run_prd_compare.py"
python "$Src\orderflow_threshold_study.py"
Write-Host "Done."
