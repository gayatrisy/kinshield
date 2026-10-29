# KinShield on Windows: sets up Python packages once, then starts the web app and the reminder worker.
# Run from this folder:  powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Add NEBIUS_API_KEY (and the Telegram values), save it, then run this script again." -ForegroundColor Yellow
    notepad ".env"
    exit 1
}

$py = $null
$candidates = @(@("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("py", "-3"), @("python"))
foreach ($cand in $candidates) {
    try {
        $exe = $cand[0]; $pre = @($cand | Select-Object -Skip 1)
        $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]"3.10") { $py = $cand; break }
    } catch {}
}
if (-not $py) { Write-Host "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')." -ForegroundColor Red; exit 1 }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment ..."
    $exe = $py[0]; $pre = @($py | Select-Object -Skip 1)
    & $exe @pre -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --disable-pip-version-check -q -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed" -ForegroundColor Red; exit 1 }

if (-not (Test-Path "frontend\dist\index.html")) {
    Write-Host "The web app build (frontend\dist) is missing. Run: cd frontend; npm install; npm run build" -ForegroundColor Red
    exit 1
}

$tess = (Get-Command tesseract -ErrorAction SilentlyContinue) -or (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")
if (-not $tess) {
    Write-Host "Note: Tesseract OCR is not installed. PDFs work; phone photos need it." -ForegroundColor Yellow
    Write-Host "      Install: winget install UB-Mannheim.TesseractOCR" -ForegroundColor Yellow
}

$env:PYTHONPATH = Join-Path $PSScriptRoot "backend"
Write-Host "Starting the reminder worker in a second window ..."
Start-Process -FilePath $venvPy -ArgumentList "-m", "kinshield.worker" -WorkingDirectory $PSScriptRoot

Write-Host ""
Write-Host "KinShield is starting at http://localhost:8000  (Ctrl+C to stop)" -ForegroundColor Green
Start-Process "http://localhost:8000"
& $venvPy -m uvicorn kinshield.api:app --host 127.0.0.1 --port 8000 --app-dir backend
