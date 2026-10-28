# ==============================================================================
# aegishiker - Automated MSIX Package Verification & Test
#
#   powershell -ExecutionPolicy Bypass -File tools\build\test-msix.ps1 [-MsixPath <file>]
#       [-SkipUnitTests] [-SkipGuiLaunch] [-SelfTestTimeoutSec 300]
#
# 1. Trusts the package's signing certificate (one UAC prompt, the first time only)
# 2. Removes any installed AegisHiker package, installs the newest dist\*.msix, checks its version
# 3. Runs the pytest suite headless against the sources (.venv)
# 4. Smoke-tests the installed package: `aegishiker --version`, `aegishiker --test-mode`
#    (offscreen end-to-end self-test), and a real windowed launch from the Start menu entry
# Leaves the tested package installed.
# ==============================================================================

[CmdletBinding()]
param(
    [string]$MsixPath,
    [switch]$SkipUnitTests,
    [switch]$SkipGuiLaunch,
    [int]$SelfTestTimeoutSec = 300
)

. (Join-Path $PSScriptRoot 'msix_common.ps1')
Set-Location $ProjectRoot

Write-Banner 'MSIX Package Test: aegishiker'

# --- 1. Locate the package
if (-not $MsixPath) { $MsixPath = Get-LatestMsix }
$MsixPath = (Resolve-Path $MsixPath).Path
$expectedVersion = Get-MsixManifestVersion $MsixPath
Write-Status "Testing artifact: $MsixPath (version $expectedVersion)"

# --- 2. Signing certificate must be trusted machine-wide (TrustedPeople) for Add-AppxPackage
$sig = Get-AuthenticodeSignature -FilePath $MsixPath
if (-not $sig.SignerCertificate) { throw 'The package is not signed. Build it without -NoSign.' }
$signer = $sig.SignerCertificate
$trusted = @(Get-ChildItem Cert:\LocalMachine\TrustedPeople, Cert:\LocalMachine\Root |
    Where-Object { $_.Thumbprint -eq $signer.Thumbprint }).Count -gt 0
if (-not $trusted) {
    Write-Info "Signing certificate '$($signer.Subject)' ($($signer.Thumbprint)) is not trusted on this machine."
    Write-Info 'Adding it to Cert:\LocalMachine\TrustedPeople (approve the UAC prompt)...'
    $cerPath = Join-Path $MsixBuildDir 'signer.cer'
    New-Item -ItemType Directory -Force $MsixBuildDir | Out-Null
    [System.IO.File]::WriteAllBytes($cerPath, $signer.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    $importCmd = "Import-Certificate -FilePath '$cerPath' -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null"
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile', '-Command', $importCmd)
    if ($p.ExitCode -ne 0) { throw 'Importing the signing certificate failed or was declined.' }
    Write-Status 'Signing certificate trusted.'
}

# --- 3. Clean reinstall
$existing = Get-AppxPackage -Name $MsixIdentityName
if ($existing) {
    Write-Info "Removing installed $($existing.PackageFullName)..."
    $existing | Remove-AppxPackage
    Write-Status 'Removed existing package.'
}
Write-Info 'Installing package...'
Add-AppxPackage -Path $MsixPath -ForceApplicationShutdown
$pkg = Get-AppxPackage -Name $MsixIdentityName
if (-not $pkg) { throw 'Package is not listed by Get-AppxPackage after installing.' }
if ($pkg.Version -ne $expectedVersion) { throw "Installed version $($pkg.Version), expected $expectedVersion." }
Write-Status "Installed $($pkg.PackageFullName)"

# --- 4. Unit tests (sources, headless)
$prevQpa = $env:QT_QPA_PLATFORM
if ($SkipUnitTests) {
    Write-Info 'Skipping unit tests (-SkipUnitTests).'
} else {
    $devPy = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path $devPy) {
        Write-Info 'Running automated pytest suite...'
        $env:QT_QPA_PLATFORM = 'offscreen'
        try {
            & $devPy -m pytest tests -q --timeout=180 --timeout-method=thread
            if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed!' }
        } finally { $env:QT_QPA_PLATFORM = $prevQpa }
        Write-Status 'Unit tests passed.'
    } else {
        Write-Info "No dev venv at $devPy; skipping unit tests (pip install -r requirements-dev.txt into .venv)."
    }
}

