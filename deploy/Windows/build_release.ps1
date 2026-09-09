[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WebAppPath,

    [string]$OutputRoot,

    [string]$ReleaseName,

    [string]$PythonVersion = "3.10.19"
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

Write-Host "Locating uv-managed CPython $PythonVersion"
& uv python install $PythonVersion
if ($LASTEXITCODE -ne 0) {
    throw "uv could not install CPython $PythonVersion. Partial outputs were kept in: $OutputRoot"
}

$pythonListJson = (& uv python list --only-installed --managed-python --output-format json $PythonVersion | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "uv could not list managed Python installations."
}
$pythonInstall = @($pythonListJson | ConvertFrom-Json) |
    Where-Object {
        $_.version -eq $PythonVersion -and
        $_.implementation -eq "cpython" -and
        $_.os -eq "windows" -and
        $_.arch -eq "x86_64" -and
        $_.path
    } |
    Select-Object -First 1
if (-not $pythonInstall) {
    throw "No uv-managed 64-bit Windows CPython $PythonVersion installation was found."
}

$pythonHome = Split-Path -Parent $pythonInstall.path
$toolkitPath = Join-Path $releasePath "toolkit"
Write-Host "Copying the managed Python runtime"
Copy-Item -LiteralPath $pythonHome -Destination $toolkitPath -Recurse

$releasePython = Join-Path $toolkitPath "python.exe"
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    throw "The copied Python runtime is incomplete: $releasePython"
}

Write-Host "Exporting dependencies from uv.lock"
& uv export --project $projectRoot --frozen --no-dev --no-emit-project --format requirements.txt --output-file $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "uv export failed. uv.lock was not changed."
}

Write-Host "Installing the locked dependencies into the release runtime"
& uv pip sync --python $releasePython --strict --link-mode copy $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "uv pip sync failed. Partial outputs were kept in: $OutputRoot"
}

$webAppDestination = Join-Path $toolkitPath "WebApp"
Write-Host "Copying the Alasio desktop build"
Copy-Item -LiteralPath $webAppSource -Destination $webAppDestination -Recurse

$requiredReleaseFiles = @(
    "gui.py",
    "config\deploy.template.yaml",
    "config\deploy.template-cn.yaml",
    "toolkit\python.exe",
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
    project = "AutoEpicSeven"
    project_commit = $projectCommit
    python_version = $PythonVersion
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
