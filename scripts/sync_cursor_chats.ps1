<#
.SYNOPSIS
  Экспорт чатов Cursor в .cursor/chats/ и (опционально) push на GitHub.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/sync_cursor_chats.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/sync_cursor_chats.ps1 -Push

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/sync_cursor_chats.ps1 -InstallTask
#>
[CmdletBinding()]
param(
    [switch] $Push,
    [switch] $DryRun,
    [switch] $InstallTask,
    [int] $IntervalMin = 30
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

function Get-PythonExe {
    $venvPy = Join-Path $RepoRoot "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }
    return "python"
}

function Invoke-CursorChatSync {
    $py = Get-PythonExe
    $syncPy = Join-Path $ScriptDir "sync_cursor_chats.py"
    $args = @($syncPy)
    if ($Push) { $args += "--push" }
    if ($DryRun) { $args += "--dry-run" }
  & $py @args
    if ($LASTEXITCODE -ne 0) {
        throw "sync_cursor_chats.py завершился с кодом $LASTEXITCODE"
    }
}

function Install-CursorChatSyncTask {
    $taskName = "PRD-BOT-Cursor-Chat-Sync"
    $ps1 = Join-Path $ScriptDir "sync_cursor_chats.ps1"
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$ps1`" -Push"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMin) `
        -RepetitionDuration ([TimeSpan]::MaxValue)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Задача '$taskName' создана: каждые $IntervalMin мин, с push на GitHub."
}

if ($InstallTask) {
    Install-CursorChatSyncTask
    exit 0
}

Set-Location -LiteralPath $RepoRoot
Invoke-CursorChatSync
