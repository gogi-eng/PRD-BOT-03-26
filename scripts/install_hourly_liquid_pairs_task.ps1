<#
.SYNOPSIS
  Registers Windows Scheduled Task: hourly Bybit liquid pairs report (schtasks /sc hourly).

.PARAMETER TaskName
  Task name (default: PRD-BOT-HourlyLiquidPairs).
#>
[CmdletBinding()]
param(
    [string] $TaskName = "PRD-BOT-HourlyLiquidPairs"
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "run_hourly_liquid_pairs.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
schtasks /delete /tn $TaskName /f 2>$null | Out-Null
$proc = Start-Process -FilePath "schtasks.exe" -ArgumentList @(
    "/create", "/tn", $TaskName, "/tr", $tr, "/sc", "hourly", "/st", "00:00", "/f"
) -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    throw "schtasks failed with exit code $($proc.ExitCode). Try running PowerShell as Administrator."
}
Write-Host "Registered task '$TaskName' every 1 hour -> $scriptPath"
Write-Host "Check: schtasks /query /tn '$TaskName' /fo LIST"
Write-Host "Or: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Log: data/reports/hourly_run.log"
Write-Host "Report: data/reports/liquid_pairs_latest.md"
