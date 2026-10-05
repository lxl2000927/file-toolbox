# Local fallback is allowed only when no existing installation or shortcut can
# be replaced. All application data uses a new profile and the test uninstalls.
param([switch] $LocalIsolated)

$ErrorActionPreference = 'Stop'
if (!$LocalIsolated -and ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows')) {
    throw 'This installer acceptance script requires an isolated GitHub Windows runner.'
}
if ($LocalIsolated) {
    $registryRoots = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')
    foreach ($registryRoot in $registryRoots) {
        $existing = Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue |
            Get-ItemProperty -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'File.?Toolbox|文件处理工具箱' }
        if ($existing) { throw 'Refusing local acceptance: an existing installation is registered.' }
    }
    foreach ($folder in @('Desktop', 'CommonDesktopDirectory', 'StartMenu', 'CommonStartMenu')) {
        $shortcutRoot = [Environment]::GetFolderPath($folder)
        if ($shortcutRoot -and (Get-ChildItem -LiteralPath $shortcutRoot -Filter '*Toolbox*' -Recurse -ErrorAction SilentlyContinue)) {
            throw 'Refusing local acceptance: an existing shortcut could be replaced.'
        }
    }
    if (Get-Process -Name 'File Toolbox' -ErrorAction SilentlyContinue) { throw 'An application instance is running.' }
}
foreach ($releaseVersion in @($env:RELEASE_TAG, $env:BASE_TAG)) {
    if ($releaseVersion -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Invalid release tag' }
}
$evidenceDir = Join-Path $PSScriptRoot '../acceptance-samples/installer-upgrade'
$testRoot = if ($LocalIsolated) { Join-Path ([System.IO.Path]::GetFullPath($evidenceDir)) ([guid]::NewGuid().ToString('N')) } else { $env:RUNNER_TEMP }
$baseDir = Join-Path $testRoot 'FileToolboxBasePackage'
$candidateDir = Join-Path $testRoot 'FileToolboxCandidatePackage'
$installDir = Join-Path $testRoot 'FileToolboxUpgradeTest'
$profileDir = Join-Path $testRoot 'FileToolboxUpgradeProfile'
New-Item -ItemType Directory -Force -Path $evidenceDir, $baseDir, $candidateDir, $profileDir | Out-Null
$cachedReleases = if ($LocalIsolated) {
    $metadata = & gh api "repos/$env:GITHUB_REPOSITORY/releases?per_page=100"
    if ($LASTEXITCODE -ne 0) { throw 'Release metadata verification failed' }
    $metadata | ConvertFrom-Json
} else { @() }
function Get-Installer([string] $tag, [string] $packageDirectory) {
    $assetName = "File.Toolbox-$($tag.Substring(1))-x64-setup.exe"
    $localAsset = Join-Path $PSScriptRoot "../electron-app/release/$assetName"
    $remoteAsset = $cachedReleases | Where-Object tag_name -eq $tag | ForEach-Object assets | Where-Object name -eq $assetName
    if ($LocalIsolated -and $remoteAsset -and (Test-Path -LiteralPath $localAsset)) {
        $digest = 'sha256:' + (Get-FileHash -LiteralPath $localAsset -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($digest -eq $remoteAsset.digest -and (Get-Item -LiteralPath $localAsset).Length -eq $remoteAsset.size) {
            Copy-Item -LiteralPath $localAsset -Destination (Join-Path $packageDirectory $assetName)
            return
        }
    }
    & gh release download $tag --repo $env:GITHUB_REPOSITORY --pattern '*-setup.exe' --dir $packageDirectory
    if ($LASTEXITCODE -ne 0) { throw "Installer download failed: $tag" }
}
Get-Installer $env:BASE_TAG $baseDir
Get-Installer $env:RELEASE_TAG $candidateDir

function Install-Toolbox([string] $packageDirectory) {
    $packages = @(Get-ChildItem -LiteralPath $packageDirectory -Filter '*-setup.exe')
    if ($packages.Count -ne 1) { throw 'Expected exactly one installer' }
    $installer = Start-Process -FilePath $packages[0].FullName -ArgumentList @('/S', '/currentuser', "/D=$installDir") -WindowStyle Hidden -Wait -PassThru
    if ($installer.ExitCode -ne 0) { throw "Installer failed: $($installer.ExitCode)" }
    if (!(Test-Path -LiteralPath (Join-Path $installDir '.file-toolbox-installed'))) { throw 'Installed distribution marker missing' }
    if (!(Test-Path -LiteralPath (Join-Path $installDir 'File Toolbox.exe'))) { throw 'Installed executable missing' }
}

try {
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
    localIsolated = [bool]$LocalIsolated; publicUpdateDownload = ($env:VERIFY_UPDATE_CHANNEL -eq 'true') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'result.json')
} finally {
    if ($LocalIsolated) {
        # Only the unique test installation created above may be uninstalled.
        $resolvedInstall = [System.IO.Path]::GetFullPath($installDir)
        $resolvedRoot = [System.IO.Path]::GetFullPath($testRoot) + [System.IO.Path]::DirectorySeparatorChar
        if (!$resolvedInstall.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe cleanup target' }
        $uninstaller = Join-Path $resolvedInstall 'Uninstall File Toolbox.exe'
        if (Test-Path -LiteralPath $uninstaller) {
            $cleanup = Start-Process -FilePath $uninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
            if ($cleanup.ExitCode -ne 0) { throw "Test uninstall failed: $($cleanup.ExitCode)" }
            for ($attempt = 0; $attempt -lt 120 -and (Test-Path -LiteralPath (Join-Path $resolvedInstall 'File Toolbox.exe')); $attempt++) {
                Start-Sleep -Milliseconds 250
            }
            if (Test-Path -LiteralPath (Join-Path $resolvedInstall 'File Toolbox.exe')) { throw 'Test executable remains after uninstall' }
            @{ uninstalled = $true; testInstall = $resolvedInstall; profile = $profileDir } |
                ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'cleanup.json')
        }
    }
}
