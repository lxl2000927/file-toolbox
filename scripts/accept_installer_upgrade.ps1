# Run only on an isolated Windows CI machine: this performs real NSIS installs.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'This installer acceptance script requires an isolated GitHub Windows runner.'
}
foreach ($releaseVersion in @($env:RELEASE_TAG, $env:BASE_TAG)) {
    if ($releaseVersion -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Invalid release tag' }
}
$evidenceDir = Join-Path $PSScriptRoot '../acceptance-samples/installer-upgrade'
$baseDir = Join-Path $env:RUNNER_TEMP 'FileToolboxBasePackage'
$candidateDir = Join-Path $env:RUNNER_TEMP 'FileToolboxCandidatePackage'
$installDir = Join-Path $env:RUNNER_TEMP 'FileToolboxUpgradeTest'
$profileDir = Join-Path $env:RUNNER_TEMP 'FileToolboxUpgradeProfile'
New-Item -ItemType Directory -Force -Path $evidenceDir, $baseDir, $candidateDir, $profileDir | Out-Null
& gh release download $env:BASE_TAG --repo $env:GITHUB_REPOSITORY --pattern '*-setup.exe' --dir $baseDir
if ($LASTEXITCODE -ne 0) { throw 'Previous installer download failed' }
& gh release download $env:RELEASE_TAG --repo $env:GITHUB_REPOSITORY --pattern '*-setup.exe' --dir $candidateDir
if ($LASTEXITCODE -ne 0) { throw 'Candidate installer download failed' }

function Install-Toolbox([string] $packageDirectory) {
    $packages = @(Get-ChildItem -LiteralPath $packageDirectory -Filter '*-setup.exe')
    if ($packages.Count -ne 1) { throw 'Expected exactly one installer' }
    $installer = Start-Process -FilePath $packages[0].FullName -ArgumentList @('/S', '/currentuser', "/D=$installDir") -WindowStyle Hidden -Wait -PassThru
    if ($installer.ExitCode -ne 0) { throw "Installer failed: $($installer.ExitCode)" }
    if (!(Test-Path -LiteralPath (Join-Path $installDir '.file-toolbox-installed'))) { throw 'Installed distribution marker missing' }
    if (!(Test-Path -LiteralPath (Join-Path $installDir 'File Toolbox.exe'))) { throw 'Installed executable missing' }
}

Install-Toolbox $baseDir
$executable = Join-Path $installDir 'File Toolbox.exe'
$baseVersion = $env:BASE_TAG.Substring(1)
$candidateVersion = $env:RELEASE_TAG.Substring(1)
$baseArguments = @('--exe', $executable, '--type', 'installer', '--version', $baseVersion,
    '--legacy-navigation', '--profile', $profileDir, '--set-collapsed', '--report', (Join-Path $evidenceDir 'base.json'))
if ($env:VERIFY_UPDATE_CHANNEL -eq 'true') {
    $baseArguments += @('--update-version', $candidateVersion, '--download-update')
}
& node (Join-Path $PSScriptRoot 'smoke_packaged_app.cjs') @baseArguments
if ($LASTEXITCODE -ne 0) { throw 'Previous installed application acceptance failed' }

# The same profile is used across both real installations. The smoke test also
# verifies the real persisted navigation setting, not just this sentinel file.
$sentinel = Join-Path $profileDir 'upgrade-retains-data.txt'
Set-Content -LiteralPath $sentinel -Value 'retained across installer upgrade' -NoNewline
Install-Toolbox $candidateDir
& node (Join-Path $PSScriptRoot 'smoke_packaged_app.cjs') --exe $executable --type installer --version $candidateVersion --profile $profileDir --expect-collapsed --report (Join-Path $evidenceDir 'upgraded.json')
if ($LASTEXITCODE -ne 0) { throw 'Upgraded installed application acceptance failed' }
if ((Get-Content -LiteralPath $sentinel -Raw) -ne 'retained across installer upgrade') { throw 'User profile was modified by the installer' }
@{ passed = $true; base = $baseVersion; candidate = $candidateVersion;
    freshInstall = $true; realInstallerUpgrade = $true; retainedPreferences = $true;
    publicUpdateDownload = ($env:VERIFY_UPDATE_CHANNEL -eq 'true') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'result.json')
