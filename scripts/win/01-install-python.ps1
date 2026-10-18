# Install the Python sidecar dependencies into <repo>\.venv
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
  & 'python' -m venv (Join-Path $RepoRoot '.venv')
}
& $python -m pip install -U pip
& $python -m pip install -r (Join-Path $RepoRoot 'services\sidecar\requirements-core.txt')
Write-Output "sidecar dependencies installed"
