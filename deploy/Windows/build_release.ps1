[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WebAppPath,

    [Parameter(Mandatory = $true)]
    [string]$PortablePythonPath,

    [string]$OutputRoot,

    [string]$ReleaseName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $projectRoot "build\release"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)

$webAppSource = (Resolve-Path -LiteralPath $WebAppPath).Path
if (-not (Test-Path -LiteralPath $webAppSource -PathType Container)) {
    throw "WebApp source is not a directory: $webAppSource"
}

$webAppExecutable = Join-Path $webAppSource "Alasio.exe"
$webAppAsar = Join-Path $webAppSource "resources\app.asar"
foreach ($requiredWebAppFile in @($webAppExecutable, $webAppAsar)) {
    if (-not (Test-Path -LiteralPath $requiredWebAppFile -PathType Leaf)) {
        throw "WebApp build is incomplete, missing: $requiredWebAppFile"
    }
}

$portablePythonSource = (Resolve-Path -LiteralPath $PortablePythonPath).Path
if (-not (Test-Path -LiteralPath $portablePythonSource -PathType Container)) {
    throw "Portable Python source is not a directory: $portablePythonSource"
}
$portablePythonExecutable = Join-Path $portablePythonSource "python.exe"
if (-not (Test-Path -LiteralPath $portablePythonExecutable -PathType Leaf)) {
    throw "Portable Python source is incomplete, missing: $portablePythonExecutable"
}
if (Test-Path -LiteralPath (Join-Path $portablePythonSource "WebApp")) {
    throw "PortablePythonPath must not contain WebApp; pass a Python runtime directory only."
}

$pythonInfoJson = (& $portablePythonExecutable -c "import json, platform, sys; print(json.dumps({'implementation': platform.python_implementation(), 'major': sys.version_info.major, 'minor': sys.version_info.minor, 'patch': sys.version_info.micro, 'architecture': platform.architecture()[0]}))" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $pythonInfoJson) {
    throw "Portable Python could not be inspected: $portablePythonExecutable"
}
$pythonInfo = $pythonInfoJson | ConvertFrom-Json
if (
    $pythonInfo.implementation -ne "CPython" -or
    $pythonInfo.major -ne 3 -or
    $pythonInfo.minor -ne 10 -or
    $pythonInfo.architecture -ne "64bit"
) {
    throw "Portable Python must be 64-bit CPython 3.10.x, found: $pythonInfoJson"
}
& $portablePythonExecutable -c "import bz2, ctypes, ensurepip, lzma, multiprocessing, sqlite3, ssl, venv"
if ($LASTEXITCODE -ne 0) {
    throw "Portable Python is missing required standard-library components."
}
$pythonVersion = "$($pythonInfo.major).$($pythonInfo.minor).$($pythonInfo.patch)"

& git -C $projectRoot diff --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Tracked working tree changes must be committed before assembling a release."
}
& git -C $projectRoot diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Staged changes must be committed before assembling a release."
}

$projectCommit = (& git -C $projectRoot rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $projectCommit) {
    throw "Unable to read the AutoEpicSeven commit."
}
$shortCommit = $projectCommit.Substring(0, 8)
if (-not $ReleaseName) {
    $ReleaseName = "AutoEpicSeven-$shortCommit"
}

$releasePath = Join-Path $OutputRoot $ReleaseName
$sourceArchivePath = Join-Path $OutputRoot "$ReleaseName-source.zip"
$requirementsPath = Join-Path $OutputRoot "$ReleaseName-requirements.txt"
foreach ($newPath in @($releasePath, $sourceArchivePath, $requirementsPath)) {
    if (Test-Path -LiteralPath $newPath) {
        throw "Output already exists; choose another ReleaseName: $newPath"
    }
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

Write-Host "Exporting tracked AutoEpicSeven files from $shortCommit"
& git -C $projectRoot archive --format=zip "--output=$sourceArchivePath" HEAD
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed. Partial outputs were kept in: $OutputRoot"
}
Expand-Archive -LiteralPath $sourceArchivePath -DestinationPath $releasePath

