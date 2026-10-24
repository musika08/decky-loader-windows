# Builds overdrive-<version>.zip with the plugin layout Decky installs (a top-level overdrive/ folder, the same files
# as the 0.2.0 release zip) and prints its size and SHA-256. Entries use forward slashes (Windows PowerShell's
# Compress-Archive writes backslashes, which other unzip tools can misread).
# Run npm run build first. bin\asus-helper.exe is taken as it is: a C# build is never byte-identical, so ship the
# helper that was tested on the device (helper\build.ps1 rebuilds it).
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$root = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $root 'package.json') -Raw | ConvertFrom-Json).version
foreach ($need in 'dist\index.js', 'bin\asus-helper.exe') {
    if (-not (Test-Path (Join-Path $root $need))) { throw "$need is missing" }
}

$files = New-Object System.Collections.Generic.List[string]
foreach ($f in 'plugin.json', 'main.py', 'package.json', 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md',
               'dist\index.js', 'dist\index.js.map', 'bin\asus-helper.exe') { $files.Add($f) }
Get-ChildItem (Join-Path $root 'py_modules') -Recurse -File -Filter *.py |
    Where-Object { $_.FullName -notmatch '\\__pycache__\\' } |
    ForEach-Object { $files.Add($_.FullName.Substring($root.Length + 1)) }

$zip = Join-Path $root "overdrive-$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
$archive = [System.IO.Compression.ZipFile]::Open($zip, 'Create')
try {
    foreach ($rel in $files) {
        $entry = 'overdrive/' + ($rel -replace '\\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, (Join-Path $root $rel), $entry, 'Optimal') | Out-Null
    }
} finally {
    $archive.Dispose()
}
'{0}: {1} files, {2:N2} MB' -f (Split-Path -Leaf $zip), $files.Count, ((Get-Item $zip).Length / 1MB)
'sha256: ' + (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
