# УСТАРЕЛО: используйте scripts/hermes_sync_from_github.ps1 и репо Analise_Hermes
# Подтянуть Hermes-брифинг с VPS в локальный Cursor (.cursor/HERMES_LIVE.md)
#
# 1) Скопируйте scripts/hermes_pull_config.example.json → hermes_pull_config.json
# 2) Укажите IP сервера в ssh_host
# 3) Запуск:
#      powershell -ExecutionPolicy Bypass -File scripts/hermes_pull_cursor_feed.ps1
#
# Автоматически каждые 5 мин (Планировщик заданий Windows):
#   powershell -ExecutionPolicy Bypass -File scripts/hermes_pull_cursor_feed.ps1 -InstallTask

param(
    [string]$ConfigPath = "",
    [int]$IntervalSec = 300,
    [switch]$Loop,
    [switch]$InstallTask
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $ScriptDir "hermes_pull_config.json"
}

function Get-PullSettings {
    if (-not (Test-Path $ConfigPath)) {
        throw "Нет файла $ConfigPath — скопируйте hermes_pull_config.example.json"
    }
    $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $sshHost = if ($env:HERMES_SSH_HOST) { $env:HERMES_SSH_HOST } else { $cfg.ssh_host }
    $remote = if ($env:HERMES_REMOTE_MD) { $env:HERMES_REMOTE_MD } else { $cfg.remote_md }
    $localRel = if ($env:HERMES_LOCAL_MD) { $env:HERMES_LOCAL_MD } else { $cfg.local_md }
    $identity = if ($env:HERMES_SSH_IDENTITY) { $env:HERMES_SSH_IDENTITY } else { $cfg.identity_file }
    $local = Join-Path $RepoRoot $localRel
    return @{
        SshHost = $sshHost
        Remote = $remote
        Local = $local
        Identity = $identity
    }
}

function Invoke-HermesPull {
    $s = Get-PullSettings
    $localDir = Split-Path -Parent $s.Local
    if (-not (Test-Path $localDir)) {
        New-Item -ItemType Directory -Path $localDir -Force | Out-Null
    }
    $scpArgs = @()
    if ($s.Identity -and (Test-Path $s.Identity)) {
        $scpArgs += @("-i", $s.Identity)
    }
    $scpArgs += @("${s.SshHost}:$($s.Remote)", $s.Local)
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "scp завершился с кодом $LASTEXITCODE"
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$stamp] Обновлено: $($s.Local)"
}

function Install-HermesPullTask {
    $taskName = "PRD-BOT-Hermes-Cursor-Pull"
    $ps1 = Join-Path $ScriptDir "hermes_pull_cursor_feed.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$ps1`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Seconds $IntervalSec) -RepetitionDuration ([TimeSpan]::MaxValue)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Description "Hermes → Cursor live feed" -Force | Out-Null
    Write-Host "Задача '$taskName' создана (каждые $IntervalSec сек)"
}

if ($InstallTask) {
    Install-HermesPullTask
    exit 0
}

if ($Loop) {
    while ($true) {
        try {
            Invoke-HermesPull
        } catch {
            Write-Warning $_.Exception.Message
        }
        Start-Sleep -Seconds $IntervalSec
    }
} else {
    Invoke-HermesPull
}
