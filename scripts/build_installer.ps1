# ADA-Setup.exe üretir (Windows kurulum programı). Çıktı: dist\ADA-Setup.exe
# Gereksinim: Inno Setup 6 (winget install -e --id JRSoftware.InnoSetup --scope user)
# Kullanım: cmd /c powershell -File scripts\build_installer.ps1            (uygulamayı da yeniden paketler)
#           cmd /c powershell -File scripts\build_installer.ps1 -SkipApp   (var olan dist\ADA ile)
param([switch]$SkipApp)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $SkipApp) {
    & (Join-Path $PSScriptRoot "build_windows.ps1")
}
if (-not (Test-Path "dist\ADA\ADA.exe")) { throw "dist\ADA\ADA.exe yok; önce scripts\build_windows.ps1 çalıştırın." }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup bulunamadı: winget install -e --id JRSoftware.InnoSetup --scope user" }

$version = (Select-String -Path ada\__init__.py -Pattern '^__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$isccArgs = @("/DAppVersion=$version", "/Qp")

# İmza sertifikası varsa (scripts\sign.ps1 -Create ile oluşturulan ya da ADA_SIGN_THUMBPRINT) ADA.exe,
# kurulum programı ve kaldırıcı imzalanır; yoksa imzasız üretilir.
$sign = Join-Path $PSScriptRoot "sign.ps1"
$hasCert = $env:ADA_SIGN_THUMBPRINT -or (Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -eq "CN=Duck Team" -and $_.NotAfter -gt (Get-Date) })
if ($hasCert) {
    & $sign "dist\ADA\ADA.exe"
    $isccArgs += "/DSign"
    $isccArgs += "/Sps=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$q$sign`$q `$f"  # $f: Inno dosya yolunu kendisi tırnaklar
} else {
    Write-Host "İmza sertifikası yok: kurulum programı imzasız üretiliyor (scripts\sign.ps1 -Create)."
}

& $iscc @isccArgs "scripts\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup derlemesi başarısız (kod $LASTEXITCODE)" }

Write-Host ("Hazır: dist\ADA-Setup.exe" + $(if ($hasCert) { " (imzalı)" } else { "" }))
