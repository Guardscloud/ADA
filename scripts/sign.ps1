# Bir dosyayı ADA'nın kod imzalama sertifikasıyla imzalar (Authenticode, SHA-256, zaman damgalı).
# Kullanım: powershell -File scripts\sign.ps1 <dosya>
#           powershell -File scripts\sign.ps1 -Create     (kendinden imzalı sertifikayı bir kez oluşturur)
#
# Sertifika: ADA_SIGN_THUMBPRINT ortam değişkeni verilmişse o (ör. satın alınmış bir sertifika), yoksa
# Cert:\CurrentUser\My altındaki "CN=Duck Team" kod imzalama sertifikası. Kendinden imzalı sertifika
# yalnızca onu güvenilir olarak ekleyen bilgisayarlarda "geçerli" görünür (bkz. ADA-Windows-Kurulum.md).
param([string]$File, [switch]$Create)
$ErrorActionPreference = "Stop"
$subject = "CN=Duck Team"

function Get-AdaCert {
    if ($env:ADA_SIGN_THUMBPRINT) { return Get-Item "Cert:\CurrentUser\My\$($env:ADA_SIGN_THUMBPRINT)" }
    Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -eq $subject -and $_.NotAfter -gt (Get-Date) } |
        Sort-Object NotAfter -Descending | Select-Object -First 1
}

if ($Create) {
    $cert = Get-AdaCert
    if (-not $cert) {
        $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $subject -FriendlyName "ADA kod imzalama (Duck Team)" `
            -CertStoreLocation Cert:\CurrentUser\My -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
            -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(5)
    }
    Write-Host "Sertifika: $($cert.Subject)  parmak izi $($cert.Thumbprint)  bitiş $($cert.NotAfter.ToString('yyyy-MM-dd'))"
    return
}

if (-not $File) { throw "İmzalanacak dosya verilmedi." }
$cert = Get-AdaCert
if (-not $cert) { throw "İmza sertifikası yok. Önce: powershell -File scripts\sign.ps1 -Create" }

# Zaman damgası sunucusuna ulaşılamazsa sıradaki denenir; hiçbiri olmazsa damgasız imzalanır
$sig = $null
foreach ($ts in "http://timestamp.digicert.com", "http://timestamp.sectigo.com", "") {
    try {
        $args2 = @{ FilePath = $File; Certificate = $cert; HashAlgorithm = "SHA256" }
        if ($ts) { $args2.TimestampServer = $ts }
        $sig = Set-AuthenticodeSignature @args2
        if ($sig.SignerCertificate) { break }
    } catch { $sig = $null }
}
if (-not $sig -or -not $sig.SignerCertificate) { throw "İmzalanamadı: $File" }
Write-Host "İmzalandı: $File"
