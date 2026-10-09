# One-command start for Windows PowerShell:  .\run.ps1   (add -Mock to try the sample data)
param([switch]$Mock)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
  Write-Host "Creating virtual environment..."
  py -3 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
pip install -q -r requirements.txt

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host "Created .env. Add your SERPAPI_API_KEY (and optionally GEMINI_API_KEY) to it."
}
if ($Mock) { $env:PANCHAYAT_MOCK = "1" }

python -m backend
