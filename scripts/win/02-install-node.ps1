# Install the desktop (Electron + React) dependencies with every cache kept in the repo.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
Set-Location $RepoRoot
# Electron binaries are large; a mainland mirror is used when ELECTRON_MIRROR is set.
npm ci --no-audit --no-fund
Write-Output "desktop dependencies installed"
