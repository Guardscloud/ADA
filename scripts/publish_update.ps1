# Yeni sürümü yayımlar: kurulum programını (imzalı) üretir ve uygulama içi güncellemenin okuyacağı yere koyar.
#
# Önce ada\__init__.py ve pyproject.toml içindeki sürümü artırın (ör. 0.1.0 → 0.2.0).
#
#   cmd /c powershell -File scripts\publish_update.ps1 -Notes "Neler değişti…"
#       → dist\update\ADA-Setup.exe + dist\update\latest.json üretir. İkisini aynı klasöre, https ile
#         erişilen herhangi bir yere yükleyin; ADA'da güncelleme adresi o latest.json'ın adresidir.
#
#   cmd /c powershell -File scripts\publish_update.ps1 -GitHub sahip/depo -Notes "Neler değişti…"
#       → ayrıca GitHub'da v<sürüm> Release'i açıp kurulum programını yükler (gh CLI ile giriş yapılmış olmalı).
#         ADA'da güncelleme adresi: github:sahip/depo
#
#   -SkipBuild: var olan dist\ADA-Setup.exe kullanılır (yeniden derlenmez).
param([string]$Notes = "", [string]$GitHub = "", [switch]$SkipBuild)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$version = (Select-String -Path ada\__init__.py -Pattern '^__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$pyproject = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if ($version -ne $pyproject) { throw "Sürümler farklı: ada\__init__.py $version, pyproject.toml $pyproject" }

if (-not $SkipBuild) { & (Join-Path $PSScriptRoot "build_installer.ps1") }
$exe = "dist\ADA-Setup.exe"
if (-not (Test-Path $exe)) { throw "$exe yok." }
$built = (Get-Item $exe).VersionInfo.ProductVersion
if ($built -and $built.Trim() -ne $version) { throw "dist\ADA-Setup.exe sürümü $built, beklenen $version. -SkipBuild olmadan yeniden çalıştırın." }

New-Item -ItemType Directory -Force dist\update | Out-Null
Copy-Item $exe dist\update\ADA-Setup.exe -Force
$sha = (Get-FileHash dist\update\ADA-Setup.exe -Algorithm SHA256).Hash.ToLower()
$manifest = [ordered]@{ version = $version; url = "ADA-Setup.exe"; sha256 = $sha; notes = $Notes } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path (Get-Location) "dist\update\latest.json"), $manifest, (New-Object System.Text.UTF8Encoding $false))
Write-Host "Hazır: dist\update\ADA-Setup.exe ve latest.json (sürüm $version)"

if ($GitHub) {
    $notesArg = if ($Notes) { $Notes } else { "ADA $version" }
    gh release create "v$version" "dist\update\ADA-Setup.exe" --repo $GitHub --title "ADA $version" --notes $notesArg
    if ($LASTEXITCODE -ne 0) { throw "GitHub Release oluşturulamadı (kod $LASTEXITCODE)" }
    Write-Host "Yayımlandı: https://github.com/$GitHub/releases/tag/v$version"
    Write-Host "ADA'daki güncelleme adresi: github:$GitHub"
}
