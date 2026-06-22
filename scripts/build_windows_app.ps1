Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$PythonCmd = Get-Command py -ErrorAction SilentlyContinue
if ($PythonCmd) {
    $Python = "py"
    $PythonArgs = @("-3.11")
} else {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCmd) {
        throw "Python 3.11 or 3.12 is required to build the Windows app."
    }
    $Python = "python"
    $PythonArgs = @()
}

if (-not (Test-Path ".venv_build")) {
    Write-Host "Creating build environment..."
    & $Python @PythonArgs -m venv .venv_build
}

& ".\.venv_build\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv_build\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt

$DefaultTesseractDir = "C:\Program Files\Tesseract-OCR"
if (-not (Test-Path (Join-Path $DefaultTesseractDir "tesseract.exe"))) {
    $Choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($Choco) {
        Write-Host "Installing Tesseract OCR for bundling..."
        choco install tesseract --no-progress -y
    }
}

if (Test-Path (Join-Path $DefaultTesseractDir "tesseract.exe")) {
    $env:TESSERACT_DIR = $DefaultTesseractDir
    Write-Host "Bundling Tesseract OCR from $env:TESSERACT_DIR"
} else {
    Write-Warning "Tesseract OCR was not found. The app will build, but image/scanned OCR may require Tesseract."
}

if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }

& ".\.venv_build\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging/DataSecurityLocal-Windows.spec

Write-Host ""
Write-Host "Built:"
Write-Host "  dist\Data Security Local.exe"
Write-Host ""
Write-Host "For sharing, zip the EXE:"
Write-Host "  Compress-Archive -Path 'dist\Data Security Local.exe' -DestinationPath 'dist\Data Security Local-windows.zip' -Force"
