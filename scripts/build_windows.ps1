# ADA.exe üretir (Windows). Çıktı: dist\ADA\ADA.exe
# Gereksinim: uv (https://docs.astral.sh/uv/), Node 18+ (arayüz derlenmemişse)
# Kullanım (PowerShell): .\scripts\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "ada\static\dist\index.html")) {
    Push-Location frontend; npm install; npm run build; Pop-Location
}

uv sync
uv run pyinstaller `
  --noconfirm --clean --windowed `
  --name ADA `
  --icon assets\icon.ico `
  --add-data "ada\static\dist;ada\static\dist" `
  --collect-submodules ada `
  --collect-all ddgs `
  --collect-all primp `
  --collect-all trafilatura `
  --collect-all justext `
  --collect-data docx `
  --collect-data pptx `
  --collect-submodules google.genai `
  --collect-submodules uvicorn `
  --hidden-import pyautogui `
  --collect-all winpty `
  --hidden-import psutil `
  app_main.py

Write-Host "Hazır: dist\ADA\ADA.exe"
Write-Host "Not: Windows'ta Mail/Takvim (AppleScript) araçları kapalıdır; e-posta için Ayarlar'dan IMAP kullanın."
