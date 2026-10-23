<#
Update this Windows fork of Decky Loader to the latest official version, then test, build and install it.

  powershell -ExecutionPolicy Bypass -File tools\update-decky.ps1            # merge official changes, test, build, install, back up
  powershell -ExecutionPolicy Bypass -File tools\update-decky.ps1 -Rebuild   # no merge needed: just test, build and install this branch
  Options: -NoInstall (stop after building), -NoPush (don't push to the backup remote)

  powershell -ExecutionPolicy Bypass -File tools\update-decky.ps1 -Package -Version 0.1.0
    Release packaging only: builds the frontend, runs the tests, builds the installer folder in a temporary folder and
    zips it (python\, app\ with app\VERSION = the version, LICENSE) into ..\build\release\decky-windows-<version>.zip
    (or -OutDir). Does not merge, install, push, restart anything or touch build\installer\DeckyLoader. -Version must be x.y.z.

Stops (exit 2) when official changes clash with ours, leaving the branch as it was, and (exit 3) when tests or the
build fail. Plugins and settings in C:\ProgramData\decky are never touched; --install keeps them.

The installer folder (build\installer\DeckyLoader) is a private copy of the PSF-signed Python runtime (python\) plus
Decky's source and dependencies (app\), made by tools\build_win_runtime.py. No PyInstaller exe is installed.
#>
param([switch]$Rebuild, [switch]$NoInstall, [switch]$NoPush, [switch]$Package, [string]$Version, [string]$OutDir)
# Not 'Stop': Windows PowerShell 5.1 then treats any stderr line of a native tool (PyInstaller's
# progress output, for one) as a terminating error. Native tools are checked by $LASTEXITCODE and
# the file operations below use -ErrorAction Stop.
$ErrorActionPreference = 'Continue'

$repo     = Split-Path -Parent $PSScriptRoot
$backend  = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$python   = Join-Path $backend '.venv\Scripts\python.exe'
$poetry   = Join-Path $backend '.venv\Scripts\poetry.exe'
$buildDir = Join-Path (Split-Path -Parent $repo) 'build'
$staged   = Join-Path $buildDir 'installer\DeckyLoader'
$branch   = 'windows-platform-fixes'
$upstream = 'origin/main'

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fail($code, $msg) { Write-Host "`nSTOPPED: $msg" -ForegroundColor Red; exit $code }
function Invoke-Git { & git.exe -C $repo @args; if ($LASTEXITCODE -ne 0) { throw "git $args failed ($LASTEXITCODE)" } }

if ($Package) {
    if ($Version -notmatch '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$') { Fail 1 '-Package needs -Version x.y.z (digits only)' }
    $Rebuild = $true; $NoInstall = $true; $NoPush = $true
    if (-not $OutDir) { $OutDir = Join-Path $buildDir 'release' }
}

Step 'Checking the repository'
if ((& git.exe -C $repo branch --show-current) -ne $branch) { Fail 1 "check out '$branch' first" }
if (& git.exe -C $repo status --porcelain --untracked-files=no) { Fail 1 'there are uncommitted changes; commit or stash them first' }

$before = (& git.exe -C $repo rev-parse HEAD)
if (-not $Rebuild) {
    Step 'Fetching the official Decky Loader'
    Invoke-Git fetch -q origin
    $new = [int](& git.exe -C $repo rev-list --count "HEAD..$upstream")
    if ($new -eq 0) { Write-Host 'Already up to date with the official version. Use -Rebuild to rebuild and reinstall anyway.'; exit 0 }
    Write-Host "$new new official commit(s):"
    & git.exe -C $repo log --oneline "HEAD..$upstream" | Select-Object -First 20

    $changed = & git.exe -C $repo diff --name-only HEAD $upstream
    Invoke-Git tag -f "pre-update-$(Get-Date -Format yyyyMMdd-HHmmss)" HEAD
    Step 'Merging official changes into our branch'
    & git.exe -C $repo merge --no-edit $upstream
    if ($LASTEXITCODE -ne 0) {
        $conflicts = & git.exe -C $repo diff --name-only --diff-filter=U
        & git.exe -C $repo merge --abort
        Fail 2 ("official changes clash with ours in:`n  " + ($conflicts -join "`n  ") + "`nNothing was changed. Ask Claude to resolve the merge.")
    }

    if ($changed -match '^backend/(pyproject\.toml|poetry\.lock)$') {
        Step 'Backend dependencies changed: installing'
        Push-Location $backend; & $poetry install --no-root; $ok = $LASTEXITCODE -eq 0; Pop-Location
        if (-not $ok) { Fail 3 'poetry install failed' }
    }
    if ($changed -match '^frontend/(package\.json|pnpm-lock\.yaml)$') {
        Step 'Frontend dependencies changed: installing'
        Push-Location $frontend; & npx --yes pnpm@9 install --frozen-lockfile; $ok = $LASTEXITCODE -eq 0; Pop-Location
        if (-not $ok) { Fail 3 'pnpm install failed' }
    }
}

Step 'Building the frontend'
Push-Location $frontend
& (Join-Path $frontend 'node_modules\.bin\rollup.cmd') -c
$ok = $LASTEXITCODE -eq 0
Pop-Location
if (-not $ok) { Fail 3 'frontend build failed' }

Step 'Running the backend tests'
$tmp = Join-Path $env:TEMP "decky-update-pytest-$PID"
Push-Location $backend
& $python -m pytest -q -p no:cacheprovider "--basetemp=$tmp" tests
$ok = $LASTEXITCODE -eq 0
Pop-Location
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
if (-not $ok) { Fail 3 "tests failed. To undo the merge: git -C `"$repo`" reset --hard $before" }

Step 'Building the installer folder (signed Python runtime + source)'
$work = Join-Path $env:TEMP "decky-update-build-$PID"
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
# Checks the runtime's PSF signature and version, copies it and the dependency closure, precompiles, smoke-tests
& $python (Join-Path $repo 'tools\build_win_runtime.py') --out $work
if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue; Fail 3 'building the installer folder failed (see above)' }
& (Join-Path $work 'python\python.exe') -I (Join-Path $work 'app\main.py') --check-imports linecache netrc pdb keyword reprlib sqlite3 psutil PIL pystray watchdog aiohttp certifi winreg
if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue; Fail 3 'the installer folder is missing modules (--check-imports)' }

if ($Package) {
    Step 'Zipping the installer folder'
    try {
        # app\VERSION is what the dashboard reads as the installed Decky version (strict x.y.z)
        [System.IO.File]::WriteAllText((Join-Path $work 'app\VERSION'), $Version, (New-Object System.Text.UTF8Encoding($false)))
        Copy-Item -ErrorAction Stop (Join-Path $repo 'LICENSE') (Join-Path $work 'LICENSE')
        # Not needed at run time: source maps (only browser dev tools read them) and certifi's own test folder
        Get-ChildItem -LiteralPath (Join-Path $work 'app\decky_loader\static') -Filter *.map -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction Stop
        $certTests = Join-Path $work 'app\certifi\tests'
        if (Test-Path $certTests) { Remove-Item -Recurse -Force $certTests -ErrorAction Stop }
        New-Item -ItemType Directory -Force $OutDir -ErrorAction Stop | Out-Null
        $zipPath = Join-Path $OutDir "decky-windows-$Version.zip"
        if (Test-Path $zipPath) { Remove-Item -Force $zipPath -ErrorAction Stop }
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $fs = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew)
        $za = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            $rootLen = (Resolve-Path $work).Path.TrimEnd('\').Length + 1
            # Forward-slash entry names (Compress-Archive in Windows PowerShell 5.1 writes backslashes, which the dashboard refuses)
            foreach ($f in (Get-ChildItem -LiteralPath $work -Recurse -File | Sort-Object FullName)) {
                $name = $f.FullName.Substring($rootLen).Replace('\', '/')
                # Fixed entry time so the same input gives the same zip
                $ze = $za.CreateEntry($name, [System.IO.Compression.CompressionLevel]::Optimal)
                $ze.LastWriteTime = New-Object System.DateTimeOffset(2026, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
                $zo = $ze.Open()
                try { $zi = [System.IO.File]::OpenRead($f.FullName); try { $zi.CopyTo($zo) } finally { $zi.Dispose() } } finally { $zo.Dispose() }
            }
        } finally { $za.Dispose(); $fs.Dispose() }
    } catch { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue; Fail 3 "could not zip the installer folder: $_" }
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    $item = Get-Item $zipPath
    Write-Host "Package: $zipPath ($([math]::Round($item.Length / 1MB, 1)) MB)"
    Write-Host 'Done (package only: nothing was installed or pushed).' -ForegroundColor Green
    exit 0
}

try {
    New-Item -ItemType Directory -Force (Split-Path $staged) -ErrorAction Stop | Out-Null
    if (Test-Path "$staged.prev") { Remove-Item -Recurse -Force "$staged.prev" -ErrorAction Stop }
    if (Test-Path $staged) { Move-Item $staged "$staged.prev" -ErrorAction Stop }
    Copy-Item -Recurse $work $staged -ErrorAction Stop
} catch { Fail 3 "could not stage the installer: $_" }
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Write-Host "Installer staged at $staged (previous one kept as DeckyLoader.prev)"

if (-not $NoInstall) {
    Step 'Installing (admin prompt)'
    # Start-Process joins -ArgumentList with spaces, so the script path (it may contain spaces) is quoted here.
    # -I: the runtime ignores PYTHON* environment variables (main.py refuses to run without it).
    $main = Join-Path $staged 'app\main.py'
    $started = Get-Date
    $p = Start-Process (Join-Path $staged 'python\python.exe') -ArgumentList '-I', "`"$main`"", '--install' -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        # The elevated install ran in its own console, which closed; it logged why it failed
        $logs = @(Join-Path $env:ProgramData 'decky\logs\install.log') +
                @(Get-ChildItem (Join-Path $env:TEMP 'decky-install-*.log') -ErrorAction SilentlyContinue |
                  Where-Object { $_.LastWriteTime -ge $started } | ForEach-Object FullName)
        foreach ($log in $logs) {
            if ((Test-Path $log) -and (Get-Item $log).LastWriteTime -ge $started) {
                Write-Host "`n-- $log (last 40 lines):" -ForegroundColor Yellow
                Get-Content $log -Tail 40 | Write-Host
            }
        }
        Fail 3 "install exited with $($p.ExitCode)"
    }
    Write-Host 'Installed. Decky restarted with the new version.'
}

if (-not $NoPush -and (& git.exe -C $repo remote) -contains 'backup') {
    Step 'Backing up to GitHub'
    & git.exe -C $repo push -q backup $branch
    if ($LASTEXITCODE -ne 0) { Write-Host 'Push to the backup remote failed (the update itself is done).' -ForegroundColor Yellow }
}

Write-Host "`nDone: $(& git.exe -C $repo log -1 --format='%h %s')" -ForegroundColor Green
