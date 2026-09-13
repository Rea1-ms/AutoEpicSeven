[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallPath,

    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-DirectoryTreeHash([string]$Path) {
    $root = (Resolve-Path -LiteralPath $Path).Path.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $files = @(Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName)
    if ($files.Count -eq 0) {
        throw "Cannot hash an empty directory: $root"
    }

    $entries = foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($root.Length + 1).Replace('\', '/')
        $fileHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$relativePath`t$fileHash`n"
    }
    $payload = [System.Text.Encoding]::UTF8.GetBytes(($entries -join ""))
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($payload))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Resolve-ReleaseDirectory([string]$Path, [string]$Name) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "$Name is not a directory: $resolved"
    }
    return $resolved.TrimEnd([System.IO.Path]::DirectorySeparatorChar)
}

function Test-PathContains([string]$Parent, [string]$Child) {
    $prefix = $Parent + [System.IO.Path]::DirectorySeparatorChar
    return $Child.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ProtectedFileState([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{ Exists = $false }
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Protected user-data path is not a file: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        Exists = $true
        SHA256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        Sddl = (Get-Acl -LiteralPath $Path).Sddl
        Attributes = [int]$item.Attributes
    }
}

$installRoot = Resolve-ReleaseDirectory $InstallPath "InstallPath"
$packageRoot = Resolve-ReleaseDirectory $PackagePath "PackagePath"
$installDriveRoot = [System.IO.Path]::GetPathRoot($installRoot).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar
)

if ($installRoot.Equals($installDriveRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "InstallPath must not be a drive root: $installRoot"
}
if (
    $installRoot.Equals($packageRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    (Test-PathContains $installRoot $packageRoot) -or
    (Test-PathContains $packageRoot $installRoot)
) {
    throw "InstallPath and PackagePath must be separate, non-nested directories."
}

$requiredPackageFiles = @(
    "gui.py",
    "release-manifest.json",
    "toolkit\python.exe",
    "toolkit\Lib\site-packages\frontend\build\index.html",
    "toolkit\WebApp\Alasio.exe",
    "toolkit\WebApp\resources\app.asar"
)
foreach ($relativePath in $requiredPackageFiles) {
    $fullPath = Join-Path $packageRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Release package is incomplete, missing: $relativePath"
    }
}

$manifestPath = Join-Path $packageRoot "release-manifest.json"
try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
}
catch {
    throw "Release manifest is invalid: $manifestPath"
}
if (
    $manifest.schema_version -ne 2 -or
    $manifest.project -ne "AutoEpicSeven" -or
    $manifest.python_distribution -ne "portable"
) {
    throw "Release manifest does not describe a supported AutoEpicSeven package."
}

foreach ($commitField in @("project_commit", "alasio_commit")) {
    $commitProperty = $manifest.PSObject.Properties[$commitField]
    if (
        $null -eq $commitProperty -or
        [string]$commitProperty.Value -notmatch "^[0-9a-fA-F]{40}$"
    ) {
        throw "Release manifest has an invalid source commit: $commitField"
    }
}

$manifestFiles = [ordered]@{
    python_executable_sha256 = "toolkit\python.exe"
    uv_executable_sha256 = "toolkit\uv.exe"
    uv_lock_sha256 = "uv.lock"
    webapp_executable_sha256 = "toolkit\WebApp\Alasio.exe"
    webapp_asar_sha256 = "toolkit\WebApp\resources\app.asar"
}
foreach ($entry in $manifestFiles.GetEnumerator()) {
    $manifestProperty = $manifest.PSObject.Properties[$entry.Key]
    if ($null -eq $manifestProperty -or [string]::IsNullOrWhiteSpace($manifestProperty.Value)) {
        throw "Release manifest is missing hash: $($entry.Key)"
    }
    $filePath = Join-Path $packageRoot $entry.Value
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        throw "Release package is incomplete, missing: $($entry.Value)"
    }
    $actualHash = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash
    if (-not $actualHash.Equals($manifestProperty.Value, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release package hash mismatch: $($entry.Value)"
    }
}

$frontendTreeProperty = $manifest.PSObject.Properties["frontend_tree_sha256"]
if ($null -eq $frontendTreeProperty -or [string]::IsNullOrWhiteSpace($frontendTreeProperty.Value)) {
    throw "Release manifest is missing hash: frontend_tree_sha256"
}
$packageFrontend = Join-Path $packageRoot "toolkit\Lib\site-packages\frontend\build"
$frontendTreeHash = Get-DirectoryTreeHash $packageFrontend
if (-not $frontendTreeHash.Equals($frontendTreeProperty.Value, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release package hash mismatch: toolkit\Lib\site-packages\frontend\build"
}

$protectedRelativePaths = @(
    "config\deploy.yaml",
    "config\aes.db",
    "config\gui.db"
)
$protectedState = @{}
foreach ($relativePath in $protectedRelativePaths) {
    $packageFile = Join-Path $packageRoot $relativePath
    if (Test-Path -LiteralPath $packageFile) {
        throw "Release package contains protected user data: $relativePath"
    }
    $installFile = Join-Path $installRoot $relativePath
    $protectedState[$relativePath] = Get-ProtectedFileState $installFile
}

Write-Host "Updating program files in $installRoot"
& robocopy.exe $packageRoot $installRoot /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ `
    /XF deploy.yaml aes.db gui.db /NFL /NDL /NJH /NJS /NP
$robocopyExitCode = $LASTEXITCODE
if ($robocopyExitCode -ge 8) {
    throw "Release update failed; robocopy exit code: $robocopyExitCode"
}

foreach ($relativePath in $protectedRelativePaths) {
    $before = $protectedState[$relativePath]
    $after = Get-ProtectedFileState (Join-Path $installRoot $relativePath)
    if ($before.Exists -ne $after.Exists) {
        throw "Protected user data changed existence during update: $relativePath"
    }
    if ($before.Exists -and (
        $before.SHA256 -ne $after.SHA256 -or
        $before.Sddl -ne $after.Sddl -or
        $before.Attributes -ne $after.Attributes
    )) {
        throw "Protected user data changed during update: $relativePath"
    }
}

Write-Host "Release updated; protected user data is unchanged."
