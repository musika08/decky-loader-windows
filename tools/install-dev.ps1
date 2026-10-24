# Dev install of Traction into the local Decky (run elevated). Copies the plugin files into
# C:\ProgramData\decky\plugins\traction and restarts Decky's scheduled task.
$ErrorActionPreference = 'Stop'
$out = Join-Path $env:USERPROFILE '.steam-home-app\admin-result.txt'
$lines = New-Object System.Collections.Generic.List[string]
try {
    $src = Split-Path -Parent $PSScriptRoot
    $dst = Join-Path $env:ProgramData 'decky\plugins\traction'
    # Stop Decky first: the running helper locks its own exe in the bin folder.
    Stop-ScheduledTask -TaskName 'Decky Loader'; Start-Sleep -Seconds 4
    Get-Process traction-helper -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dst*" } | Stop-Process -Force
    Start-Sleep -Seconds 1
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    New-Item -ItemType Directory $dst | Out-Null
    foreach ($f in 'plugin.json', 'main.py', 'package.json', 'README.md', 'LICENSE', 'THIRD-PARTY-NOTICES.md') { Copy-Item (Join-Path $src $f) $dst }
    foreach ($d in 'dist', 'py_modules', 'bin') { Copy-Item (Join-Path $src $d) $dst -Recurse }
    Get-ChildItem $dst -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    $lines.Add('files: ' + (Get-ChildItem $dst -Recurse -File).Count)
    Start-ScheduledTask -TaskName 'Decky Loader'; Start-Sleep -Seconds 15
    $lines.Add('task: ' + (Get-ScheduledTask -TaskName 'Decky Loader').State)
    $lines.Add('OK')
} catch { $lines.Add("ERROR: $_") }
$lines | Set-Content -Encoding UTF8 $out
