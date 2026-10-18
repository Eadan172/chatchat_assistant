# Shared environment for a workspace-contained deployment.
# Every cache, model, database and log path stays under this repository.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$env:PROJECT_ROOT = $RepoRoot
$env:DATA_DIR = Join-Path $RepoRoot '.data'
$env:PIP_CACHE_DIR = Join-Path $RepoRoot '.cache\pip'
$env:npm_config_cache = Join-Path $RepoRoot '.cache\npm'
$env:ELECTRON_CACHE = Join-Path $RepoRoot '.cache\electron'
$env:electron_config_cache = $env:ELECTRON_CACHE
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $RepoRoot '.cache\ms-playwright'
$env:HF_HOME = Join-Path $RepoRoot '.cache\huggingface'
$env:TMP = Join-Path $RepoRoot '.cache\tmp'
$env:TEMP = $env:TMP
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
foreach ($d in @($env:DATA_DIR, $env:PIP_CACHE_DIR, $env:npm_config_cache, $env:ELECTRON_CACHE, $env:TMP)) {
  if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