foreach ($privateRelativePath in @("config\deploy.yaml", "config\aes.db", "config\gui.db")) {
    $privatePath = Join-Path $releasePath $privateRelativePath
    if (Test-Path -LiteralPath $privatePath) {
        throw "Private runtime data entered the release unexpectedly: $privateRelativePath"
    }
}

$toolkitPath = Join-Path $releasePath "toolkit"
Write-Host "Copying portable CPython $pythonVersion"
Copy-Item -LiteralPath $portablePythonSource -Destination $toolkitPath -Recurse

$releasePython = Join-Path $toolkitPath "python.exe"
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    throw "The copied Python runtime is incomplete: $releasePython"
}

$releaseUv = Join-Path $toolkitPath "uv.exe"
if (-not (Test-Path -LiteralPath $releaseUv -PathType Leaf)) {
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction Stop
    Copy-Item -LiteralPath $uvCommand.Source -Destination $releaseUv
}

Write-Host "Exporting dependencies from uv.lock"
& $releaseUv --quiet export --project $projectRoot --frozen --no-dev --no-emit-project --format requirements.txt --output-file $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "uv export failed. uv.lock was not changed."
}

Write-Host "Installing the locked dependencies into the release runtime"
& $releaseUv pip sync --python $releasePython --strict --link-mode copy $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "uv pip sync failed. Partial outputs were kept in: $OutputRoot"
}

$webAppDestination = Join-Path $toolkitPath "WebApp"
Write-Host "Copying the Alasio desktop build"
Copy-Item -LiteralPath $webAppSource -Destination $webAppDestination -Recurse

$manifestPath = Join-Path $releasePath "release-manifest.json"
[ordered]@{
    SchemaVersion = 1
    ProjectCommit = $projectCommit
    PythonVersion = $pythonVersion
    Architecture = $pythonInfo.architecture
    WebAppSHA256 = (Get-FileHash -LiteralPath (Join-Path $webAppDestination "Alasio.exe") -Algorithm SHA256).Hash
} | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

$requiredReleaseFiles = @(
    "gui.py",
    "release-manifest.json",
    "config\deploy.template.yaml",
    "config\deploy.template-cn.yaml",
    "toolkit\python.exe",
    "toolkit\uv.exe",
    "toolkit\Lib\site-packages\adbutils\binaries\adb.exe",
    "toolkit\WebApp\Alasio.exe",
    "toolkit\WebApp\resources\app.asar"
)
foreach ($relativePath in $requiredReleaseFiles) {
    $fullPath = Join-Path $releasePath $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Release validation failed, missing: $relativePath"
    }
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = ""
Push-Location $releasePath
try {
    & $releasePython -c "import adbutils, alasio, gui, uiautomator2; from alasio.backend.backend import BackendWithSupervisor; print('Runtime imports passed')"
    if ($LASTEXITCODE -ne 0) {
        throw "Release Python import validation failed."
    }
}
finally {
    Pop-Location
    $env:PYTHONPATH = $previousPythonPath
}

$manifest = [ordered]@{
    schema_version = 1
    project = "AutoEpicSeven"
    project_commit = $projectCommit
    python_distribution = "portable"
    python_version = $pythonVersion
    python_executable_sha256 = (Get-FileHash -LiteralPath $releasePython -Algorithm SHA256).Hash.ToLowerInvariant()
    uv_executable_sha256 = (Get-FileHash -LiteralPath $releaseUv -Algorithm SHA256).Hash.ToLowerInvariant()
    source_archive_sha256 = (Get-FileHash -LiteralPath $sourceArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    requirements_sha256 = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    uv_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $releasePath "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
    webapp_executable_sha256 = (Get-FileHash -LiteralPath (Join-Path $webAppDestination "Alasio.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
    webapp_asar_sha256 = (Get-FileHash -LiteralPath (Join-Path $webAppDestination "resources\app.asar") -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest |
    ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $releasePath "release-manifest.json") -Encoding utf8

Write-Host "Release assembled: $releasePath"
Write-Host "Source archive kept: $sourceArchivePath"
Write-Host "Locked requirements kept: $requirementsPath"
