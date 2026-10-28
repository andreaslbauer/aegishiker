# ==============================================================================
# aegishiker - Full Build -> Test -> Deploy Pipeline (Windows MSIX)
#
#   powershell -ExecutionPolicy Bypass -File tools\build\msix_package_test_deploy.ps1
#       [-Clean] [-SkipUnitTests] [-SkipGuiLaunch] [-DeployCommand "<powershell>"] [-NoVersionBump]
#
# Builds (build_msix.ps1), tests (test-msix.ps1), and only if both succeed deploys the package,
# then bumps the patch version in version.txt.
# Default deploy: install the tested .msix on this machine and verify the installed version.
# -DeployCommand (or DEPLOY_COMMAND) runs instead, with $env:MSIX_PATH and $env:MSIX_CER_PATH set,
# e.g. -DeployCommand 'Copy-Item $env:MSIX_PATH, $env:MSIX_CER_PATH \\nas\releases\aegishiker\'
# ==============================================================================

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipUnitTests,
    [switch]$SkipGuiLaunch,
    [switch]$NoVersionBump,
    [string]$DeployCommand = $env:DEPLOY_COMMAND
)

. (Join-Path $PSScriptRoot 'msix_common.ps1')
Set-Location $ProjectRoot

Write-Banner 'Orchestrating MSIX Build, Test, Deploy'

# Step 1: Build
Write-Info 'Step 1: Building MSIX package...'
& (Join-Path $PSScriptRoot 'build_msix.ps1') -Clean:$Clean
$msixPath = Join-Path $DistDir "$($MsixIdentityName)_$(ConvertTo-MsixVersion (Get-AppVersion))_x64.msix"
if (-not (Test-Path $msixPath)) { throw "Build did not produce $msixPath." }

# Step 2: Test
Write-Info 'Step 2: Testing MSIX package...'
& (Join-Path $PSScriptRoot 'test-msix.ps1') -MsixPath $msixPath -SkipUnitTests:$SkipUnitTests -SkipGuiLaunch:$SkipGuiLaunch

# Step 3: Deploy (only reached if build and tests succeeded)
Write-Info 'Step 3: Deploying MSIX package...'
$expectedVersion = Get-MsixManifestVersion $msixPath
if ($DeployCommand) {
    $env:MSIX_PATH = $msixPath
    $env:MSIX_CER_PATH = Join-Path $DistDir "$($MsixIdentityName)_$($expectedVersion).cer"
    Invoke-Expression $DeployCommand
    if (-not $?) { throw 'Deploy command failed.' }
    Write-Status "Deployed $msixPath with custom command."
} else {
    Add-AppxPackage -Path $msixPath -ForceApplicationShutdown -ForceUpdateFromAnyVersion
    $pkg = Get-AppxPackage -Name $MsixIdentityName
    if (-not $pkg -or $pkg.Version -ne $expectedVersion) {
        throw "Installed version '$($pkg.Version)' does not match package version $expectedVersion."
    }
    Write-Status "Installed $($pkg.PackageFullName) on this machine."
}

# Step 4: Bump patch version
if (-not $NoVersionBump) {
    Write-Info 'Step 4: Incrementing patch version in version.txt...'
    $parts = (Get-AppVersion).Split('.')
    $parts[-1] = [string]([int]$parts[-1] + 1)
    $newVersion = $parts -join '.'
    [System.IO.File]::WriteAllText((Join-Path $ProjectRoot 'version.txt'), "$newVersion`n")
    Write-Status "Bumped version to $newVersion"
}

Write-Banner 'MSIX BUILD, TEST, AND DEPLOY SUCCESSFUL!'
