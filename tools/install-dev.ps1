# Dev install of Traction into the local Decky (run elevated). Copies the plugin files into
# C:\ProgramData\decky\plugins\traction and restarts Decky's scheduled task.
$ErrorActionPreference = 'Stop'
$out = Join-Path $env:USERPROFILE '.steam-home-app\admin-result.txt'
$lines = New-Object System.Collections.Generic.List[string]
$stopped = $false
try {
    $src = Split-Path -Parent $PSScriptRoot
    $dst = Join-Path $env:ProgramData 'decky\plugins\traction'
    $files = 'plugin.json', 'main.py', 'package.json', 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md'
    $dirs = 'dist', 'py_modules', 'bin'
    # Check every source first: nothing is stopped or deleted if the build is incomplete.
    foreach ($f in $files) { if (-not (Test-Path -LiteralPath (Join-Path $src $f) -PathType Leaf)) { throw "missing source file: $f" } }
    foreach ($d in $dirs) { if (-not (Test-Path -LiteralPath (Join-Path $src $d) -PathType Container)) { throw "missing source folder: $d" } }
    if (-not (Test-Path -LiteralPath (Join-Path $src 'bin\traction-helper.exe') -PathType Leaf)) { throw 'missing source file: bin\traction-helper.exe' }
    # Stop Decky first: the running helper locks its own exe in the bin folder.
    Stop-ScheduledTask -TaskName 'Decky Loader'; $stopped = $true; Start-Sleep -Seconds 4
    Get-Process traction-helper -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dst + '\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force
    Start-Sleep -Seconds 1
    if (Test-Path -LiteralPath $dst) {
        # Delete only the exact plugin folder, and never through a link.
        $expected = [System.IO.Path]::GetFullPath((Join-Path $env:ProgramData 'decky\plugins\traction'))
        $actual = [System.IO.Path]::GetFullPath($dst)
        if ($actual -ine $expected) { throw "refusing to delete unexpected path: $actual" }
        if ((Get-Item -LiteralPath $dst -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "refusing to delete: $dst is a link" }
        $links = @(Get-ChildItem -LiteralPath $dst -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)
        if ($links.Count -gt 0) { throw "refusing to delete: link inside plugin folder: $($links[0].FullName)" }
        Remove-Item -LiteralPath $dst -Recurse -Force
    }
    New-Item -ItemType Directory $dst | Out-Null
    foreach ($f in $files) { Copy-Item (Join-Path $src $f) $dst }
    foreach ($d in $dirs) { Copy-Item (Join-Path $src $d) $dst -Recurse }
    Get-ChildItem $dst -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    $lines.Add('files: ' + (Get-ChildItem $dst -Recurse -File).Count)
    $lines.Add('OK')
} catch { $lines.Add("ERROR: $_") }
finally {
    if ($stopped) {
        try { Start-ScheduledTask -TaskName 'Decky Loader'; Start-Sleep -Seconds 15; $lines.Add('task: ' + (Get-ScheduledTask -TaskName 'Decky Loader').State) }
        catch { $lines.Add("ERROR restarting Decky: $_") }
    }
    $lines | Set-Content -Encoding UTF8 $out
}
