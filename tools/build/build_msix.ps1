# ==============================================================================
# aegishiker - Windows MSIX Package Builder
#
#   powershell -ExecutionPolicy Bypass -File tools\build\build_msix.ps1 [-Clean] [-NoSign]
#
# 1. Builds a clean venv (build\msix\venv) from requirements.txt plus PyInstaller
# 2. Renders the MSIX logos and exe icon from src\assets\appicon.svg
# 3. Freezes the app with packaging\windows\aegishiker.spec (windowed + console launcher)
# 4. Lays out the package with packaging\windows\AppxManifest.xml and packs it with makeappx
# 5. Signs it, then writes dist\AegisHiker_<version>_x64.msix (+ .cer for sideloading)
#
# Signing: by default a self-signed code-signing certificate whose subject is MSIX_PUBLISHER
# (CN=AegisHiker Dev) is created once in Cert:\CurrentUser\My and reused. For a real certificate
# pass -CertificatePath (or MSIX_CERT_PATH) to a .pfx, its password in MSIX_CERT_PASSWORD, set
# MSIX_PUBLISHER to its exact subject, and optionally -TimestampUrl (or MSIX_TIMESTAMP_URL).
# Store submissions are signed by the Store: use -NoSign and the Partner Center identity values.
# ==============================================================================

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NoSign,
    [string]$CertificatePath = $env:MSIX_CERT_PATH,
    [string]$TimestampUrl = $env:MSIX_TIMESTAMP_URL
)

. (Join-Path $PSScriptRoot 'msix_common.ps1')
Set-Location $ProjectRoot

Write-Banner 'MSIX Package Builder: aegishiker'

$appVersion = Get-AppVersion
$msixVersion = ConvertTo-MsixVersion $appVersion
$makeAppx = Find-SdkTool 'makeappx.exe'
$signTool = Find-SdkTool 'signtool.exe'
Write-Info "Version $appVersion (MSIX $msixVersion), identity $MsixIdentityName, publisher '$MsixPublisher'"

if ($Clean -and (Test-Path $MsixBuildDir)) {
    Write-Info 'Removing previous MSIX build directory...'
    Remove-Item $MsixBuildDir -Recurse -Force
}
New-Item -ItemType Directory -Force $MsixBuildDir, $DistDir | Out-Null

# --- 1. Build venv: only what requirements.txt ships, so dev tools never end up in the package
$venvDir = Join-Path $MsixBuildDir 'venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Info "Creating build venv in $venvDir..."
    if ($env:MSIX_PYTHON) {
        Invoke-Native $env:MSIX_PYTHON @('-m', 'venv', $venvDir)
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Native 'py' @('-3', '-m', 'venv', $venvDir)
    } else {
        Invoke-Native 'python' @('-m', 'venv', $venvDir)
    }
}
Write-Info 'Installing requirements and PyInstaller into the build venv...'
Invoke-Native $venvPy @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--upgrade', 'pip')
Invoke-Native $venvPy @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', 'requirements.txt', 'pyinstaller')

# --- 2. Logos and exe icon
$assetsDir = Join-Path $MsixBuildDir 'assets'
$icoPath = Join-Path $MsixBuildDir 'aegishiker.ico'
Write-Info 'Rendering MSIX logos and exe icon...'
Invoke-Native $venvPy @('tools\build\make_msix_assets.py', $assetsDir, $icoPath)

# --- 3. Freeze
$pyiDist = Join-Path $MsixBuildDir 'pyinstaller-dist'
$pyiWork = Join-Path $MsixBuildDir 'pyinstaller-work'
Write-Info 'Freezing the application with PyInstaller...'
$env:AEGISHIKER_ICO = $icoPath
try {
    Invoke-Native $venvPy @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
        '--distpath', $pyiDist, '--workpath', $pyiWork, 'packaging\windows\aegishiker.spec')
} finally {
    Remove-Item Env:\AEGISHIKER_ICO -ErrorAction SilentlyContinue
}
$frozenDir = Join-Path $pyiDist 'AegisHiker'
foreach ($exe in 'AegisHiker.exe', 'aegishiker-cli.exe') {
    if (-not (Test-Path (Join-Path $frozenDir $exe))) { throw "PyInstaller did not produce $exe." }
}
Write-Status 'Application frozen.'

