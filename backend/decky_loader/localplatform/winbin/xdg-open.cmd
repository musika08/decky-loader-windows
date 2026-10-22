@echo off
rem Decky plugin compat: Linux xdg-open opens a file or URL with its default program
if "%~1"=="" exit /b 1
start "" "%~1"
