# Construit l'exe du poste offline (--onedir). À lancer depuis la racine
# du projet : powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

Write-Host "== Zweey : build .exe ==" -ForegroundColor Cyan

Write-Host "-- Installation des dependances offline --" -ForegroundColor Cyan
& ".\venv\Scripts\python.exe" -m pip install -r requirements\offline.txt
if ($LASTEXITCODE -ne 0) { throw "pip install a echoue." }

Write-Host "-- Nettoyage build/ et dist/ --" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "-- PyInstaller --" -ForegroundColor Cyan
& ".\venv\Scripts\pyinstaller.exe" pyinstaller\stock_facturation.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a echoue." }

Write-Host "== Build termine : dist\stock_facturation\stock_facturation.exe ==" -ForegroundColor Green
