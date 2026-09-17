[CmdletBinding()]
param(
    [switch]$Full,

    [string[]]$Tests
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($Full -and $Tests) {
    throw "Use either -Full or -Tests, not both."
}
if (-not $Full -and -not $Tests) {
    throw (
        "Focused validation requires explicit test paths. " +
        "Example: .\tools\run_tests.ps1 -Tests " +
        "tests\unit\test_orbital_cube.py"
    )
}

$previousPythonPath = $env:PYTHONPATH
$previousQtOpenGl = $env:QT_OPENGL
$sourcePath = Join-Path $repositoryRoot "src"
$testSupportPath = Join-Path $repositoryRoot "tests\unit"
$env:PYTHONPATH = $sourcePath + [IO.Path]::PathSeparator + $testSupportPath
$env:QT_OPENGL = "software"
[string[]]$targets = if ($Full) { @("tests\unit") } else { $Tests }
$exitCode = 1

Push-Location $repositoryRoot
try {
    & py -m compileall -q src tools
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
    }
    else {
        & py -m pytest @targets -q
        $exitCode = $LASTEXITCODE
    }
}
finally {
    Pop-Location
    $env:PYTHONPATH = $previousPythonPath
    $env:QT_OPENGL = $previousQtOpenGl
}

exit $exitCode
