param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$BackupDirectory = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
function Get-BackupSha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try { return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}
$backupRoot = (Resolve-Path -LiteralPath $BackupDirectory).Path
$index = Get-Content -LiteralPath (Join-Path $backupRoot 'BACKUP_INDEX.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $destinationPath) { throw 'Destination must be a new directory. Existing data will not be overwritten.' }
$archivePath = Join-Path $backupRoot $index.archive
if (Test-Path -LiteralPath $archivePath) { throw 'Combined ZIP already exists. Extract it to a new directory, or use a fresh backup download folder.' }
foreach ($part in $index.parts) {
    if ([System.IO.Path]::GetFileName($part.name) -ne $part.name) { throw 'Invalid part name.' }
    $partPath = Join-Path $backupRoot $part.name
    if ((Get-Item -LiteralPath $partPath).Length -ne $part.size) { throw "Part size mismatch: $($part.name)" }
    if ((Get-BackupSha256 $partPath) -ne $part.sha256) { throw "Part checksum mismatch: $($part.name)" }
}
if ([System.IO.Path]::GetFileName($index.archive) -ne $index.archive) { throw 'Invalid archive name.' }
$joined = [System.IO.File]::Open($archivePath, [System.IO.FileMode]::CreateNew)
try {
    foreach ($part in $index.parts) {
        $source = [System.IO.File]::OpenRead((Join-Path $backupRoot $part.name))
        try { $source.CopyTo($joined) } finally { $source.Dispose() }
    }
} finally { $joined.Dispose() }
if ((Get-BackupSha256 $archivePath) -ne $index.archive_sha256) { throw 'Combined ZIP checksum mismatch.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $destinationPath)
$manifest = Get-Content -LiteralPath (Join-Path $destinationPath 'FULL_BACKUP_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    $file = [System.IO.Path]::GetFullPath((Join-Path $destinationPath $entry.path))
    if (-not $file.StartsWith($destinationPath.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid manifest path.' }
    if ((Get-Item -LiteralPath $file).Length -ne $entry.size -or (Get-BackupSha256 $file) -ne $entry.sha256) { throw "Restored file verification failed: $($entry.path)" }
}
Write-Host "Restored and verified $($manifest.files.Count) files in $destinationPath"
Write-Host 'The saved Python environment and shortcuts may contain old absolute paths; use release\Ramp Studio.exe or rebuild .venv on the new computer.'
