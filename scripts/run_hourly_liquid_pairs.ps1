<#
.SYNOPSIS
  Запуск hourly_liquid_pairs_report.py из корня проекта; лог в data/reports/hourly_run.log
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$LogDir = Join-Path $RepoRoot "data\reports"
$LogFile = Join-Path $LogDir "hourly_run.log"
$PyScript = Join-Path $RepoRoot "scripts\hourly_liquid_pairs_report.py"

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-LogLine {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

if (-not (Test-Path -LiteralPath $PyScript)) {
    Write-LogLine "ERROR: missing script $PyScript"
    exit 1
}

Write-LogLine "START hourly liquid pairs root=$RepoRoot"

$py = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
else {
    Write-LogLine "ERROR: python not found in PATH"
    exit 1
}

Set-Location -LiteralPath $RepoRoot
$output = & $py $PyScript 2>&1
foreach ($line in $output) {
    Write-LogLine $line.ToString()
}
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-LogLine "ERROR: hourly_liquid_pairs_report.py exit code $code"
    exit $code
}
Write-LogLine "OK: report finished"
exit 0
