<#
.SYNOPSIS
  Removes PRD-BOT-HourlyLiquidPairs from Windows Task Scheduler.
#>
[CmdletBinding()]
param(
    [string] $TaskName = "PRD-BOT-HourlyLiquidPairs"
)

$ErrorActionPreference = "Stop"
$proc = Start-Process -FilePath "schtasks.exe" -ArgumentList @("/delete", "/tn", $TaskName, "/f") -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    Write-Host "Task '$TaskName' not found or could not be deleted (exit $($proc.ExitCode))."
    exit $proc.ExitCode
}
Write-Host "Removed task '$TaskName'."
