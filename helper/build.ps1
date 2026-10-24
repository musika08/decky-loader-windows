<#
 Build script for the Traction helper (traction-helper.exe).
   build.ps1         -> <plugin>\bin\traction-helper.exe  (/target:exe /platform:x64 /optimize)
   build.ps1 -Tests  -> also helper\out\HelperTests.exe, then runs it (its exit code = number of failed tests).
 Uses the in-box .NET Framework 4.x csc.exe, so C# 5 only. JSON via System.Web.Extensions (JavaScriptSerializer).
 Exit code is non-zero on any failure.
#>
param([switch]$Tests)

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path   # helper\
$Plugin = Split-Path -Parent $Root
$Csc    = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$Bin    = Join-Path $Plugin 'bin'
$Out    = Join-Path $Root 'out'

function Fail($msg) { Write-Host "BUILD FAILED: $msg" -ForegroundColor Red; exit 1 }

function Get-Sources($dir) {
    if (-not (Test-Path $dir)) { return @() }
    return @(Get-ChildItem -Path $dir -Filter *.cs -Recurse -File | Sort-Object FullName | ForEach-Object { $_.FullName })
}

function Invoke-Csc([string[]]$cscArgs) {
    & $Csc @cscArgs
    if ($LASTEXITCODE -ne 0) { Fail "csc exited with code $LASTEXITCODE" }
}

if (-not (Test-Path $Csc)) { Fail "csc.exe not found at $Csc" }
New-Item -ItemType Directory -Force $Bin | Out-Null
New-Item -ItemType Directory -Force $Out | Out-Null

$Refs = @('System.Core.dll', 'System.Web.Extensions.dll', 'System.ServiceProcess.dll') | ForEach-Object { "/r:$_" }
$Src  = Get-Sources (Join-Path $Root 'src')
if ($Src.Count -eq 0) { Fail 'No sources under helper\src' }

$exe = Join-Path $Bin 'traction-helper.exe'
Write-Host "Compiling $exe"
Invoke-Csc (@('/nologo', '/target:exe', '/platform:x64', '/optimize', '/warnaserror+', "/out:$exe") + $Refs + $Src)

if ($Tests) {
    $testExe = Join-Path $Out 'HelperTests.exe'
    $testSrc = $Src + @(Get-Sources (Join-Path $Root 'tests'))
    Write-Host "Compiling $testExe"
    # The helper's own Main is excluded with /main so the test runner is the entry point.
    Invoke-Csc (@('/nologo', '/target:exe', '/platform:x64', '/debug:pdbonly', '/main:Traction.Helper.Tests.TestRunner', "/out:$testExe") + $Refs + $testSrc)
    & $testExe
    $failures = $LASTEXITCODE
    if ($failures -ne 0) { Fail "$failures helper test(s) failed" }
}
Write-Host 'BUILD OK' -ForegroundColor Green
exit 0
