# ==============================================================================
# aegishiker - shared settings and helpers for the MSIX build/test/deploy scripts
# (dot-sourced by build_msix.ps1, test-msix.ps1 and msix_package_test_deploy.ps1)
#
# Package identity overrides (defaults are fine for local sideloading):
#   MSIX_IDENTITY_NAME            package name           (default: AegisHiker)
#   MSIX_PUBLISHER                publisher subject DN   (default: CN=AegisHiker Dev); must equal
#                                 the signing certificate's subject
#   MSIX_PUBLISHER_DISPLAY_NAME   shown in Settings > Apps (default: Aegis)
# ==============================================================================

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$DistDir = Join-Path $ProjectRoot 'dist'
$MsixBuildDir = Join-Path $ProjectRoot 'build\msix'

$MsixIdentityName = if ($env:MSIX_IDENTITY_NAME) { $env:MSIX_IDENTITY_NAME } else { 'AegisHiker' }
$MsixPublisher = if ($env:MSIX_PUBLISHER) { $env:MSIX_PUBLISHER } else { 'CN=AegisHiker Dev' }
$MsixPublisherDisplayName = if ($env:MSIX_PUBLISHER_DISPLAY_NAME) { $env:MSIX_PUBLISHER_DISPLAY_NAME } else { 'Aegis' }
$MsixAppId = 'AegisHiker'   # <Application Id> in packaging/windows/AppxManifest.xml

function Write-Status([string]$Message) { Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Info([string]$Message) { Write-Host "[i] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "[X] $Message" -ForegroundColor Red }
function Write-Banner([string]$Message) {
    Write-Host '========================================' -ForegroundColor Cyan
    Write-Host $Message -ForegroundColor Cyan
    Write-Host '========================================' -ForegroundColor Cyan
}

function Get-AppVersion {
    (Get-Content (Join-Path $ProjectRoot 'version.txt') -Raw).Trim()
}

# version.txt "0.1.7" -> MSIX "0.1.7.0" (four parts, each 0..65535)
function ConvertTo-MsixVersion([string]$Version) {
    $parts = @($Version.Trim().Split('.'))
    if ($parts.Count -gt 4) { throw "Version '$Version' has more than four parts." }
    while ($parts.Count -lt 4) { $parts += '0' }
    foreach ($p in $parts) {
        $n = 0
        if (-not [int]::TryParse($p, [ref]$n) -or $n -lt 0 -or $n -gt 65535) {
            throw "Version '$Version' is not a valid MSIX version (four numbers 0..65535)."
        }
    }
    $parts -join '.'
}

# makeappx.exe / signtool.exe from the newest installed Windows 10/11 SDK (or MSIX_SDK_BIN)
function Find-SdkTool([string]$Name) {
    if ($env:MSIX_SDK_BIN) {
        $p = Join-Path $env:MSIX_SDK_BIN $Name
        if (Test-Path $p) { return $p }
        throw "$Name not found in MSIX_SDK_BIN ($env:MSIX_SDK_BIN)."
    }
    $onPath = Get-Command $Name -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $found = Get-ChildItem -Path $kits -Filter $Name -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -eq 'x64' -and $_.Directory.Parent.Name -match '^10\.' } |
        Sort-Object { [version]$_.Directory.Parent.Name } -Descending |
        Select-Object -First 1
    if (-not $found) {
        throw "$Name not found. Install the Windows SDK (https://developer.microsoft.com/windows/downloads/windows-sdk/) or set MSIX_SDK_BIN."
    }
    $found.FullName
}

# Run a native program and fail on a non-zero exit code; -Quiet shows its output only on failure
function Invoke-Native([string]$Exe, [string[]]$ArgumentList, [switch]$Quiet) {
    if ($Quiet) {
        $out = & $Exe @ArgumentList | Out-String
        if ($LASTEXITCODE -ne 0) { Write-Host $out }
    } else {
        & $Exe @ArgumentList
    }
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path $Exe -Leaf) failed with exit code $LASTEXITCODE." }
}

function Get-LatestMsix {
    $msix = Get-ChildItem -Path $DistDir -Filter "$($MsixIdentityName)_*.msix" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $msix) { throw "No $($MsixIdentityName)_*.msix found in $DistDir. Run tools\build\build_msix.ps1 first." }
    $msix.FullName
}

# Identity Version from the AppxManifest.xml inside a .msix (it is a zip)
function Get-MsixManifestVersion([string]$MsixPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($MsixPath)
    try {
        $entry = $zip.GetEntry('AppxManifest.xml')
        $reader = New-Object System.IO.StreamReader($entry.Open())
        try { [xml]$manifest = $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally { $zip.Dispose() }
    $manifest.Package.Identity.Version
}
