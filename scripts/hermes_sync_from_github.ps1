# Синхронизация Hermes с любого ПК через GitHub
#
# 1) Клонируйте рядом с PRD-BOT:
#      git clone https://github.com/gogi-eng/Analise_Hermes.git
# 2) Запускайте после работы (или -InstallTask):
#      powershell -ExecutionPolicy Bypass -File scripts/hermes_sync_from_github.ps1

param(
    [string]$HermesDir = "",
    [string]$PrdBotRoot = "",
    [int]$IntervalSec = 300,
    [switch]$Loop,
    [switch]$InstallTask
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DefaultPrdRoot = Split-Path -Parent $ScriptDir

if (-not $PrdBotRoot) {
    $PrdBotRoot = if ($env:PRD_BOT_ROOT) { $env:PRD_BOT_ROOT } else { $DefaultPrdRoot }
}
if (-not $HermesDir) {
    $HermesDir = if ($env:HERMES_GITHUB_DIR) { $env:HERMES_GITHUB_DIR } else {
        Join-Path (Split-Path -Parent $PrdBotRoot) "Analise_Hermes"
    }
}

function Sync-HermesFromGitHub {
    if (-not (Test-Path (Join-Path $HermesDir ".git"))) {
        throw "Нет клона Analise_Hermes в $HermesDir. Выполните: git clone https://github.com/gogi-eng/Analise_Hermes.git"
    }
    Push-Location $HermesDir
    try {
        git pull --rebase origin main 2>$null
        if ($LASTEXITCODE -ne 0) {
            git pull --rebase origin master
        }
    } finally {
        Pop-Location
    }

    $src = Join-Path $HermesDir "HERMES_LIVE.md"
    if (-not (Test-Path $src)) {
        throw "Нет HERMES_LIVE.md в репозитории (сервер ещё не публиковал отчёт)"
    }

    $dstDir = Join-Path $PrdBotRoot ".cursor"
    if (-not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    $dst = Join-Path $dstDir "HERMES_LIVE.md"
    Copy-Item -Path $src -Destination $dst -Force
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$stamp] Cursor обновлён: $dst"
}

function Install-HermesSyncTask {
    $taskName = "PRD-BOT-Hermes-GitHub-Sync"
    $ps1 = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$ps1`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Seconds $IntervalSec) -RepetitionDuration ([TimeSpan]::MaxValue)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Description "Hermes GitHub → .cursor/HERMES_LIVE.md" -Force | Out-Null
    Write-Host "Задача '$taskName' — каждые $IntervalSec сек"
}

if ($InstallTask) {
    Install-HermesSyncTask
    exit 0
}

if ($Loop) {
    while ($true) {
        try { Sync-HermesFromGitHub } catch { Write-Warning $_.Exception.Message }
        Start-Sleep -Seconds $IntervalSec
    }
} else {
    Sync-HermesFromGitHub
}
