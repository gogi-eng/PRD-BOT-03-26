<#
.SYNOPSIS
  Registers a Windows Scheduled Task to run daily-push-prd-bot.ps1 once per day.

.PARAMETER At
  Local time to run (default: 23:45).

.PARAMETER TaskName
  Scheduled task name (default: PRD-BOT-PRD-SCALP-DailyPush).
#>
[CmdletBinding()]
param(
    [string] $At = "23:45",
    [string] $TaskName = "PRD-BOT-PRD-SCALP-DailyPush"
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "daily-push-prd-bot.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest | Out-Null
Write-Host "Registered task '$TaskName' daily at $At -> $scriptPath"
Write-Host "If registration failed, run PowerShell as Administrator and retry."
