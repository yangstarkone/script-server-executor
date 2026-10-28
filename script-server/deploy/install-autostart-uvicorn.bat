@echo off
rem ==========================================================================
rem  Register the script-server autostart scheduled task (no external daemon).
rem
rem  Double-click this file: it re-launches elevated (UAC) and runs
rem  deploy\register-autostart-task.ps1. Full output: C:\ScriptServer\logs\register-task.log
rem
rem  Uninstall (elevated PowerShell):
rem    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register-autostart-task.ps1" -Uninstall
rem
rem  Keep this file ASCII-only (cmd parses .bat in the OEM code page).
rem ==========================================================================
setlocal EnableExtensions
set "PS1=%~dp0register-autostart-task.ps1"

if not exist "%PS1%" (
    echo [FAIL] not found: %PS1%
    if /i not "%~1"=="-nopause" pause
    exit /b 1
)

echo [1/2] Requesting administrator rights (UAC) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%PS1%') -Wait"
if errorlevel 1 (
    echo [FAIL] elevation was refused, or the register script failed.
    echo        See C:\ScriptServer\logs\register-task.log
    if /i not "%~1"=="-nopause" pause
    exit /b 1
)

echo [2/2] Done. Full log: C:\ScriptServer\logs\register-task.log
if /i not "%~1"=="-nopause" pause
exit /b 0
