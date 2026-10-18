# Start the FastAPI sidecar on 127.0.0.1:8765 with a workspace-local data directory.
param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')

$envFile = Join-Path $RepoRoot '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
    $name, $value = $_.Split('=', 2)
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
  }
}
if (-not $env:SIDECAR_TOKEN) { $env:SIDECAR_TOKEN = 'devtoken' }
if (-not $env:FEATURE_ASK) { $env:FEATURE_ASK = '1' }

Set-Location (Join-Path $RepoRoot 'services\sidecar')
$shownData = $env:DATA_DIR.Replace($RepoRoot, '<repo>')
Write-Output "DATA_DIR=$shownData"
Write-Output "listening on http://127.0.0.1:$Port"
# uvicorn writes its banner to stderr; keep that from being treated as a fatal PowerShell error.
$ErrorActionPreference = 'Continue'
& (Join-Path $RepoRoot '.venv\Scripts\python.exe') -m uvicorn app.main:app --host 127.0.0.1 --port $Port 2>&1 |
  ForEach-Object { "$_" }