# --- 5. Installed CLI: version and end-to-end self-test through the execution alias
$alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\aegishiker.exe'
if (-not (Test-Path $alias)) { throw "Execution alias $alias was not registered." }

Write-Info 'Smoke test: aegishiker --version'
$versionOut = (& $alias --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "aegishiker --version exited with $LASTEXITCODE." }
if ($versionOut -notmatch '^AegisHiker (\S+)$' -or (ConvertTo-MsixVersion $Matches[1]) -ne $expectedVersion) {
    throw "Unexpected version output: '$versionOut' (package version $expectedVersion)."
}
Write-Status "Installed app reports '$versionOut'."

Write-Info "Smoke test: aegishiker --test-mode (offscreen, timeout $SelfTestTimeoutSec s)"
$outLog = Join-Path $MsixBuildDir 'selftest-stdout.log'
$errLog = Join-Path $MsixBuildDir 'selftest-stderr.log'
$env:QT_QPA_PLATFORM = 'offscreen'
try {
    $proc = Start-Process -FilePath $alias -ArgumentList '--test-mode' -NoNewWindow -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $null = $proc.Handle  # keep the handle so ExitCode is available after exit
    if (-not $proc.WaitForExit($SelfTestTimeoutSec * 1000)) {
        $proc.Kill()
        throw "Self-test did not finish within $SelfTestTimeoutSec s."
    }
} finally { $env:QT_QPA_PLATFORM = $prevQpa }
$stdout = Get-Content $outLog -Raw -ErrorAction SilentlyContinue
$stderr = Get-Content $errLog -Raw -ErrorAction SilentlyContinue
if ($proc.ExitCode -ne 0 -or $stdout -notmatch 'AUTOMATED_TEST_COMPLETE') {
    Write-Fail "Self-test failed (exit code $($proc.ExitCode)). Output:"
    Write-Host $stdout
    Write-Host $stderr
    throw 'Installed package self-test failed.'
}
if ($stderr -match 'Traceback|Fatal Python error') {
    Write-Fail 'Crash signature in self-test stderr:'
    Write-Host $stderr
    throw 'Installed package self-test logged a crash.'
}
Write-Status 'Installed package self-test passed.'

# --- 6. Windowed launch through the Start menu entry: must start and stay up
if ($SkipGuiLaunch) {
    Write-Info 'Skipping windowed launch (-SkipGuiLaunch).'
} else {
    Write-Info 'Smoke test: launching the Start menu entry...'
    $appLog = Join-Path $env:USERPROFILE '.aegishiker\logs\aegishiker.log'
    $faultLog = Join-Path $env:USERPROFILE '.aegishiker\logs\faulthandler.log'
    $logStart = @{}
    foreach ($f in $appLog, $faultLog) { $logStart[$f] = if (Test-Path $f) { (Get-Item $f).Length } else { 0 } }

    $installDir = $pkg.InstallLocation
    Start-Process "shell:AppsFolder\$($pkg.PackageFamilyName)!$MsixAppId"
    $gui = $null
    for ($i = 0; $i -lt 40 -and -not $gui; $i++) {
        Start-Sleep -Milliseconds 500
        $gui = Get-Process -Name 'AegisHiker' -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.StartsWith($installDir, [System.StringComparison]::OrdinalIgnoreCase) } |
            Select-Object -First 1
    }
    if (-not $gui) { throw 'The windowed app did not start.' }
    Start-Sleep -Seconds 8
    $gui.Refresh()
    if ($gui.HasExited) { throw "The windowed app exited early (exit code $($gui.ExitCode))." }
    Write-Status "Windowed app is running (PID $($gui.Id))."
    Stop-Process -Id $gui.Id -Force -ErrorAction SilentlyContinue

    foreach ($f in $appLog, $faultLog) {
        if (-not (Test-Path $f)) { continue }
        $fs = [System.IO.File]::Open($f, 'Open', 'Read', 'ReadWrite')
        try {
            $null = $fs.Seek($logStart[$f], 'Begin')
            $newText = (New-Object System.IO.StreamReader($fs)).ReadToEnd()
        } finally { $fs.Dispose() }
        if ($newText -match 'Traceback|Fatal Python error') {
            Write-Fail "Crash signature in $($f):"
            Write-Host $newText
            throw 'The windowed app logged a crash.'
        }
    }
}

Write-Status 'MSIX package verification successful!'
