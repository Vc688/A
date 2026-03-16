$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Run-With([string]$runner) {
  & $runner -m pip install -r requirements.txt
  & $runner app.py
}

try {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    Run-With "py"
    exit 0
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    Run-With "python"
    exit 0
  }

  Write-Host "Python was not found on PATH." -ForegroundColor Red
  Write-Host "Please reinstall Python and check 'Add Python to PATH'."
}
catch {
  Write-Host "Startup failed:" -ForegroundColor Red
  Write-Host $_.Exception.Message
}

Read-Host "Press Enter to close"
