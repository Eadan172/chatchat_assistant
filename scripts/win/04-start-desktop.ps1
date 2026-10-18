# Start the Electron desktop shell. It boots its own sidecar child process.
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

# Electron profile and logs stay in .data\desktop; the knowledge base is shared
# with the standalone sidecar in .data, so both UIs show the same documents.
$env:CHATCHAT_DATA_DIR = Join-Path $RepoRoot '.data\desktop'
$env:DATA_DIR = Join-Path $RepoRoot '.data'
if (-not $env:VITE_FEATURE_ASK) { $env:VITE_FEATURE_ASK = 'true' }
# Electron must run as an app, not as plain Node.
Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
# No usable GPU on this machine (the GPU process crashes); use software rendering.
$env:CHATCHAT_DISABLE_GPU = '1'

Set-Location $RepoRoot
Write-Output "DATA_DIR=$($env:DATA_DIR)"
$ErrorActionPreference = 'Continue'
npm run dev
