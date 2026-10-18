# Serve the renderer in a normal browser against the standalone sidecar.
# UI: http://127.0.0.1:5199  (sidecar must already run on 8765)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')

$env:VITE_SIDECAR_URL = 'http://127.0.0.1:8765'
if (-not $env:VITE_SIDECAR_TOKEN) { $env:VITE_SIDECAR_TOKEN = 'devtoken' }
if (-not $env:VITE_FEATURE_ASK) { $env:VITE_FEATURE_ASK = 'true' }

Set-Location (Join-Path $RepoRoot 'apps\desktop')
Write-Output "web ui on http://127.0.0.1:5199 (sidecar $($env:VITE_SIDECAR_URL))"
$ErrorActionPreference = 'Continue'
& (Join-Path $RepoRoot 'node_modules\.bin\vite.cmd') --config vite.web.config.ts
