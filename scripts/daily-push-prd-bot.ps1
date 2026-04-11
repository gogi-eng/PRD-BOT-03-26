<#
.SYNOPSIS
  Commits the current PRD-SCALP tree and pushes to GitHub as a dated branch:
  dd.MM.yy_ScalpBot (example: 11.04.26_ScalpBot).

  Run once per day (e.g. Task Scheduler at 23:45) so each day gets its own branch
  with that day's snapshot. Same-day reruns add commits on the same branch.

  Requires: git, and credentials for https://github.com/gogi-eng/PRD-BOT-03-26.git
  (Git Credential Manager, PAT, or SSH remote if you change $RemoteUrl).

.PARAMETER RemoteName
  Git remote name (default: prd-bot).

.PARAMETER RemoteUrl
  Target repository URL.

.PARAMETER RepoRoot
  Override project root (default: parent of this script directory).
#>
[CmdletBinding()]
param(
    [string] $RemoteName = "prd-bot",
    [string] $RemoteUrl = "https://github.com/gogi-eng/PRD-BOT-03-26.git",
    [string] $RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    # scripts/daily-push-prd-bot.ps1 -> project root is parent of scripts/
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

Set-Location -LiteralPath $RepoRoot

$branch = "{0}_ScalpBot" -f (Get-Date -Format "dd.MM.yy")
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
$commitMsg = "Daily snapshot $stamp"

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot ".git"))) {
    git init
}

# Ensure remote exists and points at the GitHub repo
$remotes = @(git remote 2>$null)
if ($remotes -notcontains $RemoteName) {
    git remote add $RemoteName $RemoteUrl
} else {
    git remote set-url $RemoteName $RemoteUrl
}

$hasCommits = $false
try {
    git rev-parse --verify HEAD 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $hasCommits = $true }
} catch { $hasCommits = $false }

# Create or switch to today's branch
$branches = @(git branch --list --format="%(refname:short)" 2>$null)
if ($branches -contains $branch) {
    git checkout $branch
} elseif ($hasCommits) {
    git checkout -b $branch
} else {
    git checkout -b $branch
}

git add -A
$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    git commit --allow-empty -m "$commitMsg (no working tree changes)"
} else {
    git commit -m $commitMsg
}

git push -u $RemoteName "HEAD:refs/heads/$branch"

Write-Host "Pushed branch: $branch -> $RemoteName ($RemoteUrl)"
