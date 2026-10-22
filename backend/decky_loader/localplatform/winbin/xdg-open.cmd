@echo off
rem Decky plugin compat: Linux xdg-open opens a file or URL with its default program.
rem Arguments with cmd syntax characters are refused so they can't run other commands.
setlocal EnableExtensions DisableDelayedExpansion
if "%~1"=="" (
  echo xdg-open: missing file or URL 1>&2
  exit /b 1
)
rem cmd splits an unquoted argument at "=", as in an unquoted ?a=b URL; join the pieces back
set "arg=%~1"
:join
shift
if "%~1"=="" goto joined
set "arg=%arg%=%~1"
goto join
:joined
setlocal EnableDelayedExpansion
rem Linux callers often wrap the URL in single quotes, which cmd keeps
if "!arg:~0,1!!arg:~-1!"=="''" set "arg=!arg:~1,-1!"
set "t=!arg:"=!"
set "t=!t:%%=!"
set "t=!t:^=!"
set "t=!t:&=!"
set "t=!t:|=!"
set "t=!t:<=!"
set "t=!t:>=!"
if not "!t!"=="!arg!" (
  echo xdg-open: refusing an argument with cmd syntax characters 1>&2
  exit /b 1
)
start "" "!arg!"
