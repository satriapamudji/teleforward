Param(
  [switch]$RunTui = $true
)

$ErrorActionPreference = "Stop"

function Assert-Python {
  $py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $py) {
    throw "python not found. Install Python 3.10+ and ensure it's on PATH."
  }
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" | Out-Null
}

Assert-Python

if (-not (Test-Path ".env")) {
  Write-Host "Creating .env from .env.example"
  Copy-Item ".env.example" ".env"
  Write-Host "Edit .env and set TELEGRAM_API_ID/TELEGRAM_API_HASH before running."
}

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -U pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip install -e .

if ($RunTui) {
  & .\.venv\Scripts\teleforward.exe tui
}
