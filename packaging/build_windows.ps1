[CmdletBinding()]
param(
    [switch] $SkipInstaller,
    [string] $IsccPath = ""
)

$ErrorActionPreference = "Stop"

$PackagingDirectory = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PackagingDirectory "..")).Path
$PyInstallerPath = Join-Path $ProjectRoot ".venv-packaging\Scripts\pyinstaller.exe"
$SpecPath = Join-Path $PackagingDirectory "Moltage.spec"
$InstallerScript = Join-Path $PackagingDirectory "Moltage.iss"
$BuildRoot = Join-Path $ProjectRoot "build\windows"
$ApplicationDistRoot = Join-Path $ProjectRoot "dist\windows"
$InstallerDistRoot = Join-Path $ProjectRoot "dist\installer"
$ProjectLicensePath = Join-Path $ProjectRoot "LICENSE"
$ThirdPartyNoticesPath = Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md"
$LicenseDirectory = Join-Path $ProjectRoot "LICENSES"

function Remove-OwnedBuildDirectory {
    param([Parameter(Mandatory = $true)][string] $Path)

    $resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
    $resolvedTarget = [System.IO.Path]::GetFullPath($Path)
    $projectPrefix = $resolvedProjectRoot
    if (-not $projectPrefix.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $projectPrefix += [System.IO.Path]::DirectorySeparatorChar
    }
    if (-not $resolvedTarget.StartsWith(
        $projectPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove a build target outside the project root: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $PyInstallerPath -PathType Leaf)) {
    throw "PyInstaller is missing from .venv-packaging. Install packaging/requirements-build.txt first."
}

foreach ($RequiredLegalPath in @(
    $ProjectLicensePath,
    $ThirdPartyNoticesPath,
    $LicenseDirectory
)) {
    if (-not (Test-Path -LiteralPath $RequiredLegalPath)) {
        throw "Required release legal material is missing: $RequiredLegalPath"
    }
}

Remove-OwnedBuildDirectory -Path $BuildRoot
Remove-OwnedBuildDirectory -Path $ApplicationDistRoot
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ApplicationDistRoot -Force | Out-Null

$PyInstallerArguments = @(
    "--noconfirm",
    "--distpath", $ApplicationDistRoot,
    "--workpath", $BuildRoot,
    $SpecPath
)
$OriginalBuildPath = $env:PATH
try {
    # Do not collect same-named DLLs from unrelated tools on the caller's PATH.
    $env:PATH = @(
        (Split-Path -Parent $PyInstallerPath),
        (Join-Path $env:SystemRoot "System32"),
        $env:SystemRoot
    ) -join [System.IO.Path]::PathSeparator
    & $PyInstallerPath @PyInstallerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    $env:PATH = $OriginalBuildPath
}

$ApplicationPath = Join-Path $ApplicationDistRoot "Moltage\Moltage.exe"
if (-not (Test-Path -LiteralPath $ApplicationPath -PathType Leaf)) {
    throw "PyInstaller did not create the expected application: $ApplicationPath"
}
Write-Output "APPLICATION=$ApplicationPath"

if ($SkipInstaller) {
    return
}

if ($IsccPath) {
    $ResolvedIsccPath = [System.IO.Path]::GetFullPath($IsccPath)
} else {
    $IsccCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    $ResolvedIsccPath = $IsccCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}
if (-not $ResolvedIsccPath -or
    -not (Test-Path -LiteralPath $ResolvedIsccPath -PathType Leaf)) {
    throw "Inno Setup 6 compiler ISCC.exe was not found."
}

New-Item -ItemType Directory -Path $InstallerDistRoot -Force | Out-Null

$InstallerPath = Join-Path $InstallerDistRoot "Moltage-Setup-0.2.1.exe"
if (Test-Path -LiteralPath $InstallerPath) {
    throw "Refusing to overwrite an existing release artifact: $InstallerPath"
}
& $ResolvedIsccPath /Qp $InstallerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
    throw "Inno Setup did not create the expected installer: $InstallerPath"
}
$InstallerItem = Get-Item -LiteralPath $InstallerPath
$InstallerHash = Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256
$ChecksumPath = "$InstallerPath.sha256"
Set-Content -LiteralPath $ChecksumPath -Encoding ascii -Value (
    "$($InstallerHash.Hash.ToLowerInvariant())  $($InstallerItem.Name)"
)

Write-Output "INSTALLER=$InstallerPath"
Write-Output "SHA256=$($InstallerHash.Hash.ToLowerInvariant())"
