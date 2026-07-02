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

function Invoke-GitPull {
    param([string]$Branch)
    # git пишет "From https://..." в stderr — PowerShell не должен считать это ошибкой
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git pull --rebase origin $Branch 2>&1 | Out-Null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Sync-HermesFromGitHub {
    if (-not (Test-Path (Join-Path $HermesDir ".git"))) {
        throw "Analise_Hermes not found at $HermesDir. Run: git clone https://github.com/gogi-eng/Analise_Hermes.git"
    }
    Push-Location $HermesDir
    try {
        $exit = Invoke-GitPull -Branch "main"
        if ($exit -ne 0) {
            $exit = Invoke-GitPull -Branch "master"
        }
        if ($exit -ne 0) {
            Write-Warning "git pull failed (exit $exit); copying local Hermes files"
        }
    } finally {
        Pop-Location
    }

    $src = Join-Path $HermesDir "HERMES_LIVE.md"
    if (-not (Test-Path $src)) {
        throw "HERMES_LIVE.md missing in repo (server has not published yet)"
    }

    $dstDir = Join-Path $PrdBotRoot ".cursor"
    if (-not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    $dst = Join-Path $dstDir "HERMES_LIVE.md"
    Copy-Item -Path $src -Destination $dst -Force

    $hermesData = Join-Path $PrdBotRoot "data\hermes"
    if (-not (Test-Path $hermesData)) {
        New-Item -ItemType Directory -Path $hermesData -Force | Out-Null
    }
    foreach ($name in @("winning_entry_rules.json", "HERMES_LIVE.md", "meta.json")) {
        $f = Join-Path $HermesDir $name
        if (Test-Path $f) {
            Copy-Item -Path $f -Destination (Join-Path $hermesData $name) -Force
        }
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$stamp] Updated: $dst"
}

function Invoke-Schtasks {
    param([string[]]$SchtasksArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & schtasks @SchtasksArgs 2>&1
        return @{ Exit = $LASTEXITCODE; Out = ($out | Out-String).Trim() }
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Install-HermesSyncTask {
    $taskName = "PRD-BOT-Hermes-GitHub-Sync"
    $ps1 = $MyInvocation.MyCommand.Path
    $tr = "powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$ps1`""

    # schtasks /SC MINUTE — без лимита Duration (Register-ScheduledTask MaxValue ломается на Windows)
    $minutes = [Math]::Max(1, [int][Math]::Round($IntervalSec / 60.0))
    if ($IntervalSec -lt 60) {
        Write-Warning "IntervalSec=${IntervalSec}: Task Scheduler min step is 1 min; using $minutes min"
    }

    Invoke-Schtasks -Args @("/Delete", "/TN", $taskName, "/F") | Out-Null
    $created = Invoke-Schtasks -Args @("/Create", "/TN", $taskName, "/TR", $tr, "/SC", "MINUTE", "/MO", "$minutes", "/F")
    if ($created.Exit -ne 0) {
        throw "schtasks failed: $($created.Out)"
    }
    Write-Host "Task '$taskName': every $minutes min (Hermes sync)"
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