# --- 4. Package layout: frozen app at the root, logos in Assets\, filled-in manifest
$layoutDir = Join-Path $MsixBuildDir 'layout'
if (Test-Path $layoutDir) { Remove-Item $layoutDir -Recurse -Force }
New-Item -ItemType Directory -Force $layoutDir | Out-Null
Copy-Item (Join-Path $frozenDir '*') $layoutDir -Recurse
Copy-Item $assetsDir (Join-Path $layoutDir 'Assets') -Recurse
Get-ChildItem $layoutDir -Directory -Recurse -Filter '__pycache__' | Remove-Item -Recurse -Force

$manifest = Get-Content 'packaging\windows\AppxManifest.xml' -Raw
$manifest = $manifest.Replace('{{IDENTITY_NAME}}', [System.Security.SecurityElement]::Escape($MsixIdentityName))
$manifest = $manifest.Replace('{{PUBLISHER_DISPLAY_NAME}}', [System.Security.SecurityElement]::Escape($MsixPublisherDisplayName))
$manifest = $manifest.Replace('{{PUBLISHER}}', [System.Security.SecurityElement]::Escape($MsixPublisher))
$manifest = $manifest.Replace('{{VERSION}}', $msixVersion)
[System.IO.File]::WriteAllText((Join-Path $layoutDir 'AppxManifest.xml'), $manifest, (New-Object System.Text.UTF8Encoding($false)))

# --- 5. Pack
$msixPath = Join-Path $DistDir "$($MsixIdentityName)_$($msixVersion)_x64.msix"
Write-Info "Packing $msixPath..."
Invoke-Native $makeAppx @('pack', '/d', $layoutDir, '/p', $msixPath, '/o', '/h', 'SHA256') -Quiet

# --- 6. Sign
if ($NoSign) {
    Write-Info 'Skipping signing (-NoSign): the package cannot be sideloaded until it is signed.'
} else {
    $tsArgs = @()
    if ($TimestampUrl) { $tsArgs = @('/tr', $TimestampUrl, '/td', 'SHA256') }

    if ($CertificatePath) {
        $pfx = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertificatePath, $env:MSIX_CERT_PASSWORD)
        if ($pfx.Subject -ne $MsixPublisher) {
            throw "Certificate subject '$($pfx.Subject)' does not match MSIX_PUBLISHER '$MsixPublisher'."
        }
        $cert = $pfx
        $signArgs = @('sign', '/fd', 'SHA256', '/f', $CertificatePath)
        if ($env:MSIX_CERT_PASSWORD) { $signArgs += @('/p', $env:MSIX_CERT_PASSWORD) }
    } else {
        $codeSigning = '1.3.6.1.5.5.7.3.3'
        $cert = Get-ChildItem Cert:\CurrentUser\My |
            Where-Object { $_.Subject -eq $MsixPublisher -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date).AddDays(1) -and
                ($_.EnhancedKeyUsageList | Where-Object { $_.ObjectId -eq $codeSigning }) } |
            Sort-Object NotAfter -Descending | Select-Object -First 1
        if (-not $cert) {
            Write-Info "Creating self-signed development certificate '$MsixPublisher' in Cert:\CurrentUser\My..."
            $cert = New-SelfSignedCertificate -Type Custom -Subject $MsixPublisher -KeyUsage DigitalSignature `
                -FriendlyName 'AegisHiker MSIX development signing' -CertStoreLocation 'Cert:\CurrentUser\My' `
                -TextExtension @("2.5.29.37={text}$codeSigning", '2.5.29.19={text}') -NotAfter (Get-Date).AddYears(3)
        }
        $signArgs = @('sign', '/fd', 'SHA256', '/sha1', $cert.Thumbprint, '/s', 'My')
    }
    Write-Info "Signing with certificate $($cert.Thumbprint)..."
    Invoke-Native $signTool ($signArgs + $tsArgs + @($msixPath))

    # Public certificate next to the package, for trusting it on other test machines
    $cerPath = Join-Path $DistDir "$($MsixIdentityName)_$($msixVersion).cer"
    [System.IO.File]::WriteAllBytes($cerPath, $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    Write-Status "Signed. Public certificate: $cerPath"
}

$sizeMb = [math]::Round((Get-Item $msixPath).Length / 1MB, 1)
Write-Status "MSIX built successfully: $msixPath ($sizeMb MB)"
