<#
.SYNOPSIS
  Регистрирует задачу Windows: автосохранение чатов Cursor в репо + push.

.PARAMETER IntervalMin
  Интервал в минутах (по умолчанию 30).
#>
[CmdletBinding()]
param(
    [int] $IntervalMin = 30
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "sync_cursor_chats.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Нет файла: $scriptPath"
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -InstallTask -IntervalMin $IntervalMin
