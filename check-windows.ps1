# Live checks: Nebius models, JSON replies, OCR, RxNorm, openFDA, Telegram. Run after start-windows.ps1 has set up .venv.
Set-Location -Path $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "backend"
& ".venv\Scripts\python.exe" scripts\check_live.py --telegram
