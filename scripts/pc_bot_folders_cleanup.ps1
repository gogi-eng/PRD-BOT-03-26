# Safe local cleanup for PRD-BOT folders on Windows.
# Does NOT touch .env, venv, live config.yaml, session secrets.
# Run: powershell -ExecutionPolicy Bypass -File scripts/pc_bot_folders_cleanup.ps1
# Optional: -ConfirmDelete to actually delete (default is dry-run report + safe deletes of caches only when -ConfirmDelete)

param(
  [switch]$ConfirmDelete
)

$ErrorActionPreference = 'Continue'
$report = New-Object System.Collections.Generic.List[string]
$freed = [int64]0

function Add-Report([string]$msg) { $report.Add($msg) | Out-Null; Write-Host $msg }

function Remove-SafeItem([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  $item = Get-Item -LiteralPath $path -Force
  $size = 0
  if ($item.PSIsContainer) {
    $size = (Get-ChildItem -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue |
      Measure-Object -Property Length -Sum).Sum
    if (-not $size) { $size = 0 }
  } else {
    $size = [int64]$item.Length
  }
  if ($ConfirmDelete) {
    Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    Add-Report ("REMOVED  {0}  (~{1:N1} KB)" -f $path, ($size/1KB))
  } else {
    Add-Report ("DRY-RUN  {0}  (~{1:N1} KB)" -f $path, ($size/1KB))
  }
  $script:freed += [int64]$size
}

function Keep-LatestBak([string]$dir, [string]$filter) {
  if (-not (Test-Path -LiteralPath $dir)) { return }
  $files = @(Get-ChildItem -LiteralPath $dir -Filter $filter -File -Force -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
  if ($files.Count -le 1) {
    if ($files.Count -eq 1) { Add-Report ("KEEP bak {0}" -f $files[0].FullName) }
    return
  }
  Add-Report ("KEEP bak {0}" -f $files[0].FullName)
  for ($i = 1; $i -lt $files.Count; $i++) {
    Remove-SafeItem $files[$i].FullName
  }
}

$roots = @(
  'c:\Users\Labuh\.vscode\PRD-BOT-ALL',
  'c:\Users\Labuh\.vscode\AGENT-WORLD'
)

Add-Report "=== pc_bot_folders_cleanup ConfirmDelete=$ConfirmDelete ==="

foreach ($root in $roots) {
  if (-not (Test-Path -LiteralPath $root)) {
    Add-Report "SKIP missing $root"
    continue
  }
  Add-Report "--- $root ---"

  # __pycache__ and *.pyc (skip venv)
  Get-ChildItem -LiteralPath $root -Recurse -Directory -Filter '__pycache__' -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\venv\\|\\\.venv\\' } |
    ForEach-Object { Remove-SafeItem $_.FullName }

  Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.pyc' -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\venv\\|\\\.venv\\' } |
    ForEach-Object { Remove-SafeItem $_.FullName }

  # Все config.yaml.bak* (включая .bak.prune.*) — оставить только самый новый.
  Keep-LatestBak $root 'config.yaml.bak*'

  # huge rotated logs older than 14 days (bot.log itself kept)
  Get-ChildItem -LiteralPath $root -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
      ($_.Name -match '^(bot\.log\.|telegram_signal_agent\.log\.)') -and
      ($_.LastWriteTime -lt (Get-Date).AddDays(-14))
    } | ForEach-Object { Remove-SafeItem $_.FullName }
}

# dumps: keep newest directory/file only
$dumps = 'c:\Users\Labuh\.vscode\dumps'
if (Test-Path -LiteralPath $dumps) {
  Add-Report "--- $dumps ---"
  $items = @(Get-ChildItem -LiteralPath $dumps -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne '_make_aw_dump.sh' } |
    Sort-Object LastWriteTime -Descending)
  if ($items.Count -gt 1) {
    Add-Report ("KEEP dump {0}" -f $items[0].FullName)
    for ($i = 1; $i -lt $items.Count; $i++) {
      Remove-SafeItem $items[$i].FullName
    }
  } elseif ($items.Count -eq 1) {
    Add-Report ("KEEP dump (only one) {0}" -f $items[0].FullName)
  }
}

# Temp clones / unfinished push dirs (entire directories safe to remove)
$tempDirs = @(
  'C:\Temp\prd-bot-push',
  'C:\Temp\prd-bot-push-garch',
  'C:\Temp\prd-opp-spike',
  'C:\Temp\prd-audit'
)
Add-Report "--- C:\Temp prd* ---"
foreach ($td in $tempDirs) {
  if (Test-Path -LiteralPath $td) {
    Remove-SafeItem $td
  }
}

Add-Report ("=== summary estimated ~{0:N1} MB ===" -f ($freed/1MB))
if (-not $ConfirmDelete) {
  Add-Report "DRY-RUN only. Re-run with -ConfirmDelete to delete."
}
