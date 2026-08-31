param([switch]$SkipSamples)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$nycLens = Join-Path $projectRoot ".venv\Scripts\nyc-lens.exe"

if (-not (Test-Path -LiteralPath $nycLens)) {
    throw "Virtual environment not found. Follow README.md installation steps first."
}

& $nycLens init
& $nycLens refresh
if ($LASTEXITCODE -ne 0) {
    Write-Warning "One or more sources failed. Every attempted source retained its own ingestion-run status; inspect nyc-lens status before retrying."
}
if (-not $SkipSamples) {
    & $nycLens samples
}
& $nycLens docs
& $nycLens status
& $nycLens qa
