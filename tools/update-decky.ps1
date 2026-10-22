<#
Update this Windows fork of Decky Loader to the latest official version, then test, build and install it.

  powershell -ExecutionPolicy Bypass -File tools\update-decky.ps1            # merge official changes, test, build, install, back up
  powershell -ExecutionPolicy Bypass -File tools\update-decky.ps1 -Rebuild   # no merge needed: just test, build and install this branch
  Options: -NoInstall (stop after building), -NoPush (don't push to the backup remote)

Stops (exit 2) when official changes clash with ours, leaving the branch as it was, and (exit 3) when tests or the
build fail. Plugins and settings in C:\ProgramData\decky are never touched; --install keeps them.
#>
param([switch]$Rebuild, [switch]$NoInstall, [switch]$NoPush)
$ErrorActionPreference = 'Stop'

$repo     = Split-Path -Parent $PSScriptRoot
$backend  = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$python   = Join-Path $backend '.venv\Scripts\python.exe'
$poetry   = Join-Path $backend '.venv\Scripts\poetry.exe'
$buildDir = Join-Path (Split-Path -Parent $repo) 'build'
$staged   = Join-Path $buildDir 'installer\PluginLoader'
$branch   = 'windows-platform-fixes'
$upstream = 'origin/main'

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fail($code, $msg) { Write-Host "`nSTOPPED: $msg" -ForegroundColor Red; exit $code }
function Invoke-Git { & git.exe -C $repo @args; if ($LASTEXITCODE -ne 0) { throw "git $args failed ($LASTEXITCODE)" } }

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

Step 'Building the installer (onedir)'
$work = Join-Path $env:TEMP "decky-update-build-$PID"
$env:DECKY_ONEDIR = '1'; $env:DECKY_NOCONSOLE = '1'
Push-Location $backend
& $python -m PyInstaller pyinstaller.spec --noconfirm --distpath "$work\dist" --workpath "$work\work" *> "$work.log"
$ok = $LASTEXITCODE -eq 0
Pop-Location
Remove-Item Env:DECKY_ONEDIR, Env:DECKY_NOCONSOLE
if (-not $ok) { Fail 3 "PyInstaller failed, see $work.log" }
$exe = Join-Path $work 'dist\PluginLoader\PluginLoader.exe'
& $exe --check-imports linecache netrc pdb keyword reprlib sqlite3 psutil PIL pystray watchdog aiohttp certifi winreg
if ($LASTEXITCODE -ne 0) { Fail 3 'the built exe is missing modules (--check-imports)' }

New-Item -ItemType Directory -Force (Split-Path $staged) | Out-Null
if (Test-Path "$staged.prev") { Remove-Item -Recurse -Force "$staged.prev" }
if (Test-Path $staged) { Move-Item $staged "$staged.prev" }
Copy-Item -Recurse (Join-Path $work 'dist\PluginLoader') $staged
Remove-Item -Recurse -Force $work, "$work.log" -ErrorAction SilentlyContinue
Write-Host "Installer staged at $staged (previous one kept as PluginLoader.prev)"

if (-not $NoInstall) {
    Step 'Installing (admin prompt)'
    $p = Start-Process (Join-Path $staged 'PluginLoader.exe') -ArgumentList '--install' -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail 3 "install exited with $($p.ExitCode)" }
    Write-Host 'Installed. Decky restarted with the new version.'
}

if (-not $NoPush -and (& git.exe -C $repo remote) -contains 'backup') {
    Step 'Backing up to GitHub'
    & git.exe -C $repo push -q backup $branch
    if ($LASTEXITCODE -ne 0) { Write-Host 'Push to the backup remote failed (the update itself is done).' -ForegroundColor Yellow }
}

Write-Host "`nDone: $(& git.exe -C $repo log -1 --format='%h %s')" -ForegroundColor Green
