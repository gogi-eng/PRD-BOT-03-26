<#
.SYNOPSIS
  Renames the local OPTIMUS working branch (dd.MM.yy_OPTIMUS) and updates remote prd-bot.

.PARAMETER NewDate
  Date suffix for the new branch name, e.g. "12.04.26" -> branch 12.04.26_OPTIMUS.
  Default: today's date in local time.

.PARAMETER OldBranch
  Current branch name. Default: auto-detect branch matching *_OPTIMUS.

.PARAMETER RemoteName
  Git remote (default: prd-bot).

.PARAMETER DeleteOldRemote
  If set, deletes the old branch on the remote after pushing the new one.
#>
[CmdletBinding()]
param(
    [string] $NewDate = "",
    [string] $OldBranch = "",
    [string] $RemoteName = "prd-bot",
    [switch] $DeleteOldRemote
)

$ErrorActionPreference = "Stop"

if (-not $NewDate) {
    $NewDate = Get-Date -Format "dd.MM.yy"
}
$newBranch = "${NewDate}_OPTIMUS"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $OldBranch) {
    $current = git rev-parse --abbrev-ref HEAD 2>$null
    if ($current -match '_OPTIMUS$') {
        $OldBranch = $current
    } else {
        $candidates = @(git branch --list '*_OPTIMUS' --format='%(refname:short)')
        if ($candidates.Count -eq 1) {
            $OldBranch = $candidates[0]
        } else {
            throw "Could not infer OldBranch. Checkout your OPTIMUS branch or pass -OldBranch '11.04.26_OPTIMUS'"
        }
    }
}

if ($OldBranch -eq $newBranch) {
    Write-Host "Already on branch name: $newBranch (nothing to do)"
    exit 0
}

Write-Host "Renaming local: $OldBranch -> $newBranch"
git branch -m $OldBranch $newBranch

Write-Host "Pushing: $RemoteName $newBranch"
git push -u $RemoteName $newBranch

if ($DeleteOldRemote) {
    Write-Host "Deleting remote branch: $RemoteName/$OldBranch"
    git push $RemoteName --delete $OldBranch
}

Write-Host "Done. Current branch: $newBranch (tracks $RemoteName/$newBranch)"
